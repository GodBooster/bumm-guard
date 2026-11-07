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

**Two versions.** v1 (`tests/fixtures/near/swarm_jobs`) is what runs on `bumm-guard-jobs.testnet` since 2026-11-06,
with two jobs submitted. An adversarial review of v1 on the same day found four weaknesses (listed under "What v2
changed"); v2 (`tests/fixtures/near/swarm_jobs_v2`, 2026-11-07) fixes them and carries a `migrate` from the v1 state.
The upgrade of the live contract goes through BUMM Guard's own deploy gate, with the upgrade scenario
`scenarios/upgrade/swarm_jobs_v1_to_v2_with_migrate.json` as the proof: the thesis of this repository applied to its
own contract (the third live upgrade pair, after N06 → N10 and escrow v1 → v2). The v1 crate is kept unchanged, like
`escrow_v1`, so the pair can be rebuilt and re-proved. This document describes v2; the differences to v1 are listed
once, below the methods table.

## Flow

```
 poster                    agent (in the swarm)               reviewer (runs BUMM Guard)
   |                              |                                     |
   |                              |-- join_swarm(profile) + budget ---->|  (budget tracked, no inference here;
   |                              |                                     |   the record's storage part stays locked)
   |-- post_job(desc, target, reward, scenario_sha256?, assigned_agent?) + deposit -->| job: Open
   |     deposit >= reward + storage fee of the record; excess back    |
   |                              |                                     |
   |                              |  builds wasm, runs `bumm-guard simulate` (CI)
   |                              |-- submit(job, wasm_sha256, report_sha256) --> job: Submitted
   |                              |     needs budget >= 0.1 NEAR x (jobs under review + 1) + storage part;
   |                              |     only assigned_agent may submit when the job names one
   |                              |                                     |
   |                              |          bumm-guard review-job (read-only):
   |                              |            get_job -> v2? Submitted?  sha256(--wasm) == wasm_sha256?
   |                              |            sha256(--simulate-report) == report_sha256?
   |                              |            scenario_sha256 pinned? -> every report scenario has it
   |                              |            gate: scan, report bound to the wasm, view_code(target):
   |                              |                  code there -> upgrade proof for (onchain -> wasm),
   |                              |                  migration method protected
   |                              |            writes .bumm-guard/review-<job>.json, prints ONE command
   |                              |                                     |
   |                              |      passed: approve(job, agent, wasm_sha256, report_sha256, sha256(review report))
   |                              |<----- reward (transfer) ------------|  job: Paying -> Paid
   |                              |      blocked: reject(job, reason, close=false)  job: Rejected -> an agent resubmits
   |                              |               reject(job, reason, close=true)   job: Cancelled, reward back to the poster
   |                              |                                     |
   |-- cancel(job) while Open or Rejected: reward back (the fee stays)     job: Cancelled
   |                              |-- leave_swarm() with nothing under review: whole budget back, record deleted
```

## Contract v2 (`tests/fixtures/near/swarm_jobs_v2`, near-sdk 5.17.2)

| method | who | what |
|---|---|---|
| `new(reviewer)` | once, `#[init]` | sets the reviewer account |
| `migrate()` | the contract account only (`#[private]`, `#[init(ignore_state)]`) | reads the v1 state and rewrites every job into the v2 layout (`reward_storage_fee` 0, `scenario_sha256` null); agents and counters carry over; called in the deploy batch |
| `join_swarm(profile)` payable | anyone except the reviewer | registers the caller as an agent (or updates the profile); the deposit is added to its budget. The first join needs at least 0.1 NEAR (`MIN_AGENT_DEPOSIT`); the storage cost of the new record is measured then and recorded as the agent's storage part (shown as `storage_cost`): it stays in the budget but cannot be withdrawn while the agent is registered. Top-ups may be any amount |
| `withdraw_budget(amount)` | the agent | sends `amount` (yoctoNEAR string) of its own budget back; what stays must be at least 0.1 NEAR per job of the agent in `Submitted` or `Paying`, plus the storage part |
| `leave_swarm()` | the agent | refused while the agent has a job in `Submitted` or `Paying`; otherwise deletes the record, frees its storage and transfers the whole remaining budget (storage part included) back; returns the amount. Finished jobs that name the agent are kept; a later join starts again at 0.1 NEAR with `jobs_paid` 0 |
| `post_job(description, target_account?, reward, scenario_sha256?, assigned_agent?)` payable | anyone | `reward` is a yoctoNEAR string (> 0); the attached deposit must be at least the reward plus the storage fee of the new record (bytes added × `storage_byte_cost`, measured in the call); the fee is kept by the contract, non-refundable, and recorded as `reward_storage_fee`; the excess over reward + fee is transferred back to the poster in the same call. `scenario_sha256`, when given, is the sha256 of the one scenario file the poster accepts as proof (64 lowercase hex); the contract only stores and shows it. `assigned_agent`, when given, is the only account that may `submit`. Status `Open`; returns the id |
| `submit(job_id, wasm_sha256, report_sha256)` | a registered agent (the `assigned_agent` if the job has one) | allowed while `Open` or `Rejected`, and only if `budget >= 0.1 NEAR × (jobs_in_review + 1)` plus the agent's storage part; both hashes 64 lowercase hex; status `Submitted` |
| `approve(job_id, agent, wasm_sha256, report_sha256, gate_report_sha256)` | the reviewer | needs `Submitted` and `agent`, `wasm_sha256`, `report_sha256` all equal to the current submission; records the gate report hash; `Paying`, transfers the reward, then the `#[private]` callback `on_reward_paid` sets `Paid`, or on a failed transfer `Submitted` again with the reward still in the contract (exactly as in v1) |
| `reject(job_id, reason, close)` | the reviewer | needs `Submitted`; stores the reason (≤ 500 bytes); the agent's `jobs_in_review` goes down. `close = false`: `Rejected`, an agent may submit again. `close = true`: `Cancelled` and the reward is transferred back to the poster in the same transaction (the fee stays), so a rejected agent cannot keep the job locked by resubmitting before the poster's `cancel` |
| `cancel(job_id)` | the poster | only while `Open` or `Rejected`; refunds the reward, not the storage fee; `Cancelled`; the record stays |
| views | anyone | `get_job(id)`, `list_jobs(from_index?, limit?)` (≤ 100), `job_count()`, `get_agent(account)`, `agent_count()`, `get_reviewer()` |

`get_job` returns `{id, poster, description, target_account, reward, status, agent, wasm_sha256, report_sha256,
gate_report_sha256, reject_reason, reward_storage_fee, scenario_sha256, assigned_agent}`; `status` is one of `Open`, `Submitted`,
`Rejected`, `Paying`, `Paid`, `Cancelled`. `get_agent` returns `{account, profile, budget, jobs_paid, jobs_in_review,
storage_cost}` (`jobs_in_review` counts the agent's jobs in `Submitted` or `Paying`). The v1 field names are kept and
only new fields were added, so v1 callers keep working; `storage_cost` and `reward_storage_fee` are `"0"` for an
agent or job that was registered or posted on v1, and `assigned_agent` is null for a v1 job.

**What v2 changed** (each item was a weakness of v1 found in the review of 2026-11-06):

- *Storage was paid by the contract and records were never freed.* v2: the poster pays the storage of the job
  record (`post_job` measures it with `env::storage_usage()` before and after the insert), the agent's first deposit
  covers its own record and that part is locked until `leave_swarm`, which deletes the record. Spam now costs its
  storage. The fee is not refunded by `cancel`; finished jobs (`Paid`, `Cancelled`, `Rejected` without a resubmit)
  are kept as history: they are what `jobs_paid` and the reviewer's `gate_report_sha256` point to.
- *The 0.1 NEAR floor was flat.* v2: 0.1 NEAR per job under review plus the agent's storage part, checked on
  `submit` and on `withdraw_budget`.
- *`approve` bound only the wasm hash.* v2: it names the agent, the wasm hash and the report hash, and all three
  must equal the current submission, so an approve printed for one submission cannot pay a resubmission of the
  same wasm by another agent or with another report (`02_floor_per_job_and_approve_bindings.json` shows the
  copycat case).
- *Nothing let the poster pin the acceptance criteria.* v2: `post_job` may carry `scenario_sha256`; the contract
  stores it, `review-job` rejects a report whose scenarios do not all carry it.
- *The first submitter owned the job, and a copycat could take it over after a reject.* v2: `post_job` may name
  `assigned_agent`; then only that account may `submit`, before and after a reject
  (`04_reject_close_and_assigned_agent.json`). Without it the first submitter still owns the job until the
  reviewer acts.
- *A rejected agent could deny the poster's `cancel` by resubmitting first, forever, at gas cost.* v2:
  `reject(job_id, reason, close)`; with `close = true` the job is `Cancelled` and the reward refunded in the same
  transaction. `review-job` prints both variants and the reviewer picks.
- Signature changes against v1: `post_job` takes `reward` as an argument (v1: reward = attached deposit),
  `scenario_sha256` and `assigned_agent`; `approve` has five arguments (v1: three); `reject` has `close`. A
  v1-style `approve` on v2 fails at input deserialization; `review-job` refuses to print an approve for a v1
  contract (exit 2). The reward callback runs with 20 Tgas (v1: 10).

**State and the upgrade from v1.** The root struct keeps the v1 shape (`reviewer`, `jobs`, `agents`, `agent_count`,
`next_id`); only the `Job` record gained two fields. The agent's storage part is kept under its own raw key (`s` +
account id), not as a root field, so that the root layout does not change. Consequences, both shown by the upgrade
scenarios: after a *code-only* upgrade `job_count`, `agent_count`, `get_reviewer` and `get_agent` still answer
correctly (the smoke view lies, as with escrow) and every read of a job fails with near-sdk's `Cannot deserialize
element`; `migrate()` re-inserts every job under a new map prefix (the reason is in the method's doc comment, the
same as in `escrow_v2`) and clears the old one. Migrated jobs keep status, agent and hashes, so a `Submitted` job
stays under review and the v2 `approve` pays it; migrated agents keep their budget and `jobs_in_review` and have
`storage_cost` 0 (their record was paid by the contract in v1).

Checks of our own rules on the contract (dogfooding): `python3 -m bumm_guard scan tests/fixtures/near/swarm_jobs_v2`
→ `0 finding(s) in 1 file(s)`; the callback is `#[private]` (BG-NEAR-001) and reads the promise result (BG-NEAR-002);
the migration guard reports `migrate` as `#[private]` (`src/lib.rs:243`). Unit tests: 56 (`cargo +stable test -p
swarm_jobs_v2` from `tests/fixtures/near`; the dev-dependencies need rustc ≥ 1.88, the wasm is built with the pinned
1.85.0): the v1 cases kept, plus the storage fee (taken, recorded, higher for a longer description, exactly the
reward refused, less than the reward refused, the exact amount accepted), the pinned scenario (stored, shown,
uppercase and 63-character hashes refused), the approve bindings (other agent, other report, the copycat
resubmission), the per-job floor on submit and withdraw (with the storage part: exactly 0.1 NEAR is not enough for
one job), the assigned agent (another agent refused before and after a reject, the assigned one accepted and shown),
`reject` with `close` (Cancelled, counter down, no resubmit, no cancel, reviewer only, Submitted only; without
`close` a resubmit still works), `leave_swarm` (refused while Submitted or Paying, refused
for a non-agent, refunds the whole budget and frees the record, allowed after a reject) and `migrate` on a v1-shaped
state written through v1's own Borsh structs (the live testnet state mirrored: two submitted jobs, alice with 0.5
NEAR; the v2 approve then pays job 0 and alice may leave with her 0.5 NEAR). Scenarios on near-sandbox (CI job
`scenario runner`): `scenarios/swarm_jobs_v2/01_storage_fee_and_pinned_scenario.json` (fee taken and excess
refunded, four refused posts, pinned hash shown, five-argument approve, `leave_swarm` returns the budget),
`02_floor_per_job_and_approve_bindings.json` (second submit with 0.15 NEAR refused, withdraw below the floor refused,
approve for another agent / wasm / report refused, the approve for the first agent does not pay the copycat,
withdraw of the storage part refused, leave), `03_leave_swarm_and_cancel.json` (no leaving under review, cancel
refunds the reward and keeps the fee, leave frees the record, rejoin at 0.1 NEAR), `04_reject_close_and_assigned_agent.json`
(another agent refused on an assigned job, the assigned one accepted, reject without close then resubmit, reject
with close: Cancelled, poster refunded, counter down, no resubmit, no cancel, not twice), and the upgrade pair
`scenarios/upgrade/swarm_jobs_v1_to_v2_with_migrate.json` (49 steps: v1 deployed, the live state mirrored, upgrade
with `migrate`, every field of both jobs and of the agent checked with `equals`, then the v2 path: approve of job 0,
a v1-style approve refused, reject and cancel of job 1, a v2 job with fee and pin, alice leaves) and
`swarm_jobs_v1_to_v2_code_only.json` (same prefix, upgrade without `migrate`: expected to FAIL; CI asserts that
exactly the four views pass and that all seven steps that read a job fail with `Cannot deserialize element`). CI
also gates v2 with its own report (fresh deploy) and uploads the wasm as the `swarm_jobs_v2-wasm` artifact; the
hash guard checks the six committed wasm files (`evidence/wasm/swarm_jobs_v2.wasm`, sha256 `5dbcf8cd…`, was added to it from
the green CI run after the merge (not from a local build).

## `bumm-guard review-job`

```
python3 -m bumm_guard review-job --jobs-contract ACCOUNT --job ID --crate DIR --wasm FILE \
  [--simulate-report FILE] [--account TARGET] --network testnet [--reviewer ACCOUNT] [--rpc-url URL] [--out PATH]
```

1. Reads the job and the reviewer with read-only `call_function` queries (`get_job`, `get_reviewer`). The contract
   must be v2: `get_job` carries `scenario_sha256` (null or a hash). On a v1 contract (no such key) the review stops
   with exit 2 and prints no command: the v2 `approve` it would print names the agent and the report hash, which
   v1 refuses (`runs swarm_jobs v1 (get_job has no scenario_sha256) … upgrade the contract to v2 first`). The job
   must be `Submitted` with a valid agent; `--reviewer`, if given, must be the contract's reviewer (it is the
   default signer).
2. sha256 of `--wasm` must equal the job's `wasm_sha256`, sha256 of `--simulate-report` its `report_sha256`
   (the contract always records a report hash, so `--simulate-report` is needed in practice).
3. If the job has a `scenario_sha256`, every scenario in the submitted report must carry that `scenario_sha256`
   (bumm-sim records it per scenario, §3); the line `scenario_sha256 on the job: <hash>; report scenarios with it:
   <n> of <m>` says what was found. A mismatch, a report with no scenario, or one with several scenario files, is a
   reject (the pin decides even when the gate passed, and it is the reason even when the gate blocked too). Without
   a pin the line says `null (the poster accepts any scenario)`.
4. Runs the deploy gate in dry-run mode on that wasm for the job's `target_account` (or `--account`, which must not
   differ from it): scan of `--crate`, report bound to the wasm, `view_code` of the target and, if it has code, the
   upgrade proof for exactly (on-chain sha256 → wasm sha256) and the migration guard. Nothing is deployed.
5. Writes the review report (`docs/FORMATS.md` §7) to `.bumm-guard/review-<job>.json` (gitignored), prints the line
   `note: the scenario report and the crate were supplied with the submission; this review checked that they are
   the submitted files and ran the gate on them, it did not re-execute the scenarios or rebuild the wasm` (the
   report records the same as `"evidence_origin": "submission (not re-executed)"`), then the verdict and one
   command for the reviewer:
   - gate passed, pin matched (or none) and there is something to deploy (exit 0, `REVIEW PASSED`): `near contract
     call-function as-transaction <jobs> approve json-args
     '{"job_id":..,"agent":<the job's agent>,"wasm_sha256":..,"report_sha256":..,"gate_report_sha256":<sha256 of
     the review report>}' prepaid-gas '100 Tgas' attached-deposit '0 NEAR' sign-as <reviewer> network-config
     testnet sign-with-keychain send`;
   - pin mismatch (exit 3, `REVIEW BLOCKED`): `reject` with the reason `the poster accepts only the scenario with
     sha256 <pin>; the submitted report has <hashes found>: run that scenario file and submit its report`;
   - gate blocked (exit 3, `REVIEW BLOCKED`): the same with `reject` and
     `{"job_id":..,"reason":<the gate's DEPLOY BLOCKED line, ≤ 500 bytes>,"close":false}`, followed by one more
     line, `or, to close the job as well (Cancelled, the reward goes back to the poster, nobody can resubmit):` and
     the same command with `"close":true`. The reviewer picks one; every reject (pin, gate, NO CHANGE) prints both;
   - the target account already runs exactly the submitted wasm (the gate's `NO CHANGE`; exit 3, `REVIEW
     BLOCKED`): `reject` with the reason `the target account already runs this exact wasm: nothing was changed by
     this submission`. It is never an approve;
   - bad input, a v1 contract, a job that is not `Submitted`, a job whose agent or `scenario_sha256` is not plain
     (not an account id, not 64 lowercase hex), files other than the submitted ones, or an RPC failure (exit 2): no
     command, no report. An RPC error never turns into a reject: this holds both when the job cannot be read
     (`get_job` / `get_reviewer`) and when the gate cannot read the target account (`view_code` / `view_account`).
     The message says the submission was not judged; run the review again. (The plain `gate` command answers
     `DEPLOY BLOCKED`, exit 3, when it cannot read the account; `review-job` does not turn that into a verdict.)

Text that comes from the submitted report or from `get_job` (scenario names and hashes, the reward, the target) is
printed without control characters and cut to 200 characters (`docs/FORMATS.md` §4), so a submission cannot draw a
fake `REVIEW PASSED` block in the reviewer's terminal. The agent is repeated verbatim in the approve command, so a
job whose `agent` is not a valid account id gets no command at all (exit 2).

The gate's own deploy command for the target account appears in the output for information only; it is not part
of the job and nobody runs it during the review. MCP: the tool `review_job` (`jobs_contract`, `job_id`,
`crate_dir`, `wasm`, `simulate_report?`, `account?`, `network`, `reviewer?`) returns `{verdict, passed, reason,
command, close_command, command_executed: false, gate_report_sha256, review_report_path, gate, job, messages,
note}` (`close_command` is the `close:true` reject, null on approve); it has no execute, sign or send parameter; a
v1 contract is a tool error.

Keep the review report: `gate_report_sha256` on chain is its hash, and the file is what lets anyone check what the
reviewer approved.

## Honest limits

- **The review runs off-chain, and the approve is signed by a trusted reviewer account.** The contract checks only
  that the caller is the reviewer and that the approved agent, wasm hash and report hash are the submitted ones. It
  cannot verify a sandbox proof by itself: a dishonest or compromised reviewer can approve anything.
  `gate_report_sha256` makes the reviewer's decision auditable, not trustless. The poster's `scenario_sha256` is
  enforced by `review-job`, not by the contract, for the same reason.
- **The report and the crate are supplied by the agent, and the review does not re-run them.** `review-job` checks
  that the two files have the submitted hashes and runs the gate on them. It does not execute the scenarios and
  does not build the wasm. A fabricated report (a JSON file that claims a passing upgrade scenario for the right
  pair of hashes, with the pinned `scenario_sha256` written into it) passes the gate. So the reviewer must take the
  report from a run they control: their own CI, or their own `bumm-guard simulate` on the submitted wasm, and
  compare its sha256 with the submitted `report_sha256`. In the testnet run the report is this repository's CI
  report, committed in `evidence/`. Nothing binds the crate sources to the wasm either: the scan and the migration
  guard read `--crate`, the proof is about `--wasm`, and no step shows that one was built from the other. The
  reviewer has to build the wasm from the crate (CI here) to know that.
- **Nothing protects the agent from the poster.** A submission is two hashes; the files have to be public (or at
  least in the reviewer's and poster's hands) before payment, so the poster can use the wasm without the job ever
  being approved. A change of the target's code after the submit invalidates the proof: the proof is for exactly
  (code on the account → submitted wasm), so if the target's owner deploys anything else, the review blocks, and
  if the owner deploys the submitted wasm itself, the review gives `NO CHANGE` and a reject. In both cases the
  poster can then cancel and take the reward back. The agent has no defence against that in this contract; it
  assumes an honest poster and reviewer.
- **The first submitter owns the job unless `assigned_agent` is set.** On an open job the first registered agent
  to `submit` holds it until the reviewer acts, and after a reject anyone may resubmit the same hashes; the
  approve names the agent, so the copycat is paid only if the reviewer approves *its* submission. A poster who
  wants one agent names it in `assigned_agent`. A copycat can still submit the same hashes to *another* job.
- **No real inference and no agent runtime.** `join_swarm` only records a budget; nothing spends it. There are no
  bids, no job assignment, no reputation, no slashing. The per-job floor raises the cost of keeping many jobs
  locked; an agent that submits junk still keeps its budget and may withdraw it once nothing of its is under
  review.
- **One reviewer, no DAO.** The reviewer is fixed at `new`; there is no way to change it, no quorum and no appeal.
- **A submitted job stays locked until the reviewer acts.** While a job is `Submitted` the poster cannot cancel it
  and no other agent can submit; only `approve` or `reject` by the reviewer unlocks it. A reviewer who does nothing
  keeps the reward locked, and the agent's 0.1 NEAR for that job with it. A reviewer who rejects with
  `close = false` leaves a window in which the rejected agent may resubmit before the poster's `cancel`; `close =
  true` ends the job instead.
- **The migration window is one-way.** `migrate` reads the v1 jobs under the v1 prefix. A code-only v2 deploy
  followed by one `post_job` writes a v2 record under that prefix, and `migrate` can no longer read the map: the
  v1 jobs would be stuck. Deploy and `migrate` in one batch only (what the gate prints), never code first.
- **`on_reward_paid` runs with 20 Tgas and has no admin path.** If that callback ever failed (it did not, in the
  sandbox or the unit tests) the job would stay `Paying` with the reward already paid and no method to repair it.
- **`migrate` is O(N) over the jobs.** Every record is read, rewritten (17 more bytes each) and the old one
  removed in one transaction; hundreds of jobs could exceed the gas limit, in which case the whole batch reverts
  and nothing is lost, but the upgrade needs another way. The live contract has two jobs.
- **The storage fee covers the record as posted; the contract still pays bounded growth.** Per job: `submit` and
  `approve` add about 200 bytes (the agent, two or three hashes), `reject` a reason of up to 500 bytes, `migrate`
  17 bytes per v1 record (the two new fields and the assignment). The agent's storage part is measured at the
  first join and a longer profile later is not re-measured (at most 200 bytes). Jobs and agents from v1 have no
  fee and no storage part. These are small, bounded amounts, not the open-ended lock of v1, but they are still
  paid from the contract's balance.
- **`withdraw_budget`, `leave_swarm`, `cancel`, `reject` with `close` and the refund in `post_job` have no transfer callback.** The state
  is updated before the transfer to the caller; the caller account exists, so a failure is unlikely, but a failed
  refund is not re-credited (only `approve` has a callback that undoes its state on a failed transfer).
- The agent submits hashes, not files: the reviewer must obtain the wasm and the report out of band (here: files in
  the repository / a CI artifact).
- `#[init]` `new` is a separate transaction after the deploy (the gate prints `without-init-call` for a fresh
  deploy); until it runs, anyone could initialise the contract with another reviewer. Run `new` right after the
  deploy and check `get_reviewer`. (The upgrade is different: `migrate` runs in the deploy batch and is
  `#[private]`.)
- **A target that already runs the submitted wasm is rejected, not approved.** The gate's `NO CHANGE` means there
  is nothing to deploy; `review-job` prints `reject` with the reason `the target account already runs this exact
  wasm: nothing was changed by this submission`. Run the review before the target is upgraded: after the upgrade
  the same honest submission is rejected.
- **An RPC failure is not a verdict.** If the job or the target account cannot be read, `review-job` exits 2 and
  prints no command; nothing is approved or rejected because a node was down.
- Shown in the sandbox (CI), with unit tests, and on testnet up to the review: v1 was deployed through the gate to
  `bumm-guard-jobs.testnet`, an agent joined, two jobs were posted and submitted, and the review was rehearsed
  read-only (one blocked, one passed); see [evidence/SWARM_JOBS_TESTNET.md](../evidence/SWARM_JOBS_TESTNET.md).
  The upgrade to v2, the approve and the payment are not done yet (plan below).

## Testnet run plan, v1 (the owner signs every transaction; nothing here is run by BUMM Guard)

Status on 2026-11-06: steps 0 to 6 and the posting and submit of job 1 (first half of step 9) are done, and the
two reviews (step 7 and the review in step 9) were rehearsed read-only; transactions and the checks after each are
in [evidence/SWARM_JOBS_TESTNET.md](../evidence/SWARM_JOBS_TESTNET.md). In the live run job 1 was posted before the
two submits. Step 8 (approve, payment) and the reject of job 1 are not done on v1: with `review-job` for v2 they
are done after the upgrade, see the next section. Steps 7 to 9 below are the v1 wording, kept as the record of the
rehearsal.

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
7. Review, as rehearsed on v1 on 2026-11-06 (read-only; `upgrade of bumm-guard-escrow2.testnet: ecd8a805… ->
   13425a04…, proved by passing scenario …`, `migration migrate: #[private] (src/lib.rs:118)`, `REVIEW PASSED`,
   exit 0, and the three-argument v1 `approve` command). The same command on the live v1 contract now exits 2
   (`runs swarm_jobs v1`): the review of job 0 is redone after the upgrade, step 5 of the next section.
   ```
   python3 -m bumm_guard review-job --jobs-contract bumm-guard-jobs.testnet --job 0 --crate tests/fixtures/near/escrow_v2 \
     --wasm evidence/wasm/escrow_v2.wasm --simulate-report evidence/escrow-upgrade-with-migrate-report-ci.json --network testnet
   ```
8. (On v2, next section.) **bob** runs the printed `approve … sign-as bumm-guard-bob.testnet …` command. Check:
   `get_job` says `Paid`, alice's balance is about 1 NEAR higher:
   ```
   near contract call-function as-read-only bumm-guard-jobs.testnet get_job json-args '{"id":0}' network-config testnet now
   ```
9. Second job, the blocked case. **demo** posts job 1 (same command as step 5, returns `1`); **alice** submits the
   same wasm with the code-only report (both done on 2026-11-06):
   ```
   near contract call-function as-transaction bumm-guard-jobs.testnet submit json-args '{"job_id":1,"wasm_sha256":"13425a042dec55eec364240e890a1645c7332f2898149353ebcb5830a5886013","report_sha256":"e7cfcfd8712d2dc31c1fb2c951894b367da9dc34a93376b45e1f61e249ac55c5"}' prepaid-gas '30 Tgas' attached-deposit '0 NEAR' sign-as bumm-guard-alice.testnet network-config testnet sign-with-keychain send
   ```
   The review of job 1 and bob's reject are step 6 of the next section.

Both reviews only read `bumm-guard-escrow2.testnet` (`view_code`); it stays on v1 until the video's upgrade.

## Upgrade v1 → v2 on testnet (the owner signs; BUMM Guard prints and reads)

The live contract on `bumm-guard-jobs.testnet` (v1, code `bcace583…`) holds alice's 0.5 NEAR budget and the two
submitted jobs. This section deploys v2 onto that account through the deploy gate, with `migrate` in the deploy
batch, and proves on the live state what the sandbox scenario proved: the jobs survive, then the v2 approve pays
job 0 and job 1 is rejected. Nothing below is done yet.

Files, all taken from the green CI run of the merge (artifacts `swarm_jobs_v2-wasm` and `simulate-report`), added to
the repository by the author after the merge and not by a local build (done on 2026-11-07 from CI run 42390541743 on `00bd558`): `evidence/wasm/swarm_jobs_v2.wasm` (its
sha256 goes into `evidence/wasm/SHA256SUMS`; written below as `<v2 sha256>`),
`evidence/swarm-jobs-upgrade-with-migrate-report-ci.json` (the `simulate-out/swarm-jobs-upgrade-with-migrate.json`
of that run: `wasm_sha256` `bcace583…`, `upgrade_wasm_sha256` `<v2 sha256>`, 1/1 scenario, 49/49 steps) and
`evidence/swarm-jobs-upgrade-code-only-report-ci.json` (`simulate-out/swarm-jobs-upgrade-code-only.json`, the
expected failure). The gate binds the proof to the exact pair (`bcace583…` → `<v2 sha256>`), so a wasm built
anywhere else is refused: the report proves another pair.

1. Gate without a proof (expected: `DEPLOY BLOCKED: account bumm-guard-jobs.testnet already runs code bcace583…;
   this is an upgrade and no passing upgrade scenario proves bcace583… -> <v2 sha256>`, exit 3, no command):
   ```
   python3 -m bumm_guard gate tests/fixtures/near/swarm_jobs_v2 --wasm evidence/wasm/swarm_jobs_v2.wasm \
     --account bumm-guard-jobs.testnet --network testnet
   ```
2. Gate with the code-only report (expected: the same `DEPLOY BLOCKED … no passing upgrade scenario proves …`,
   exit 3: the report names the right pair, but its scenario failed after the upgrade step, so it proves nothing):
   ```
   python3 -m bumm_guard gate tests/fixtures/near/swarm_jobs_v2 --wasm evidence/wasm/swarm_jobs_v2.wasm \
     --simulate-report evidence/swarm-jobs-upgrade-code-only-report-ci.json \
     --account bumm-guard-jobs.testnet --network testnet
   ```
3. Gate with the migrate report (expected: `upgrade of bumm-guard-jobs.testnet: bcace583… -> <v2 sha256>, proved
   by passing scenario 'Upgrade swarm_jobs v1 → v2 (upgrade with migrate()) …'`, `proved after upgrade (37
   step(s)): view job_count, view agent_count, …`, `migration migrate: #[private] (src/lib.rs:243)`, exit 0, and
   one deploy command). **owner** runs the printed command: one batch transaction, deploy plus `migrate`, signed by
   the contract account:
   ```
   python3 -m bumm_guard gate tests/fixtures/near/swarm_jobs_v2 --wasm evidence/wasm/swarm_jobs_v2.wasm \
     --simulate-report evidence/swarm-jobs-upgrade-with-migrate-report-ci.json \
     --account bumm-guard-jobs.testnet --network testnet --estimate-storage
   ```
   ```
   near contract deploy bumm-guard-jobs.testnet use-file evidence/wasm/swarm_jobs_v2.wasm with-init-call migrate json-args '{}' prepaid-gas '100 Tgas' attached-deposit '0 NEAR' network-config testnet sign-with-keychain send
   ```
4. Check the deploy and the migrated state (expected: on-chain code sha256 `<v2 sha256>` MATCH, both smoke views
   `ok`, exit 0). The expected `get_job` value is the live job 0 of `evidence/SWARM_JOBS_TESTNET.md` plus the two
   v2 fields:
   ```
   python3 -m bumm_guard gate --verify-deployed bumm-guard-jobs.testnet --wasm evidence/wasm/swarm_jobs_v2.wasm --network testnet \
     --smoke 'job_count=2' \
     --smoke 'get_job:{"id":0}={"id":0,"poster":"bumm-guard-demo.testnet","description":"upgrade bumm-guard-escrow2.testnet to escrow v2 keeping its deals","target_account":"bumm-guard-escrow2.testnet","reward":"1000000000000000000000000","status":"Submitted","agent":"bumm-guard-alice.testnet","wasm_sha256":"13425a042dec55eec364240e890a1645c7332f2898149353ebcb5830a5886013","report_sha256":"f305461fe47aa2d15d2361038b7ed010be51b2c53219fee1352eac59a24eb420","gate_report_sha256":null,"reject_reason":null,"reward_storage_fee":"0","scenario_sha256":null}'
   ```
   Also worth reading: `get_agent {"account":"bumm-guard-alice.testnet"}` (budget `500000000000000000000000`,
   `jobs_in_review` 2, `storage_cost` `"0"`) and `get_job {"id":1}` (`Submitted`, `report_sha256` `e7cfcfd8…`).
5. The v2 approve of job 0. Review (read-only; `bumm-guard-escrow2.testnet` must still run escrow v1, otherwise the
   verdict is `NO CHANGE` → reject; expected: `scenario_sha256 on the job: null (the poster accepts any scenario)`,
   `upgrade of bumm-guard-escrow2.testnet: ecd8a805… -> 13425a04…, proved by passing scenario …`, `migration
   migrate: #[private] (src/lib.rs:118)`, `REVIEW PASSED`, exit 0, and the five-argument `approve` with
   `agent` `bumm-guard-alice.testnet`, the two submitted hashes and `gate_report_sha256` = sha256 of
   `.bumm-guard/review-0.json`, which must be kept):
   ```
   python3 -m bumm_guard review-job --jobs-contract bumm-guard-jobs.testnet --job 0 --crate tests/fixtures/near/escrow_v2 \
     --wasm evidence/wasm/escrow_v2.wasm --simulate-report evidence/escrow-upgrade-with-migrate-report-ci.json --network testnet
   ```
   **bob** runs the printed `approve … sign-as bumm-guard-bob.testnet …` command (100 Tgas, 0 NEAR). Check: `get_job
   {"id":0}` says `Paid` with that `gate_report_sha256`, `get_agent` says `jobs_paid` 1 and `jobs_in_review` 1,
   alice's balance is about 1 NEAR higher:
   ```
   near contract call-function as-read-only bumm-guard-jobs.testnet get_job json-args '{"id":0}' network-config testnet now
   ```
6. The reject of job 1 (expected: `REVIEW BLOCKED: DEPLOY BLOCKED: account bumm-guard-escrow2.testnet already runs
   code ecd8a805…; this is an upgrade and no passing upgrade scenario proves …`, exit 3, a `reject` command):
   ```
   python3 -m bumm_guard review-job --jobs-contract bumm-guard-jobs.testnet --job 1 --crate tests/fixtures/near/escrow_v2 \
     --wasm evidence/wasm/escrow_v2.wasm --simulate-report evidence/escrow-upgrade-code-only-report-ci.json --network testnet
   ```
   Two commands are printed: `reject … "close":false` (job `Rejected`, alice may resubmit) and the `"close":true`
   variant (job `Cancelled`, demo's 1 NEAR comes back in the same transaction). **bob** runs the first one;
   `get_job {"id":1}` says `Rejected` with that reason, `get_agent` says `jobs_in_review` 0. Optionally **demo**
   takes the reward back (the record stays, `Cancelled`):
   ```
   near contract call-function as-transaction bumm-guard-jobs.testnet cancel json-args '{"job_id":1}' prepaid-gas '30 Tgas' attached-deposit '0 NEAR' sign-as bumm-guard-demo.testnet network-config testnet sign-with-keychain send
   ```
7. Optional, the v2-only methods on the live contract: **demo** posts a job with a pinned scenario and a 1 NEAR
   reward (the fee is about 0.003 NEAR; the excess of the 1.1 NEAR comes back in the same call; returns `2`), and
   **alice** leaves with her whole 0.5 NEAR once nothing of hers is under review:
   ```
   near contract call-function as-transaction bumm-guard-jobs.testnet post_job json-args '{"description":"upgrade bumm-guard-escrow2.testnet to escrow v2 keeping its deals","target_account":"bumm-guard-escrow2.testnet","reward":"1000000000000000000000000","scenario_sha256":"7e7d87ce7e6b46feeb6ac43ac345dd2f1c906c6b63c683e12f322f72871fe0bd"}' prepaid-gas '30 Tgas' attached-deposit '1.1 NEAR' sign-as bumm-guard-demo.testnet network-config testnet sign-with-keychain send
   near contract call-function as-transaction bumm-guard-jobs.testnet leave_swarm json-args '{}' prepaid-gas '30 Tgas' attached-deposit '0 NEAR' sign-as bumm-guard-alice.testnet network-config testnet sign-with-keychain send
   ```
   (`7e7d87ce…` is the `scenario_sha256` of `scenarios/upgrade/escrow_v1_to_v2_with_migrate.json` as recorded in
   `evidence/escrow-upgrade-with-migrate-report-ci.json`, so a submission with that report would pass the pin.)

Every command above with `sign-with-keychain send` is the owner's; the gate and the review only read the chain.
The transactions and the checks after each go into `evidence/SWARM_JOBS_TESTNET.md` when they are done.
