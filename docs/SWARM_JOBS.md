# Guarded jobs for agent swarms (`swarm_jobs` + `review-job`)

**What it is.** A minimal onchain job escrow in which an agent's reward is released only after BUMM Guard's
review of its work passed. A poster locks a reward in a job; an agent that joined the swarm submits the sha256 of
the wasm it built and of the scenario report that proves it; the reviewer runs `bumm-guard review-job`, which reads
the job, checks that the files are exactly the submitted ones and runs the deploy gate on them; the gate's verdict
becomes one printed command, `approve` (the reward goes to the agent) or `reject` (the agent may resubmit). The
reviewer signs that command with their own key; BUMM Guard never signs.

**Why.** Motivated by the "swarm for hire, with guardrails" idea raised for this hackathon: jobs bid onchain,
agents in a swarm funded with NEAR for inference, paid for the jobs they take. This is the guardrail part of that
idea, for agents whose work is contract code.

It is the first, deliberately small step of a larger Swarm-for-Hire effort, not a swarm.

## Flow

```
 poster                    agent (in the swarm)               reviewer (runs BUMM Guard)
   |                              |                                     |
   |                              |-- join_swarm(profile) + budget ---->|  (budget tracked, no inference here)
   |-- post_job(desc, target) + reward ------------------------------->| job: Open
   |                              |                                     |
   |                              |  builds wasm, runs `bumm-guard simulate` (CI)
   |                              |-- submit(job, wasm_sha256, report_sha256) --> job: Submitted
   |                              |                                     |
   |                              |          bumm-guard review-job (read-only):
   |                              |            get_job -> Submitted?  sha256(--wasm) == wasm_sha256?
   |                              |            sha256(--simulate-report) == report_sha256?
   |                              |            gate: scan, report bound to the wasm, view_code(target):
   |                              |                  code there -> upgrade proof for (onchain -> wasm),
   |                              |                  migration method protected
   |                              |            writes .bumm-guard/review-<job>.json, prints ONE command
   |                              |                                     |
   |                              |      gate passed: approve(job, wasm_sha256, sha256(review report))
   |                              |<----- reward (transfer) ------------|  job: Paying -> Paid
   |                              |      gate blocked: reject(job, reason)  job: Rejected -> agent resubmits
   |                              |                                     |
   |-- cancel(job) while Open or Rejected: reward back to the poster       job: Cancelled
```

## Contract (`tests/fixtures/near/swarm_jobs`, near-sdk 5.17.2)

| method | who | what |
|---|---|---|
| `new(reviewer)` | once, `#[init]` | sets the reviewer account |
| `join_swarm(profile)` payable | anyone except the reviewer | registers the caller as an agent (or updates the profile); the deposit is added to its budget. The first join needs at least 0.1 NEAR (`MIN_AGENT_DEPOSIT`); top-ups may be any amount |
| `withdraw_budget(amount)` | the agent | sends `amount` (yoctoNEAR string) of its own budget back; while the agent has a job in `Submitted` or `Paying` the budget cannot drop below 0.1 NEAR |
| `post_job(description, target_account?)` payable | anyone | reward = attached deposit (> 0); status `Open`; returns the id |
| `submit(job_id, wasm_sha256, report_sha256)` | a registered agent | allowed while `Open` or `Rejected`; both hashes 64 lowercase hex; status `Submitted` |
| `approve(job_id, wasm_sha256, gate_report_sha256)` | the reviewer | needs `Submitted` and `wasm_sha256` equal to the submitted one; records the report hash; `Paying`, transfers the reward, then the `#[private]` callback `on_reward_paid` sets `Paid`, or on a failed transfer `Submitted` again with the reward still in the contract |
| `reject(job_id, reason)` | the reviewer | needs `Submitted`; stores the reason (≤ 500 bytes); `Rejected` |
| `cancel(job_id)` | the poster | only while `Open` or `Rejected`; refunds the reward; `Cancelled` |
| views | anyone | `get_job(id)`, `list_jobs(from_index?, limit?)` (≤ 100), `job_count()`, `get_agent(account)`, `agent_count()`, `get_reviewer()` |

`get_job` returns `{id, poster, description, target_account, reward, status, agent, wasm_sha256, report_sha256,
gate_report_sha256, reject_reason}`; `status` is one of `Open`, `Submitted`, `Rejected`, `Paying`, `Paid`,
`Cancelled`. `get_agent` returns `{account, profile, budget, jobs_paid, jobs_in_review}` (`jobs_in_review` counts the
agent's jobs in `Submitted` or `Paying`).

Checks of our own rules on the contract (dogfooding): `python3 -m bumm_guard scan tests/fixtures/near/swarm_jobs` →
`0 finding(s) in 1 file(s)`; the callback is `#[private]` (BG-NEAR-001) and reads the promise result
(BG-NEAR-002). Unit tests: 28 (`cargo +stable test -p swarm_jobs` from `tests/fixtures/near`; the dev-dependencies
need rustc ≥ 1.88, the wasm is built with the pinned 1.85.0), one per refusal plus the happy path, a failed transfer,
reject → resubmit → approve, the minimum first deposit, the withdraw floor while a job is under review, and the
views. Scenarios on near-sandbox (CI job `scenario runner`):
`scenarios/swarm_jobs/01_review_pays_agent.json` (agent +2 NEAR, job `Paid`, second approve refused),
`02_review_guardrails.json` (join with 0 and with 0.09 NEAR refused, unregistered submit, non-reviewer approve, approve with another wasm hash, the private
callback called directly, reject → resubmit → approve), `03_cancel_refunds.json` (only the poster cancels, not
after submit, refunds, a withdraw below 0.1 NEAR refused while the job is under review, budget withdraw). CI also gates the contract with its own report (fresh deploy) and uploads
the wasm as the `swarm_jobs-wasm` artifact.

## `bumm-guard review-job`

```
python3 -m bumm_guard review-job --jobs-contract ACCOUNT --job ID --crate DIR --wasm FILE \
  [--simulate-report FILE] [--account TARGET] --network testnet [--reviewer ACCOUNT] [--rpc-url URL] [--out PATH]
```

1. Reads the job and the reviewer with read-only `call_function` queries (`get_job`, `get_reviewer`). The job must
   be `Submitted`; `--reviewer`, if given, must be the contract's reviewer (it is the default signer).
2. sha256 of `--wasm` must equal the job's `wasm_sha256`, sha256 of `--simulate-report` its `report_sha256`
   (the contract always records a report hash, so `--simulate-report` is needed in practice).
3. Runs the deploy gate in dry-run mode on that wasm for the job's `target_account` (or `--account`, which must not
   differ from it): scan of `--crate`, report bound to the wasm, `view_code` of the target and, if it has code, the
   upgrade proof for exactly (on-chain sha256 → wasm sha256) and the migration guard. Nothing is deployed.
4. Writes the review report (`docs/FORMATS.md` §7) to `.bumm-guard/review-<job>.json` (gitignored), prints the line
   `note: the scenario report and the crate were supplied with the submission; this review checked that they are
   the submitted files and ran the gate on them, it did not re-execute the scenarios or rebuild the wasm` (the
   report records the same as `"evidence_origin": "submission (not re-executed)"`), then the verdict and one
   command for the reviewer:
   - gate passed and there is something to deploy (exit 0, `REVIEW PASSED`): `near contract call-function
     as-transaction <jobs> approve json-args
     '{"job_id":..,"wasm_sha256":..,"gate_report_sha256":<sha256 of the review report>}' prepaid-gas '100 Tgas'
     attached-deposit '0 NEAR' sign-as <reviewer> network-config testnet sign-with-keychain send`;
   - gate blocked (exit 3, `REVIEW BLOCKED`): the same with `reject` and
     `{"job_id":..,"reason":<the gate's DEPLOY BLOCKED line, ≤ 500 bytes>}`;
   - the target account already runs exactly the submitted wasm (the gate's `NO CHANGE`; exit 3, `REVIEW
     BLOCKED`): `reject` with the reason `the target account already runs this exact wasm: nothing was changed by
     this submission`. It is never an approve;
   - bad input, a job that is not `Submitted`, files other than the submitted ones, or an RPC failure (exit 2): no
     command, no report. An RPC error never turns into a reject: this holds both when the job cannot be read
     (`get_job` / `get_reviewer`) and when the gate cannot read the target account (`view_code` / `view_account`).
     The message says the submission was not judged; run the review again. (The plain `gate` command answers
     `DEPLOY BLOCKED`, exit 3, when it cannot read the account; `review-job` does not turn that into a verdict.)

Text that comes from the submitted report or from `get_job` (scenario names, hashes, the agent id) is printed
without control characters and cut to 200 characters (`docs/FORMATS.md` §4), so a submission cannot draw a fake
`REVIEW PASSED` block in the reviewer's terminal.

The gate's own deploy command for the target account appears in the output for information only; it is not part
of the job and nobody runs it during the review. MCP: the tool `review_job` (`jobs_contract`, `job_id`,
`crate_dir`, `wasm`, `simulate_report?`, `account?`, `network`, `reviewer?`) returns `{verdict, passed, reason,
command, command_executed: false, gate_report_sha256, review_report_path, gate, job, messages, note}`; it has no
execute, sign or send parameter.

Keep the review report: `gate_report_sha256` on chain is its hash, and the file is what lets anyone check what the
reviewer approved.

## Honest limits

- **The review runs off-chain, and the approve is signed by a trusted reviewer account.** The contract checks only
  that the caller is the reviewer and that the approved wasm hash is the submitted one. It cannot verify a sandbox
  proof by itself: a dishonest or compromised reviewer can approve anything. `gate_report_sha256` makes the
  reviewer's decision auditable, not trustless.
- **The report and the crate are supplied by the agent, and the review does not re-run them.** `review-job` checks
  that the two files have the submitted hashes and runs the gate on them. It does not execute the scenarios and
  does not build the wasm. A fabricated report (a JSON file that claims a passing upgrade scenario for the right
  pair of hashes) passes the gate. So the reviewer must take the report from a run they control: their own CI, or
  their own `bumm-guard simulate` on the submitted wasm, and compare its sha256 with the submitted `report_sha256`.
  In the testnet run the report is this repository's CI report, committed in `evidence/`. Nothing binds the crate
  sources to the wasm either: the scan and the migration guard read `--crate`, the proof is about `--wasm`, and
  no step shows that one was built from the other. The reviewer has to build the wasm from the crate (CI here)
  to know that.
- **`approve` binds the wasm hash only.** Its arguments are the job id, the wasm hash and the hash of the review
  report. The contract compares the wasm hash with the submitted one; it does not compare anything with the
  submitted `report_sha256` and does not name the agent. The reward goes to whoever is the job's agent when the
  approve arrives. An approve command printed for one submission would also pay a later resubmission of the same
  wasm hash by another agent with another report (possible only after a reject in between).
- **Nothing binds a submission to its author, and nothing protects the agent from the poster.** A submission is
  two hashes; the contract does not know who built the wasm. The files have to be public (or at least in the
  reviewer's and poster's hands) before payment, so a copycat can submit the same hashes to another job, and the
  poster can use the wasm without the job ever being approved. A change of the target's code after the submit
  invalidates the proof: the proof is for exactly (code on the account → submitted wasm), so if the target's owner
  deploys anything else, the review blocks, and if the owner deploys the submitted wasm itself, the review gives
  `NO CHANGE` and a reject. In both cases the poster can then cancel and take the reward back. The agent has no
  defence against that in this contract; it assumes an honest poster and reviewer.
- **No real inference and no agent runtime.** `join_swarm` only records a budget; nothing spends it. There are no
  bids, no job assignment, no reputation, no slashing.
- **One reviewer, no DAO.** The reviewer is fixed at `new`; there is no way to change it, no quorum and no appeal.
- **The 0.1 NEAR minimum first deposit only raises the cost of spam.** It is not reputation and nothing is ever
  slashed: an agent that submits junk keeps its budget and can withdraw it once nothing of its is under review.
  The floor is flat, not per job: one 0.1 NEAR deposit lets an agent keep any number of jobs under review at the
  same time (each stays locked until the reviewer acts, see the next point).
- **A submitted job stays locked until the reviewer acts.** While a job is `Submitted` the poster cannot cancel it
  and no other agent can submit; only `approve` or `reject` by the reviewer unlocks it. A reviewer who does nothing
  keeps the reward locked.
- **`withdraw_budget` and `cancel` have no transfer callback.** The state is updated before the transfer to the
  caller; the caller account exists, so a failure is unlikely, but a failed refund is not re-credited (only
  `approve` has a callback that undoes its state on a failed transfer).
- The agent submits hashes, not files: the reviewer must obtain the wasm and the report out of band (here: files in
  the repository / a CI artifact).
- `#[init]` `new` is a separate transaction after the deploy (the gate prints `without-init-call` for a fresh
  deploy); until it runs, anyone could initialise the contract with another reviewer. Run `new` right after the
  deploy and check `get_reviewer`.
- **Storage is paid by the contract and records are never deleted.** Jobs and agents are stored from the
  contract's own balance; descriptions are capped at 1000 bytes, profiles at 200, reasons at 500. No storage
  deposit is charged to posters or agents, a job may carry a reward of 1 yoctoNEAR, and a cancelled, rejected or
  paid job stays in the state for ever. Posting many tiny jobs with long descriptions therefore locks more and
  more of the contract's balance as storage stake (about 0.01 NEAR per 1000 bytes) at the cost of gas only. When
  the free balance runs out, new calls that add state fail and transfers out of the contract (a reward, a refund,
  a budget withdrawal) may fail too, because the account must keep its storage stake. This was not tested.
- **A target that already runs the submitted wasm is rejected, not approved.** The gate's `NO CHANGE` means there
  is nothing to deploy; `review-job` prints `reject` with the reason `the target account already runs this exact
  wasm: nothing was changed by this submission`. Run the review before the target is upgraded: after the upgrade
  the same honest submission is rejected.
- **An RPC failure is not a verdict.** If the job or the target account cannot be read, `review-job` exits 2 and
  prints no command; nothing is approved or rejected because a node was down.
- Shown in the sandbox (CI), with unit tests, and on testnet up to the review: the contract was deployed through
  the gate to `bumm-guard-jobs.testnet`, an agent joined, two jobs were posted and submitted, and the review was
  rehearsed read-only (one blocked, one passed); see
  [evidence/SWARM_JOBS_TESTNET.md](../evidence/SWARM_JOBS_TESTNET.md). The approve and the payment are not done
  yet.

## Testnet run plan (the owner signs every transaction; nothing here is run by BUMM Guard)

Status on 2026-11-06: steps 0 to 6 and the posting and submit of job 1 (first half of step 9) are done, and the
two reviews (step 7 and the review in step 9) were rehearsed read-only; transactions and the checks after each are
in [evidence/SWARM_JOBS_TESTNET.md](../evidence/SWARM_JOBS_TESTNET.md). In the live run job 1 was posted before the
two submits. Step 8 (approve, payment) and the reject of job 1 are not done yet.

Accounts: `bumm-guard-jobs.testnet` (new, holds the contract), reviewer `bumm-guard-bob.testnet`, agent
`bumm-guard-alice.testnet`, poster `bumm-guard-demo.testnet`, target `bumm-guard-escrow2.testnet` (runs escrow v1,
`ecd8a805…`; it is only read here and is upgraded later, in the video). Hashes used below:

| file | sha256 |
|---|---|
| `evidence/wasm/escrow_v2.wasm` | `13425a042dec55eec364240e890a1645c7332f2898149353ebcb5830a5886013` |
| `evidence/escrow-upgrade-with-migrate-report-ci.json` | `f305461fe47aa2d15d2361038b7ed010be51b2c53219fee1352eac59a24eb420` |
| `evidence/escrow-upgrade-code-only-report-ci.json` | `e7cfcfd8712d2dc31c1fb2c951894b367da9dc34a93376b45e1f61e249ac55c5` |

0. The contract wasm and its scenario report are committed: `evidence/wasm/swarm_jobs.wasm` (sha256 `bcace58397f2ee597dd5687187ea9e178e3cd5405dfdb810553214e416a9a92b`, in `evidence/wasm/SHA256SUMS`, checked by CI on every push) and `evidence/swarm-jobs-report-ci.json` (CI run 42272132654 on `adb8b70`, 3/3 scenarios, 81/81 steps). No download needed.
1. **owner** creates the contract account from the faucet:
   ```
   near account create-account sponsor-by-faucet-service bumm-guard-jobs.testnet autogenerate-new-keypair save-to-keychain network-config testnet create
   ```
2. Gate the fresh deploy (expected: `has no contract code: fresh deploy`, `simulate report ok: 3 scenario(s)
   passed for this wasm`, the storage lock, and one `near contract deploy bumm-guard-jobs.testnet use-file
   evidence/wasm/swarm_jobs.wasm without-init-call network-config testnet sign-with-keychain send`);
   **owner** runs the printed command:
   ```
   python3 -m bumm_guard gate tests/fixtures/near/swarm_jobs --wasm evidence/wasm/swarm_jobs.wasm \
     --simulate-report evidence/swarm-jobs-report-ci.json \
     --account bumm-guard-jobs.testnet --network testnet --estimate-storage
   ```
3. **owner** initialises it right away with bob as reviewer, then checks the deploy:
   ```
   near contract call-function as-transaction bumm-guard-jobs.testnet new json-args '{"reviewer":"bumm-guard-bob.testnet"}' prepaid-gas '30 Tgas' attached-deposit '0 NEAR' sign-as bumm-guard-jobs.testnet network-config testnet sign-with-keychain send
   python3 -m bumm_guard gate --verify-deployed bumm-guard-jobs.testnet --wasm evidence/wasm/swarm_jobs.wasm --network testnet --smoke 'get_reviewer="bumm-guard-bob.testnet"' --smoke 'job_count=0'
   ```
4. **alice** joins the swarm with 0.5 NEAR of budget:
   ```
   near contract call-function as-transaction bumm-guard-jobs.testnet join_swarm json-args '{"profile":"escrow upgrade agent"}' prepaid-gas '30 Tgas' attached-deposit '0.5 NEAR' sign-as bumm-guard-alice.testnet network-config testnet sign-with-keychain send
   ```
5. **demo** posts job 0 with a 1 NEAR reward (returns `0`):
   ```
   near contract call-function as-transaction bumm-guard-jobs.testnet post_job json-args '{"description":"upgrade bumm-guard-escrow2.testnet to escrow v2 keeping its deals","target_account":"bumm-guard-escrow2.testnet"}' prepaid-gas '30 Tgas' attached-deposit '1 NEAR' sign-as bumm-guard-demo.testnet network-config testnet sign-with-keychain send
   ```
6. **alice** submits escrow v2 with the passing migrate report:
   ```
   near contract call-function as-transaction bumm-guard-jobs.testnet submit json-args '{"job_id":0,"wasm_sha256":"13425a042dec55eec364240e890a1645c7332f2898149353ebcb5830a5886013","report_sha256":"f305461fe47aa2d15d2361038b7ed010be51b2c53219fee1352eac59a24eb420"}' prepaid-gas '30 Tgas' attached-deposit '0 NEAR' sign-as bumm-guard-alice.testnet network-config testnet sign-with-keychain send
   ```
7. Review (read-only; expected: `upgrade of bumm-guard-escrow2.testnet: ecd8a805… -> 13425a04…, proved by passing
   scenario …`, `migration migrate: #[private] (src/lib.rs:118)`, `REVIEW PASSED`, exit 0, and the `approve`
   command with `gate_report_sha256` = sha256 of `.bumm-guard/review-0.json`):
   ```
   python3 -m bumm_guard review-job --jobs-contract bumm-guard-jobs.testnet --job 0 --crate tests/fixtures/near/escrow_v2 \
     --wasm evidence/wasm/escrow_v2.wasm --simulate-report evidence/escrow-upgrade-with-migrate-report-ci.json --network testnet
   ```
8. **bob** runs the printed `approve … sign-as bumm-guard-bob.testnet …` command. Check: `get_job` says `Paid`,
   alice's balance is about 1 NEAR higher:
   ```
   near contract call-function as-read-only bumm-guard-jobs.testnet get_job json-args '{"id":0}' network-config testnet now
   ```
9. Second job, the blocked case. **demo** posts job 1 (same command as step 5, returns `1`); **alice** submits the
   same wasm with the code-only report:
   ```
   near contract call-function as-transaction bumm-guard-jobs.testnet submit json-args '{"job_id":1,"wasm_sha256":"13425a042dec55eec364240e890a1645c7332f2898149353ebcb5830a5886013","report_sha256":"e7cfcfd8712d2dc31c1fb2c951894b367da9dc34a93376b45e1f61e249ac55c5"}' prepaid-gas '30 Tgas' attached-deposit '0 NEAR' sign-as bumm-guard-alice.testnet network-config testnet sign-with-keychain send
   python3 -m bumm_guard review-job --jobs-contract bumm-guard-jobs.testnet --job 1 --crate tests/fixtures/near/escrow_v2 \
     --wasm evidence/wasm/escrow_v2.wasm --simulate-report evidence/escrow-upgrade-code-only-report-ci.json --network testnet
   ```
   Expected: `REVIEW BLOCKED: DEPLOY BLOCKED: account bumm-guard-escrow2.testnet already runs code ecd8a805…; this
   is an upgrade and no passing upgrade scenario proves …`, exit 3, and a `reject` command. **bob** runs it; `get_job
   {"id":1}` says `Rejected` with that reason. Optionally **demo** takes the reward back:
   ```
   near contract call-function as-transaction bumm-guard-jobs.testnet cancel json-args '{"job_id":1}' prepaid-gas '30 Tgas' attached-deposit '0 NEAR' sign-as bumm-guard-demo.testnet network-config testnet sign-with-keychain send
   ```

Both reviews only read `bumm-guard-escrow2.testnet` (`view_code`); it stays on v1 until the video's upgrade.
