# Guarded jobs for agent swarms (`swarm_jobs` + `review-job`)

**What it is.** A minimal onchain job escrow in which an agent's reward is released only after BUMM Guard's
review of its work passed. A poster locks a reward in a job; an agent that joined the swarm submits the sha256 of
the wasm it built and of the scenario report that proves it; the reviewer runs `bumm-guard review-job`, which reads
the job, checks that the files are exactly the submitted ones and runs the deploy gate on them; the gate's verdict
becomes one printed command, `approve` (the reward goes to the agent) or `reject` (the agent may resubmit). The
reviewer signs that command with their own key; BUMM Guard never signs.

**Why.** NEAR co-founder Illia Polosukhin asked hackathon builders for "Swarm-for-Hire (with guardrails or
governance AI review, please)": jobs bid onchain, agents in a swarm funded with NEAR for inference, paid for the
jobs they take; this is the guardrail part of that request, for agents whose work is contract code.
<!-- TODO(owner): link the post here; the URL is not recorded in the repository or the session notes. -->

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
4. Writes the review report (`docs/FORMATS.md` §7) to `.bumm-guard/review-<job>.json` (gitignored) and prints one
   command for the reviewer:
   - gate passed (exit 0): `near contract call-function as-transaction <jobs> approve json-args
     '{"job_id":..,"wasm_sha256":..,"gate_report_sha256":<sha256 of the review report>}' prepaid-gas '100 Tgas'
     attached-deposit '0 NEAR' sign-as <reviewer> network-config testnet sign-with-keychain send`;
   - gate blocked (exit 3): the same with `reject` and `{"job_id":..,"reason":<the gate's DEPLOY BLOCKED line, ≤ 500 bytes>}`.
   - bad input, unreadable RPC, a job that is not `Submitted`, files other than the submitted ones (exit 2): no
     command, no report. An RPC error never turns into a reject.

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
- **No real inference and no agent runtime.** `join_swarm` only records a budget; nothing spends it. There are no
  bids, no job assignment, no reputation, no slashing.
- **One reviewer, no DAO.** The reviewer is fixed at `new`; there is no way to change it, no quorum and no appeal.
- **The 0.1 NEAR minimum first deposit only raises the cost of spam.** It is not reputation and nothing is ever
  slashed: an agent that submits junk keeps its budget and can withdraw it once nothing of its is under review.
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
- Storage for jobs and agents is paid from the contract's own balance; descriptions are capped at 1000 bytes,
  profiles at 200, reasons at 500. No storage deposit is charged to posters or agents.
- Reviewing a target that already runs the submitted wasm gives the gate's `NO CHANGE` (exit 0), i.e. approve:
  run the review before the target is upgraded.
- Shown in the sandbox (CI) and with unit tests; the testnet run below is a plan, not done yet.

## Testnet run plan (the owner signs every transaction; nothing here is run by BUMM Guard)

Accounts: `bumm-guard-jobs.testnet` (new, holds the contract), reviewer `bumm-guard-bob.testnet`, agent
`bumm-guard-alice.testnet`, poster `bumm-guard-demo.testnet`, target `bumm-guard-escrow2.testnet` (runs escrow v1,
`ecd8a805…`; it is only read here and is upgraded later, in the video). Hashes used below:

| file | sha256 |
|---|---|
| `evidence/wasm/escrow_v2.wasm` | `13425a042dec55eec364240e890a1645c7332f2898149353ebcb5830a5886013` |
| `evidence/escrow-upgrade-with-migrate-report-ci.json` | `f305461fe47aa2d15d2361038b7ed010be51b2c53219fee1352eac59a24eb420` |
| `evidence/escrow-upgrade-code-only-report-ci.json` | `e7cfcfd8712d2dc31c1fb2c951894b367da9dc34a93376b45e1f61e249ac55c5` |

0. Download the wasm the CI scenarios ran against and their report (`<RUN>` = the green CI run on the branch
   that is deployed; `e2e/` is gitignored):
   ```
   gh run download <RUN> --repo GodBooster/bumm-guard -n swarm_jobs-wasm -n simulate-report -D e2e/swarm
   ```
1. **owner** creates the contract account from the faucet:
   ```
   near account create-account sponsor-by-faucet-service bumm-guard-jobs.testnet autogenerate-new-keypair save-to-keychain network-config testnet create
   ```
2. Gate the fresh deploy (expected: `has no contract code: fresh deploy`, `simulate report ok: 3 scenario(s)
   passed for this wasm`, the storage lock, and one `near contract deploy bumm-guard-jobs.testnet use-file
   e2e/swarm/swarm_jobs-wasm/swarm_jobs.wasm without-init-call network-config testnet sign-with-keychain send`);
   **owner** runs the printed command:
   ```
   python3 -m bumm_guard gate tests/fixtures/near/swarm_jobs --wasm e2e/swarm/swarm_jobs-wasm/swarm_jobs.wasm \
     --simulate-report e2e/swarm/simulate-report/swarm-jobs-report.json \
     --account bumm-guard-jobs.testnet --network testnet --estimate-storage
   ```
3. **owner** initialises it right away with bob as reviewer, then checks the deploy:
   ```
   near contract call-function as-transaction bumm-guard-jobs.testnet new json-args '{"reviewer":"bumm-guard-bob.testnet"}' prepaid-gas '30 Tgas' attached-deposit '0 NEAR' sign-as bumm-guard-jobs.testnet network-config testnet sign-with-keychain send
   python3 -m bumm_guard gate --verify-deployed bumm-guard-jobs.testnet --wasm e2e/swarm/swarm_jobs-wasm/swarm_jobs.wasm --network testnet --smoke 'get_reviewer="bumm-guard-bob.testnet"' --smoke 'job_count=0'
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
