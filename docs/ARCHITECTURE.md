# BUMM Guard — architecture and what was built (state on 2026-11-08)

This is the review document for the project: what the thing is, how the parts fit, what each part verifiably does, what evidence exists, and what is deliberately not claimed. It is written for a reader who has not seen the repository. Every number here is either in a committed file (named) or in a CI run (numbered). Facts and hypotheses are kept apart: a hypothesis is marked *(hypothesis)*.

Repository: `GodBooster/bumm-guard`. The current commit and the full history are in `git log` (79 commits on six working days between 2026-10-30 and 2026-11-06 at the time of writing). License MIT. Written during the NEAR hackathon window with Claude Code; BUMM (the author's Solana builder) is prior work and is not in this repository.

## 1. The one sentence and the one failure

**BUMM Guard checks in a sandbox that a NEAR contract upgrade works on state the old code wrote, binds that proof to the exact pair of code hashes (on-chain sha256 → candidate sha256), and only then hands an agent or a human the deploy command.**

The failure it targets is shown here for NEAR Borsh state (the general problem, new code reading state written by old code, exists on any chain with upgradeable contracts; Borsh deserialization of a changed struct is its NEAR shape). Contract state is Borsh-serialised into the account's key-value store. When new code with a different struct layout is deployed over an account that holds state written by the old layout, the deploy transaction *succeeds*, and then:

- if the **root struct** changed, every method panics with `Cannot deserialize the contract state.` (near-sdk reads the root on every call);
- if only a **record inside a collection** changed (`IterableMap<u64, Deal>`, etc.), the root still loads, "shallow" views keep answering, and every method that reads a record panics with `Cannot deserialize element`.

The tests that are usually written do not see this — unit tests of the new code, `cargo near` builds, fresh-deploy integration tests, a post-deploy smoke view — because none of them runs the new code against state written by the old layout. A dedicated migration unit test can (`escrow_v2/src/lib.rs` has one that writes v1 state and migrates it); the problem is that nothing requires such a test to exist, and a shallow smoke view can pass without it. The correct NEAR idiom is a `migrate()` method with `#[init(ignore_state)]` and `#[private]`, called in the same batch as `DeployContract` so a failing migration rolls the deploy back. In the one pilot below, the agent that passed every fresh-deploy check had written no migration; one run says the failure is real, not how often agents make it.

Everything in the repository exists to make this failure visible before deploy and to refuse the deploy until it is proven absent for the exact pair of code hashes involved. The second part, guarded jobs for agent swarms (§2.7), puts the same gate between an agent's work and its payment.

## 2. Components

```
                 ┌──────────────────────────────────────────────────────────┐
                 │  bumm_guard  (Python 3.12+, stdlib only, about 4 100 lines)│
                 │                                                          │
  .rs sources ──►│  scanner ─► rulepack (3 rules) ─► fix (2 fixers)          │
                 │                                                          │
  scenario.json─►│  simulate ──spawns──► bumm-sim (Rust, near-workspaces)   │──► simulate-report.json
                 │                        └─► near-sandbox (local chain)    │
                 │                                                          │
  wasm + account►│  gate ─► storage (RPC) ─► verify (RPC view_code/account) │──► gate-report.json
                 │          └─ find_upgrade_proof / migration_guard          │    + printed deploy cmd
                 │                                                          │
  job on chain ─►│  review (get_job, get_reviewer) ─► gate (dry run)        │──► review-<job>.json
                 │                                                          │    + printed approve/reject
  agent (stdio)─►│  mcp_server (7 tools, FastMCP)                            │
                 └──────────────────────────────────────────────────────────┘
                 contracts/sim-runner  (bumm-sim, Rust 1.99, near-workspaces 0.24)
                 contracts/sandbox     (near-workspaces test for BG-NEAR-001)
                 tests/fixtures/near   (14 crates, rustc 1.85.0 pinned)
                 tests/fixtures/near_official (near-sdk-rs examples/versioned, unmodified, + 2 old variants; built in CI from the pinned commit)
                 scenarios/            (24 JSON scenarios: 9 upgrades, 3 for swarm_jobs v1, 4 for swarm_jobs v2)
                 ui/                   (static ABI panel, about 840 lines JS/HTML/CSS)
                 clients/claude-plugin (Claude Code plugin + skill)
                 .github/workflows     (ci.yml 5 jobs, evaluate.yml dispatch)
                 evidence/             (15 JSON reports, 5 markdown records, 9 wasm files with SHA256SUMS, the review report of the paid job)
```

### 2.1 Static rule-pack (`bumm_guard/scanner.py`, `rulepack/`, `fix/`)

Three NEAR-specific defect classes, detected at regex/brace level over `.rs` text (no parser, no type information):

| Rule | Detects | Fix |
|---|---|---|
| BG-NEAR-001 `non-private-callback` | a method scheduled via `.then(Self::ext(..).cb(..))` that has no `#[private]`: anyone can call it with a forged promise result | adds `#[private]` |
| BG-NEAR-002 `callback-ignores-failure` | state mutated before a cross-contract call, callback has no failure branch (no rollback) | suggested patch only (a correct rollback is semantic) |
| BG-NEAR-003 `signer-auth` | authorisation by `env::signer_account_id()` instead of `predecessor_account_id()` (phishable through an intermediate contract) | rewrites to `predecessor_account_id()` |

CLI: `scan` (exit 1 on high/critical, 2 on usage error), `rules`, `fix` (dry run by default). Each rule has a `*_vuln`/`*_safe` fixture pair; the full fixture scan gives exactly 3 findings in 12 files, asserted in CI. One run over 290 real `.rs` files from near-sdk examples/standards is recorded in `RULEPACK_REPORT.md` (5 + 0 + 7 findings, two true-positive highs); it is a sanity check, not a false-positive rate.

### 2.2 Scenario runner (`bumm_guard/simulate.py` + `contracts/sim-runner`)

A scenario is a JSON file (format: `docs/FORMATS.md` §2): accounts with balances, a contract wasm with an init call, and a linear list of steps of kind `call`, `view`, `balance` and — the addition of this project — **`upgrade`** (§2a): deploy a second wasm to the same account, optionally with a `migrate` call in the same batch, then keep running steps on the upgraded state. Expectations: `ok`, `fail_contains`, `equals`, `contains`, `at_least/at_most`.

Python validates the file (`--validate-only` runs nothing and works on any OS), hashes the scenario and the wasm files, spawns `bumm-sim`, and renders a report (`FORMATS.md` §3): per step `passed`, `expected`, `actual`, and for failures the panic text in `error_detail` (view steps included; that is what lets CI assert the exact failure string). `bumm-sim` is a Rust binary on `near-workspaces 0.24` that starts a local `near-sandbox`; Linux only (no local real run on macOS x86_64, so every real run in this project happened in GitHub Actions).

Two contract pairs are shipped as fixtures, written by the author to exhibit the two failure shapes:

| Pair | Change | Code-only upgrade in sandbox | With `migrate()` |
|---|---|---|---|
| `event_registration` (N06) → `event_registration_waitlist` (N10) | root struct gains waitlist fields | FAIL 4/16: every call after the upgrade → `Cannot deserialize the contract state.` | PASS 16/16 |
| `escrow_v1` → `escrow_v2` | `Deal` record gains `status`, `created_at_ms` in place of `released`; root unchanged | FAIL 9/17: `deal_count` *passes* (answers 3), every `get_deal`/`release`/`refund` → `Cannot deserialize element` | PASS 33/33 |

CI asserts the failing shape exactly (which steps pass, which fail, with which string, no infrastructure error), so a different failure turns the job red. The reports are committed: `evidence/upgrade-*-report-ci.json`, `evidence/escrow-upgrade-*-report-ci.json`. A third fixture contract, `swarm_jobs` (§2.7), has three fresh-deploy scenarios (81 steps) and its report `evidence/swarm-jobs-report-ci.json`.

Fixtures are pinned to `rustc 1.85.0` via `rust-toolchain.toml` (near-sdk 5.17.2 wasm does not link on 1.99); `bumm-sim` builds on stable 1.99. Because of the pin and dev-dependency MSRVs, fixtures are `cargo check`ed in CI rather than `cargo test`ed, except `swarm_jobs`, whose 28 unit tests CI runs with a stable rustc.

### 2.3 Deploy gate (`bumm_guard/gate.py`, `verify.py`, `storage.py`)

`gate <crate> --wasm W --account A --network testnet [--simulate-report R] [--scenario-sha256 HEX] [--estimate-storage]`. It is **pipeline control**: it decides whether to print a deploy command, and by default never runs one. Decision order (all read-only RPC):

1. Scan the crate; any high/critical finding → `DEPLOY BLOCKED`, exit 3.
2. If a simulate report is given: it must be for this exact wasm (`wasm_sha256` in the report = sha256 of `--wasm`), else blocked. Binding by hash is what stops "I tested *a* build".
3. `view_code(A)`:
   - cannot be read (RPC failure) → blocked, exit 3: without it the gate cannot tell a fresh deploy from an upgrade.
   - no code → `view_account`: if the account uses a **global contract** (NEP-591, `global_contract_hash` / `global_contract_account_id`) → blocked (out of scope, refused rather than treated as empty); else `fresh deploy`, no upgrade proof required (a report with upgrade scenarios only triggers a warning).
   - code hash = candidate hash → `NO CHANGE: account … already runs this exact wasm (…); nothing to deploy`: exit 0, no command, `deploy.status: "no-change"` in the report. (`review-job`, §2.7, turns this into a reject: a paid job must change something.)
   - code hash ≠ candidate → **this is an upgrade**. `find_upgrade_proof` searches the report for a *passing* scenario whose `upgrade` step went from exactly the on-chain hash to exactly the candidate hash, whose upgrade transaction succeeded (`expected: "ok"`, `actual.status: success`; a rolled-back upgrade proves nothing), recorded the deployed `code_sha256`, and has exactly one `upgrade` step, at least one successful `call` after it and no stored-state read failure (`Cannot deserialize`) after it; when the report carries the runner's state digests (`state_after_init`, the upgrade step's `state_before`), the two must differ, so a view method sent as a transaction does not count as state written before the upgrade (a report without them, or with a malformed one, is judged by the successful `call` alone); a report whose access probe succeeded is not a proof (the probe is the migration call sent by an account that is neither the contract nor a scenario account, to the new code on the state before the migration, in a second sandbox of its own); with `--scenario-sha256` the scenario file must also carry that hash. None → blocked, with a structured `upgrade_check` (`status`: `no-report` / `no-proof` / `report-proves-other-pair` / `proof-upgrade-failed`, plus every pair the report does prove with its `upgrade_status`). A proof lists its steps after the upgrade (`proved after upgrade: …`, `upgrade.post_upgrade_steps`); only views there is refused. A failing scenario for the same pair and migration blocks (it is not outvoted); the code-only negative check next to a with-migrate proof is a note. Migrate args that name a scenario alias (`alice`) block: the sandbox resolved them, testnet would not.
4. If the proof carries a `migrate` call: `migration_guard` finds the method in the candidate crate's sources and requires `#[private]` (not on an `#[init]` method of a crate that builds with near-sdk before 5.18.0: near-sdk generates no caller check there, status `private-unenforced`) or a `predecessor_account_id()` check in the first 15 lines of its body whose rejection branch is recognised (`require!`/`assert!` family, or an `if`/`match` with `panic!`/`env::panic_str`/`return`/`unreachable!` in its body). Unprotected, or `owner-check-unknown` (a comparison without a recognised rejection, or a tautology) → blocked (anyone could call `migrate` after the deploy). Not found → blocked (the gate cannot vouch for a wasm built elsewhere). Ambiguous (several definitions) → blocked. If the report's access probe was refused (status `failure`, whatever the text), the wasm itself rejected another caller on the same code, state and arguments as the real upgrade, and `owner-check-unknown`, `unprotected`, `ambiguous` and `private-unenforced` do not block (`not-found` still does); `private-unenforced` without a refused probe blocks; a probe that did not run counts as absent, and a report without a probe (older `bumm-sim`) is decided by the source check alone.
5. `--estimate-storage`: reads `EXPERIMENTAL_protocol_config` for the per-byte price and the account's balance/`storage_usage`; prints the NEAR that the code will lock (a locked balance, not a fee).
6. Pass: writes `.bumm-guard/gate-report.json` (`FORMATS.md` §4: scan summary, wasm/abi hashes, simulate binding, `upgrade` block, `migration_guard`, storage, account, `deploy`) and prints exactly one `near contract deploy …` command — `without-init-call` for a fresh deploy, `with-init-call <migrate> json-args '…' prepaid-gas '…'` for a proven upgrade, i.e. deploy + migrate in one batch.

Text taken from the simulate report (scenario names, hashes, step kinds and methods, statuses) is supplied by whoever made the report, so before it is printed or recorded it goes through one cleaning function (`bumm_guard/sanitize.py`: control characters removed, 200 characters at most); a scenario name cannot break a line or draw a fake verdict in a terminal. A recorded migration method or gas that is not plain text is not accepted as a proof, because it would be repeated in the printed command.

`--execute` runs the printed command; it is off by default, refuses `--network mainnet` (exit 2), requires `near` on PATH, and is not exposed through MCP. The repository ships no key and no account.

`gate --verify-deployed A --wasm W --smoke M[:ARGS][=EXPECTED]…` is the post-deploy half: `view_code` hash vs artifact hash (`MATCH`/`MISMATCH`), then the given view calls, each compared with its `=EXPECTED` JSON when one is given; exit 5 on failure. Reports are written to the same file.

### 2.4 MCP server and agent client (`bumm_guard/mcp_server.py`, `clients/claude-plugin`, `.mcp.json`)

`bumm-guard-mcp` (installed with the `mcp` extra; FastMCP over stdio) exposes seven tools: `list_rules`, `scan_contract`, `fix_contract`, `gate_deploy`, `estimate_storage`, `verify_deployed`, `review_job`. `gate_deploy` returns the same decision as the CLI as JSON (`passed`, `blocked`, `upgrade`, `upgrade_check`, `deploy`, `deploy_command`, `messages`, `note`) and has no execute path; `review_job` (§2.7) returns the approve or reject command as text and has no sign or send path. The repository root carries a project-scope `.mcp.json`, so a Claude Code session opened in the repo sees the server without configuration; `clients/claude-plugin` is the same server as a plugin with a skill, `near-deploy-gate`, that tells the agent the order (scan → simulate → gate → hand the command to the human; if no runner is available where the agent runs, ask the human for a CI report rather than skip the step). MCP is tested over a real stdio subprocess (`tests/test_mcp.py`, 14 tests, skipped without the `mcp` package; CI runs them).

A dry run with a real agent (Claude Code desktop, 2026-11-03, `docs/DEMO_AGENT.md`): given the prompt "upgrade this account to escrow v2, the proof is in `evidence/`", the agent discovered the six tools of that day, called `scan_contract`, then `gate_deploy` three times (no report → blocked; code-only report → blocked "did not pass"; migrate report → passed with the command), and stopped with the command for the human. Two minutes. The structured `upgrade_check` was added after this run because the agent's first blocked result did not say *which* pairs the report did prove.

### 2.5 ABI panel (`ui/`)

A static page (no backend) that loads a contract ABI and calls view methods through public RPC; the change-call path through a wallet is written but **not verified**. It is an interaction panel, tested with `node --test` (18 tests), and is not part of the main thesis.

### 2.6 CI (`.github/workflows/`)

`ci.yml`, on every push and PR, 5 jobs, `permissions: contents: read`, timeouts set; about 3 minutes with a warm cache (2 min 52 s on run 42281448487, commit `88ed4dd`, the latest run checked while writing this):

1. `rule-pack tests (python)`: the Python unit tests (290 in the repository on 2026-11-06; 14 skipped outside the MCP venv, 3 in CI), the fixture scan assertion, the migration-guard assertion on the real fixtures.
2. `ABI panel tests (node)`.
3. `fixtures compile (cargo check, wasm32)`: `--workspace --locked`, host and `wasm32-unknown-unknown`, pinned toolchain; the `swarm_jobs` unit tests (28) with a stable rustc.
4. `sandbox tests (near-workspaces, linux only)`: `contracts/sandbox` runs the BG-NEAR-001 exploit against the vulnerable fixture and its absence against the fixed one; the 18 `bumm-sim` lib tests and the upgrade sandbox test.
5. `scenario runner (bumm-sim on near-sandbox, linux only)`: builds N06, N10, escrow v1, v2, `swarm_jobs` and `swarm_jobs_v2` (rustc 1.85.0) and `bumm-sim` (1.99.1); runs the N06, escrow, `swarm_jobs` and `swarm_jobs_v2` fresh scenarios; runs the gate against each report (fresh deploy, binding by hash); runs the six upgrade scenarios, three expected to pass, three expected to fail *after* the upgrade step with the exact panic string; checks the six wasm files it built against `evidence/wasm/SHA256SUMS` (hash guard); then builds the official near-sdk-rs example `examples/versioned` from its pinned commit with our two old variants (`scripts/build_official_example.sh`, `tests/fixtures/near_official/NOTICE.md`), runs its two code-only upgrade scenarios (V0 era expected to pass without `migrate`, plain struct expected to fail after the upgrade step) and checks those three wasm files too; uploads `simulate-report` and the nine wasm files as artifacts. **The wasm that CI simulated is the wasm the gate later accepts**: a local rebuild differs in bytes, so the artifact (now also committed in `evidence/wasm/`) is the deployable.

`evaluate.yml` (manual dispatch; inputs `crate_dir`, `scenarios`, `upgrade=candidate`, `label`, `toolchain`) builds any crate from any branch and scores it with the same runner. It scored the A/B pilot.

### 2.7 Guarded jobs for agent swarms (`tests/fixtures/near/swarm_jobs`, `bumm_guard/review.py`)

The second use of the gate, added on 2026-11-06: a minimal onchain job escrow in which an agent's reward is paid only after the gate's review of its work passed. A poster locks a reward in a job; a registered agent submits two hashes (the wasm it built, the scenario report that proves it); `bumm-guard review-job` (and the MCP tool `review_job`) reads the job with view calls, checks that the local files are exactly the submitted ones, runs the gate in dry-run mode on them for the job's target account, writes `.bumm-guard/review-<job>.json` (`FORMATS.md` §7) and prints one command for the reviewer to sign: `approve` (bound to the wasm hash and to the hash of the review report) or `reject` with the gate's reason. The gate's `NO CHANGE` (the target already runs the submitted wasm) is a reject here, and an RPC failure gives no command at all (exit 2). The review says in one line, and in the report (`evidence_origin`), that the report and the crate came with the submission and were not re-executed. The contract (near-sdk 5.17.2, 28 unit tests, 3 sandbox scenarios with 81 steps on every push) is deployed on testnet through the gate, with an agent joined and two jobs submitted; on 2026-11-07 the contract was upgraded live to v2 through the gate with both jobs in its state, then one job was approved and paid and the other rejected, closed and refunded (`evidence/SWARM_JOBS_TESTNET.md`). What it is and what it is not — one trusted reviewer, no inference, no agent runtime, agent-supplied evidence, storage paid by the contract — is in `docs/SWARM_JOBS.md`, section "Honest limits".

## 3. Evidence on a live network (testnet, all signed by the owner)

Nothing in the repository holds or uses a key. In every case below the gate printed a command and the owner ran it with `near-cli-rs` from their own keychain. All transactions are linked in `evidence/E2E_TESTNET.md`, `evidence/UPGRADE_TESTNET.md` and `evidence/SWARM_JOBS_TESTNET.md`.

| Date | Account | What happened | Result |
|---|---|---|---|
| 2026-11-02 | `bumm-guard-demo.testnet` | fresh deploy of CI-built N06 through the gate (simulate 3/3 → gate → storage estimate 1.81 NEAR → deploy) | `--verify-deployed`: code hash = artifact, MATCH |
| 2026-11-03 | same | owner `register`s (state exists); gate for N10: no proof → BLOCKED; code-only report → BLOCKED; migrate report → passed, printed deploy+`migrate` batch; owner ran it (tx `8kvdZu87…`) | MATCH on `8526195a…`; the registration survived; `get_waitlist` answers |
| 2026-11-04 | `bumm-guard-escrow.testnet` | fresh v1 through the gate; `new`; one deal of 1 NEAR from `bumm-guard-demo`; gate for v2: BLOCKED / BLOCKED "did not pass" / passed with `migration migrate: #[private] (src/lib.rs:118)`; owner ran it (tx `DPgBviLp…`) | MATCH on `13425a04…`; `get_deal(0)` returns the v1 deal in v2 shape (`status: "Funded"`, `created_at_ms: 0`) |
| 2026-11-04 | `bumm-guard-demo.testnet` + `alice`, `bob` | waitlist on the migrated N10: alice takes the last seat, bob lands on the waitlist, alice cancels → bob promoted | each state read from the archival RPC at that receipt's block (276189585/596/606) |
| 2026-11-04 | `bumm-guard-escrow2.testnet` | prepared on v1 with one deal for the video recording; the three gate calls dry-run to the scripted outputs | not upgraded yet, on purpose |
| 2026-11-06 | `bumm-guard-jobs.testnet` | `swarm_jobs` deployed through the gate (fresh deploy, CI report bound by hash), `new` with reviewer `bumm-guard-bob`; `bumm-guard-alice` joined with 0.5 NEAR; `bumm-guard-demo` posted two jobs of 1 NEAR targeting `escrow2`; alice submitted the migrate report hash to job 0 and the code-only report hash to job 1; both reviews rehearsed read-only (job 1 BLOCKED, job 0 PASSED) | `--verify-deployed` MATCH on `bcace583…`; nothing approved, rejected or paid on that day (approve, payment and reject-close followed on 2026-11-07 on v2) |

## 4. The pilot (one run per arm, not statistics)

`evidence/AB_PILOT_N10.md`: the same task (add a waitlist to N06), same model, same frozen starter, one run per arm. **A1 had no BUMM** (Rust + official NEAR docs, 30-minute box). **B1 had the BUMM Guard of that day** (scan, scenario format, `simulate` through the CI `evaluate` workflow; 45-minute box because the CI build is slow) — and that day the tool had **no `upgrade` step and no upgrade proof in the gate**; they were built *after* this pilot, because of it. Fresh deploy: both arms 3/3 scenarios, 35/35 hidden steps; A1 took about 4.5 minutes, B1 about 15. Then the author scored both wasm files again, without re-running the agents, on the hidden upgrade scenario: A1, which had written `migrate()` on its own, PASS 16/16; B1, which had passed every fresh-deploy check, FAIL (no migration; the batch reverted). What the repository claims from this: the failure is real, and a fresh-deploy check cannot see it; the upgrade step and the gate's proof requirement are the response to that finding. What it does not claim: that BUMM Guard saved time (in this pilot it cost about 10 minutes and did not prevent the failure, because the check did not exist yet), or how often agents make the mistake (n = 1). A second pilot with both arms told the contract is live, and B with the current tool, has not been run.

## 5. Formats as the contract between parts

`docs/FORMATS.md` is the normative file: §1 build artifact, §2 scenario (§2a upgrade step), §3 scenario result, §4 gate report, §5 panel inputs, §6 independent acceptance for N06/N10, §7 review report. Every producer and consumer in the repository (Python, Rust runner, CI assertions, MCP, the `swarm_jobs` contract's hash arguments) reads these shapes; the sha256 fields are the glue (scenario file, old wasm, new wasm, report binding, deployed code, submitted wasm and report, review report).

## 6. Honest limits (also in README "Honest limits" and `docs/SWARM_JOBS.md`)

- Detection, fixes and the source check of the migration guard are regex/brace level (a report from the current runner adds the access probe, which runs on the wasm, but only for the migration method named in the scenario): no macro expansion, no name resolution; helper-delegated checks (`self.assert_owner()`), DAO or access-key policies are not recognised, and a `predecessor_account_id()` comparison without a recognised rejection branch is `owner-check-unknown` — the gate then blocks a possibly safe contract (add a `require!(env::predecessor_account_id() == env::current_account_id(), ..)` first line, or `#[private]` with near-sdk 5.18.0 or later, to pass).
- The sandbox state is the scenario's own transactions, not a copy of live state; the proof covers the listed post-upgrade steps on that scenario-made state (printed and recorded as `post_upgrade_steps`), and a scenario with only views after the upgrade is refused.
- The `upgrade` step models a full-access-key batch by the contract account; not DAO-controlled upgrades; a cross-contract call inside `migrate` is not rolled back with a failing batch.
- The gate does not build the wasm and does not check that the wasm matches the crate it scans; it records both hashes. CI builds are byte-identical across the runs cited, a local build on another machine is not; the committed `evidence/wasm/` files are the ones the proofs are bound to.
- Real runs on Linux or macOS arm64 only (Docker elsewhere); the author's own contracts only (three upgrade pairs); no third-party contract through the runner; wallet path of the panel unverified; no mainnet.
- Guarded jobs: the review runs off-chain and one trusted reviewer signs; the report and the crate are the agent's files, checked by hash and not re-executed, so the reviewer must take the report from a run they control; `approve` (v2) binds the agent, the wasm hash, the report hash and the hash of the review report; a submission is bound to one agent only when the poster names `assigned_agent`; in v2 the poster pays the storage of each job record and an agent's first deposit pays its own. Shown on testnet through payment (job 0 paid) and a reject that closed and refunded job 1 (2026-11-07).
- The gate is not an on-chain guarantee: anyone with shell access can run `near contract deploy` by hand. It is the step a coding agent's workflow goes through.

## 7. Repository map

| Path | What |
|---|---|
| `bumm_guard/` | Python package: `cli.py`, `gate.py`, `simulate.py`, `mcp_server.py`, `review.py`, `verify.py`, `storage.py`, `sanitize.py`, `scanner.py`, `model.py`, `rulepack/near/*`, `fix/*` (about 4 100 lines in all) |
| `contracts/sim-runner/` | `bumm-sim` (Rust): scenario execution on near-workspaces; `tests/upgrade_sandbox.rs` |
| `contracts/sandbox/` | near-workspaces test for BG-NEAR-001 |
| `tests/` | 11 Python test modules, 380 tests; `tests/fixtures/near/` 14 crates (d1–d3 vuln/safe, proxy, N06, N10, escrow v1, v2, v3, `swarm_jobs` v1, v2); `tests/fixtures/near_official/` the official near-sdk-rs example `examples/versioned` (unmodified, MIT OR Apache-2.0) and our two old variants of it |
| `scenarios/` | 23 files: `n06/` (3), `n10/` (3 acceptance), `escrow/` (2), `upgrade/` (8: three pairs of ours with and without `migrate`, two code-only upgrades to the official near-sdk-rs example), `swarm_jobs/` (3), `swarm_jobs_v2/` (4) |
| `evidence/` | CI reports (11 JSON), testnet gate reports (3 JSON), the review report of the paid job, `wasm/` (9 files + `SHA256SUMS`), `E2E_TESTNET.md`, `UPGRADE_TESTNET.md`, `SWARM_JOBS_TESTNET.md`, `AB_PILOT_N10.md`, `AB_TASK_N10.md` |
| `docs/` | `FORMATS.md` (normative), `SWARM_JOBS.md` (guarded jobs), `DEMO_AGENT.md` (agent rehearsal), `SUBMISSION.md` (form text, X post, video script), this file |
| `clients/claude-plugin/` | plugin manifest, `.mcp.json`, skill `near-deploy-gate` |
| `ui/` | ABI panel + node tests |
| `.github/workflows/` | `ci.yml`, `evaluate.yml` |
| `README.md` | thesis, measured section, "Prove an upgrade (do X, see Y)", testnet, try-it, MCP, guarded jobs, gate, rules, runner, panel, testing, limits |
| `*_REPORT.md` (root) | dated build reports of single parts (rule-pack, MCP, simulate, upgrade, gate); their numbers are of their dates, the README is current |

## 8. Questions a reviewer should push on

These are the places where the author expects disagreement or wants a second opinion.

1. **Scope of the proof.** The proof is "these steps pass on state the scenario itself created". Is it worth adding a mode that seeds the sandbox from a live account's state (RPC `view_state` → sandbox patch), or does that over-promise? *(hypothesis: a `view_state` seed for small contracts is one day of work and changes the claim from "on the scenario's state" to "on a copy of the live state at block N")*.
2. **Migration guard false blocks.** The regex guard blocks `migrate` protected through a helper or a DAO. Is a documented false block acceptable at this stage, or should "not recognised" be softened? ("Not found" is a block too.)
3. **Agent integration surface.** MCP (7 tools) + a Claude Code skill. Cursor/Codex configs are not verified. Is a GitHub Action (`uses: bumm-guard/gate@v0`) a more natural surface for CI-centred teams than MCP?
4. **Evidence hygiene.** CI wasm artifacts are the deployables because local rebuilds are not byte-identical. Should reproducible builds (`cargo near build --reproducible-wasm`, SourceScan) be the next step so the gate can also check "this wasm came from this source"? For guarded jobs this is the gap between "the submitted files" and "the agent's work".
5. **Agent-supplied evidence.** In a guarded job the scenario report comes from the agent. Should the contract or the reviewer require the report to come from a run the reviewer controls (a CI run id, a runner signature), or is "the reviewer must run it themselves" an acceptable rule for now?
6. **Pilot size.** One run per arm. A second pilot where both agents are told the contract is live was planned and deliberately not run before submission. Worth running after?
7. **What is NEAR-specific vs portable.** The failure (Borsh state + no migration) and the fix (`#[init(ignore_state)]` + batch) are NEAR's. The gate shape (bind proof to hashes, refuse without it, print don't execute) is portable to any chain with upgradeable code. Is the NEAR framing the right one for the hackathon?

## 9. Timeline (from `git log`, commit dates)

- 10-30 — rule-pack (3 detectors), fixtures d1–d3, CLI, first CI; fix-loop and deploy gate (v0.2.0); MCP server + plugin (v0.3.0); ABI panel; shared formats; scenario runner (`bumm-sim`) with the N06 fixture.
- 11-02 — gate binding to simulate report by hash, storage estimate, `--verify-deployed`; N10 acceptance scenarios written before the implementation; first testnet deploy through the gate; `evaluate.yml`; A/B pilot run and recorded; `upgrade` step (FORMATS §2a) and the upgrade proof in the gate; near-workspaces sandbox test for BG-NEAR-001; rustc 1.85.0 pin; README for judges.
- 11-03 — live N06→N10 upgrade on testnet; an architecture review and its fixes merged the same day: docs synced, panic text in reports, non-empty proof, global-contract refusal, negative CI evidence committed, escrow pair, `evaluate.yml` toolchain input, migration guard, agent rehearsal with structured `upgrade_check` and project `.mcp.json`; CI hardening.
- 11-04 — live escrow v1→v2 upgrade; waitlist on testnet; video script v2; account for the recording; gate hardening (NO CHANGE, post-upgrade steps listed, proof needs a successful upgrade transaction, alias args blocked, owner-check recognition, smoke expectations); first version of this document.
- 11-05 — submission texts aligned with the hash-bound thesis.
- 11-06 — the four proven wasm files committed with `SHA256SUMS` and a CI hash guard; guarded jobs for agent swarms (`swarm_jobs` contract, scenarios, `review-job`, MCP `review_job`, `docs/SWARM_JOBS.md`), deployed on testnet with two jobs submitted; review fixes (NO CHANGE is a reject, RPC failure is not a verdict, report text cleaned before printing, evidence origin stated); documentation sweep; this document updated.
- 11-08 — third-party evidence: the official near-sdk-rs example `examples/versioned` (commit `7476cbe4`, tag `near-sdk-v5.17.2`, unmodified) as the new code of two sandbox-only code-only upgrades: from its V0 era it passes 15/15 without `migrate` and the gate accepts it as a proof; from a plain-struct build it fails 5/15 with `Cannot deserialize the contract state.` and the gate refuses it.
