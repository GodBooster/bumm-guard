# BUMM Guard

**BUMM Guard proves that a change to a NEAR contract works on state written by the code that is already deployed, before an agent or a human deploys it: the scenario's state, bound to the exact pair of code hashes (on-chain sha256 → candidate sha256).** In NEAR, redeploying new code over a contract whose stored state has a different layout, without a migration, leaves a contract that panics on every call (`Cannot deserialize the contract state.`). Unit tests do not see this, and neither do scenarios that deploy fresh. The `upgrade` step of the scenario runner deploys the old wasm in a local near-sandbox, puts real state into it with transactions, deploys the new wasm on the same account (with or without a `migrate` call) and runs the checks against the upgraded state. Around that sits the rest of the "after the contract compiles" path for coding agents on NEAR (near-sdk-rs 5.x): a static-analysis rule-pack with deterministic fixers, scenarios in a sandbox, a deploy gate, and an ABI interaction panel. Contract generation is deliberately left to the agent. Python 3.12+, standard library only, for the CLI; an optional MCP server (`bumm-guard-mcp`) exposes scan/fix/gate/storage/verify to Claude Code, Cursor, Codex or any MCP client. Track: Open Innovation (primary), NEAR HACKATHON; the rule-pack also fits AI Security & Defense.

```
scan -> fix -> simulate scenarios -> prove the upgrade of live state -> gate -> deploy -> ABI panel
```

**Deploy gate:** if the target account already runs code, the gate treats the deploy as an upgrade and refuses to print a command until a passing upgrade scenario proves exactly that pair (on-chain code sha256 → candidate wasm sha256); the printed command then repeats the proven `migrate` call in one batch; an account that already runs the candidate wasm gets `NO CHANGE` and no command. It does not execute the command by default; `--execute` is CLI-only, refuses anything but `--network testnet`, and does not exist in the MCP tool. Done live on testnet for both pairs (2026-11-03 and 2026-11-04): blocked without proof, passed with the migrate proof, and the state made before the upgrade survived (a registration; a 1 NEAR escrow deal) ([`evidence/UPGRADE_TESTNET.md`](evidence/UPGRADE_TESTNET.md)).

## Why this is shown for NEAR Borsh state

The general problem, new code reading state that older code wrote, exists on any chain with upgradeable contracts; Borsh deserialization of a changed struct is its NEAR shape, and that is what the runner, the fixtures and the evidence here exercise.

- **State is one Borsh-serialized struct.** The contract struct is stored as Borsh bytes under one key; new code that declares a different struct reads the old bytes as the new layout and panics with `Cannot deserialize the contract state.` on every call ([Updating Contracts](https://docs.near.org/smart-contracts/release/upgrade), [Serialization](https://docs.near.org/smart-contracts/anatomy/serialization)). Observed: all 12 steps after a code-only upgrade fail in the sandbox ([`evidence/AB_PILOT_N10.md`](evidence/AB_PILOT_N10.md)).
- **The migration is a constructor that ignores the old state.** A method marked `#[init(ignore_state)]` reads the old layout itself and returns the new struct; it must be `#[private]` or owner-checked, or anyone can call it ([near-sdk `#[near]` attribute](https://docs.rs/near-sdk/5.17.2/near_sdk/attr.near.html)). The N10 fixture does exactly this: `#[private] #[init(ignore_state)] pub fn migrate()` in `tests/fixtures/near/event_registration_waitlist/src/lib.rs`.
- **Deploy and migrate are two actions of one transaction.** `near contract deploy … with-init-call migrate …` sends `DeployContract` + `FunctionCall(migrate)` as one batch, and so does the runner's `upgrade` step (`contracts/sim-runner/src/main.rs`: `contract.batch().deploy(code)` followed by the call). If `migrate` fails, the whole batch is rolled back and the old code stays live. Observed in the pilot: the arm without a `migrate` method got `MethodNotFound`, the batch reverted, and the next call still answered with the old contract's `Event is full` ([`evidence/AB_PILOT_N10.md`](evidence/AB_PILOT_N10.md)). On testnet the owner's deploy + `migrate` was one transaction, [`8kvdZu87…`](https://explorer.testnet.near.org/transactions/8kvdZu8787miimR9rJHkiB1oNBXu58xJrJk4mxZcofrd).
- **Storage is a locked balance, not a fee.** Every byte of code and state locks 1e19 yoctoNEAR (10 NEAR per MB) on the account ([Storage staking](https://docs.near.org/protocol/storage/storage-staking)). The gate's `--estimate-storage` reads that constant from the RPC and prints the lock; on testnet it was 1.81 NEAR for N06 and 2.07 NEAR for N10 ([`evidence/E2E_TESTNET.md`](evidence/E2E_TESTNET.md), [`evidence/UPGRADE_TESTNET.md`](evidence/UPGRADE_TESTNET.md)).
- **Callbacks are public methods.** The callback of a cross-contract call is callable by anyone unless it is `#[private]` (predecessor must be the contract itself), and authorisation must use `predecessor_account_id`, not `signer_account_id` ([Private callbacks](https://docs.near.org/smart-contracts/security/cross-contract-calls/callbacks)). That is what the rule-pack's three rules check.

**vs near-workspaces.** Same engine: `bumm-sim` is built on near-workspaces-rs 0.24 and runs the same near-sandbox binary. The difference is the layer on top: the scenario is a JSON file that an agent writes and CI re-runs on every push, not a Rust test, and the gate binds its result to the pair (on-chain code sha256 → candidate sha256) right before `near contract deploy`. Not done: a proof on state copied from a live network. Our runner starts from an empty sandbox and creates the state with the scenario's own transactions.

## Why this matters (measured)

An A/B pilot on one realistic change, N10 ("add a waitlist to the N06 event-registration contract"), same task text, same model (Sonnet), same frozen starter, fresh context per arm. A1: Rust + NEAR docs. B1: the same plus BUMM Guard (scenarios, `simulate`, a remote near-sandbox in CI). **One run per arm: a pilot, not a statistical result.** Full record: [`evidence/AB_PILOT_N10.md`](evidence/AB_PILOT_N10.md); the task: [`evidence/AB_TASK_N10.md`](evidence/AB_TASK_N10.md).

| Scored against hidden scenarios in near-sandbox | A1 (no BUMM) | B1 (with BUMM) |
|---|---|---|
| **Upgrade of a live N06 contract**, with `migrate()` (A1's own procedure) | **PASS 16/16** | **FAIL**: B1 has no `migrate`, the batch reverts, `Event is full` later |
| Upgrade, code only | FAIL 4/16: `Cannot deserialize the contract state.` | FAIL 4/16, the same |
| **Fresh deploy**, `scenarios/n10` (3 files, 35 steps) | 3/3, 35/35 (run 41608409754) | 3/3, 35/35 (run 41609795646) |
| Wall clock to a finished change | about 4.5 min | about 15 min (about 10 of it waiting for CI) |

Reading, as hypothesis from one pair: on the upgrade of a contract that already holds state, only the implementation with `migrate()` survived. B1 had passed every fresh-deploy check, and none of its checks (or the unit tests) showed that it had no working upgrade path; only the new `upgrade` step does. On a fresh deploy both arms were equal and the plain agent was faster. The scoring runs are 41617679746 (A1) and 41617688666 (B1). Not measured: pilot 2 with both arms told that the contract is live.

## Prove an upgrade (do X, see Y)

The scenario format has an `upgrade` step (`docs/FORMATS.md` §2a): it deploys new code to the **same** account that holds the state written by the earlier steps, optionally in one batch with a `migrate` call, and the steps after it run against the upgraded state. Four scenarios ship in `scenarios/upgrade/`, two per contract pair. First pair: N06 contract (capacity 2) with alice and bob registered and carol refused, then the N10 build (`tests/fixtures/near/event_registration_waitlist`, adds a waitlist) is deployed over it, then 12 checks (old registrations survive, carol joins the waitlist, bob's cancel promotes her).

**Validate locally (no Docker, no runner).** Exit `0`; nothing is executed. The wasm files are not built locally, so each is marked `(missing)` with a build hint on stderr; with `--validate-only` that is a warning.
```sh
python3 -m bumm_guard simulate scenarios/upgrade/n06_*.json --validate-only
```
```
valid  scenarios/upgrade/n06_to_n10_code_only.json  'Upgrade N06 → N10 (code-only upgrade): ...': 4 account(s), 16 step(s), sha256 7bd2f4909f08, wasm ... (missing)
       upgrade wasm ... (missing)
valid  scenarios/upgrade/n06_to_n10_with_migrate.json  'Upgrade N06 → N10 (upgrade with migrate()): ...': 4 account(s), 16 step(s), sha256 32bebb6e17bf, wasm ... (missing)
       upgrade wasm ... (missing)
scenario_sha256 48ddf57ee4853bb11ca48810d5e8cd4bdac1a8f23dc5910127245f6d3bf60aaa
2 scenario file(s) valid (validate-only, nothing executed)
```
(Same for `scenarios/n06/*.json`: 3 files, `scenario_sha256 378d90e1...`; `scenarios/escrow/*.json`: 2 files, `a34654e0...`; `scenarios/upgrade/escrow_*.json`: 2 files, 33 and 17 steps, `9013c92c...`.)

**See the real sandbox run (Linux only, so CI).** Open the `scenario runner (bumm-sim on near-sandbox, linux only)` job of CI run [41619178923](https://github.com/GodBooster/bumm-guard/actions/runs/41619178923) on `main` (all 5 jobs green) and read the steps `upgrade N06 -> N10 with migrate() (expected PASS)` and `... code only (expected FAIL ...)`; the `simulate-report` artifact holds both reports, and the passing one is committed as [`evidence/upgrade-with-migrate-report-ci.json`](evidence/upgrade-with-migrate-report-ci.json). What the log shows:

| scenario | result | what happened |
|---|---|---|
| upgrade **with** `migrate` | **PASS, 16/16** (5.7 s) | registrations survive, waitlist works, `upgrade_wasm_sha256` bound to the deployed file |
| upgrade **code only** | **FAIL, 4/16**: all 12 steps after the upgrade fail (8.0 s) | the deploy itself succeeds; every call then panics with `Cannot deserialize the contract state.` (views report the RPC's `unable to fulfill the query request`) |

The CI step for the code-only case is written to *expect* the failure: it requires exit `1` and checks the report (steps 0-3 passed, all 12 after them failed with status `failure` and with `Cannot deserialize the contract state` in their error text (for view steps also in `error_detail`), no infrastructure error, the deployed code's sha256 equals the candidate's), so a different failure turns the job red. The report CI produced is committed as `evidence/upgrade-code-only-report-ci.json`. To run it yourself on Linux, build the two wasm files and `bumm-sim` as in "Scenario runner" below (`-p event_registration` and `-p event_registration_waitlist`), then `python3 -m bumm_guard simulate scenarios/upgrade/n06_to_n10_with_migrate.json --runner contracts/target/release/bumm-sim`. To score a candidate build, pass it with `--upgrade-wasm PATH` (not `--wasm`, which replaces the old code). The same is available from CI for any branch: the `evaluate` workflow (`.github/workflows/evaluate.yml`, input `upgrade=candidate`).

**Second pair: escrow v1 → v2 (the smoke view lies).** In the N06 → N10 pair the root struct changes, so after a code-only upgrade *every* call fails, and a single smoke view would catch it. The second pair, `tests/fixtures/near/escrow_v1` → `escrow_v2`, changes the record *inside* an `IterableMap` (a `Deal` gets `status` and `created_at_ms` in place of `released`) while the root struct keeps its shape. CI run [41679210002](https://github.com/GodBooster/bumm-guard/actions/runs/41679210002), job `scenario runner`, steps `upgrade escrow v1 -> v2 ...`; reports committed as [`evidence/escrow-upgrade-with-migrate-report-ci.json`](evidence/escrow-upgrade-with-migrate-report-ci.json) and [`evidence/escrow-upgrade-code-only-report-ci.json`](evidence/escrow-upgrade-code-only-report-ci.json):

| scenario | result | what happened |
|---|---|---|
| `scenarios/upgrade/escrow_v1_to_v2_with_migrate.json` | **PASS, 33/33** (7.2 s) | three deals made on v1 (one released); deploy + `migrate` in one batch (18.7 Tgas); `deal_count` 3, each `get_deal` shows the right buyer, seller, amount and status (`Released`, `Funded`, `Funded`) with `created_at_ms: 0`; the seller's `refund` pays the buyer (`Refunded`); refund of a released deal and release by a non-buyer are refused; a new deal gets a real `created_at_ms`; the contract balance keeps the locked deposits |
| `scenarios/upgrade/escrow_v1_to_v2_code_only.json` | **FAIL, 9/17**: the step right after the upgrade *passes*, the 8 after it fail | `deal_count` answers `3` (the map's length lives in the root struct, which still reads); then every `get_deal`, `release` and `refund` panics with `Cannot deserialize element` (near-sdk's message for a map record it cannot read; views report it in `error_detail` too) |

The CI step asserts exactly that shape: exit `1`, steps before the upgrade passed, the `deal_count` view immediately after it passed with value `3`, all later steps failed with `Cannot deserialize element`. A post-deploy smoke check of `deal_count` (or `get_owner`) would have reported this upgrade as healthy; the funds-moving methods are the ones that break. `migrate` moves the records to a map with a new storage prefix and clears the old one (the comment on `migrate` in `escrow_v2/src/lib.rs` says why); migrated deals get `created_at_ms = 0`, which the scenario checks, because v1 never recorded the time. The escrow pair was upgraded live on testnet on 2026-11-04 (`bumm-guard-escrow.testnet`, below).

**The migration method must be protected.** A `migrate` with `#[init(ignore_state)]` that is neither `#[private]` nor guarded by `env::predecessor_account_id()` can be called by any account after the upgrade and rewrite the state. When the proof carries a migration call, the gate looks the method up in the candidate crate's sources and blocks an unprotected one (exit `3`, recorded as `migration_guard` in `gate-report.json`):
```
DEPLOY BLOCKED: migration method migrate in src/lib.rs:12 is callable by anyone: it has no #[private] and no predecessor check; after the upgrade any account could call it and rewrite the state
  fix: add `#[private]` above `#[init(ignore_state)]`, or `require!(env::predecessor_account_id() == self.owner, "only owner")` as the first line of the body
```
On a pass it prints `migration migrate: #[private] (src/lib.rs:118)` (both fixture pairs) or `migration migrate: owner check at <file>:<line>`. A method it cannot find in the sources is a warning, not a block (the crate may not be the one the wasm was built from).

**How long it takes.** The whole `scenario runner` job (toolchains, two wasm builds, `bumm-sim` build, three N06 scenarios, the gate, both upgrade scenarios) took 9 min 16 s on a cold cache (run 41428851380, 2026-11-02) and 1 min 52 s with the Rust cache warm (run 41627653109, 2026-11-03); with the escrow pair added (four wasm builds, five more scenarios, a second gate) 2 min 28 s warm (run 41679210002, 2026-11-03). On a fresh Linux machine, budget the cold figure plus the rustup downloads (two toolchains and the wasm32 target). The upgrade scenarios themselves run in 5.7 s and 8.0 s.

**Atomicity, observed.** The upgrade is one batch (`DeployContract` + `FunctionCall`). When `migrate` fails (in the pilot: B1's build has no such method, `MethodNotFound`), NEAR reverts the whole batch and the old code stays live. A failed migration is therefore a safe failure; a missing one is not. Three qualifications, from the protocol documentation and not re-measured here: the rollback covers the one receipt that executes the batch, so a cross-contract `Promise` that `migrate` schedules runs in a later receipt and is not undone with it; the gas burnt by the batch before `migrate` fails (including the `DeployContract` action) is spent, not refunded; and `DeployContract` needs a full-access key on the account, so a function-call access key cannot upgrade a contract, and an upgrade leaves the account's keys as they were ([Access keys](https://docs.near.org/protocol/access-keys)).

## End to end on testnet, including a live upgrade

Four recorded sessions on testnet (`bumm-guard-demo.testnet`, `bumm-guard-escrow.testnet`), with the owner's own keys. Nothing in this repository signed a transaction; the gate printed the commands and the owner ran them.

- **Fresh deploy (2026-11-02)**, [`evidence/E2E_TESTNET.md`](evidence/E2E_TESTNET.md): CI build of N06 (sha256 `c3f50add…`), `simulate` in near-sandbox (3/3 scenarios, 24/24 steps), `gate` with that report and a storage estimate (1.81 NEAR), deploy with init by the owner (tx [`A4FxLHmLW7aPBYnGgzn2fV5oaHYhydkp3QwaE6y61KP8`](https://explorer.testnet.near.org/transactions/A4FxLHmLW7aPBYnGgzn2fV5oaHYhydkp3QwaE6y61KP8)), `gate --verify-deployed` (on-chain code sha256 = artifact sha256, MATCH), smoke views, and the ABI panel pointed at the contract. Report: `evidence/testnet-gate-report-2026-11-02.json`.
- **Live upgrade N06 → N10 (2026-11-03)**, [`evidence/UPGRADE_TESTNET.md`](evidence/UPGRADE_TESTNET.md): the owner first called `register` on the old contract (tx [`6BbnmQ1sRnKc6LgGKHomtSRMHBmHRwNRihFPQ8sqiKqq`](https://explorer.testnet.near.org/transactions/6BbnmQ1sRnKc6LgGKHomtSRMHBmHRwNRihFPQ8sqiKqq)), so the account held state. Gate without a proof: `DEPLOY BLOCKED: account bumm-guard-demo.testnet already runs code c3f50add…; this is an upgrade and no passing upgrade scenario proves c3f50add… -> 8526195a…`, exit 3. Gate with the failing code-only report: `DEPLOY BLOCKED … did not pass`, exit 3. Gate with the CI report of the passing migrate scenario: passed, storage lock estimate 2.07 NEAR, and it printed one batch: `near contract deploy bumm-guard-demo.testnet use-file …event_registration_waitlist.wasm with-init-call migrate json-args '{}' prepaid-gas '100 Tgas' …`. The owner ran that command, deploy + `migrate` in one transaction (tx [`8kvdZu8787miimR9rJHkiB1oNBXu58xJrJk4mxZcofrd`](https://explorer.testnet.near.org/transactions/8kvdZu8787miimR9rJHkiB1oNBXu58xJrJk4mxZcofrd), 2.0 Tgas). Then `gate --verify-deployed`: on-chain code sha256 `8526195a…` = the proven N10 wasm (MATCH); `get_registered_count` 1 and `get_registered` `["bumm-guard-demo.testnet"]` (the registration made before the upgrade survived), `get_waitlist` `[]` (the new method answers), `get_capacity` 2. Report: `evidence/testnet-upgrade-gate-report-2026-11-03.json`.

- **Live upgrade escrow v1 → v2 (2026-11-04)**, same file, second table: fresh account `bumm-guard-escrow.testnet`, v1 deployed through the gate (tx [`9xCvJr6FLY2R6uQduWQQSe4qeT4cVagqaSfr614p28cj`](https://explorer.testnet.near.org/transactions/9xCvJr6FLY2R6uQduWQQSe4qeT4cVagqaSfr614p28cj)), one deal of 1 NEAR made on v1 (tx [`F3XBjpq73hZHuCZFZEFFgweNYGSVfk3sHArerXt9XVMW`](https://explorer.testnet.near.org/transactions/F3XBjpq73hZHuCZFZEFFgweNYGSVfk3sHArerXt9XVMW)). Gate for v2: no proof → `DEPLOY BLOCKED … no passing upgrade scenario proves ecd8a805… -> 13425a04…`; code-only CI report → `DEPLOY BLOCKED … did not pass`; migrate CI report → passed, `migration migrate: #[private] (src/lib.rs:118)`, printed `… with-init-call migrate …`. The owner ran it (tx [`DPgBviLp8DCPF2FiVG9jXigHJaCgNviqni17odxiQ4Xm`](https://explorer.testnet.near.org/transactions/DPgBviLp8DCPF2FiVG9jXigHJaCgNviqni17odxiQ4Xm), 2.0 Tgas). `gate --verify-deployed`: code `13425a04…` MATCH, `deal_count` 1, `get_deal(0)` returns the v1 deal in the v2 shape (`status: "Funded"`, `created_at_ms: 0`). Report: `evidence/testnet-escrow-upgrade-gate-report-2026-11-04.json`.
- **Waitlist on the migrated N10 (2026-11-04)**, same file, third table: `register` from two more accounts; the second lands on the waitlist (`get_waitlist` `["bumm-guard-bob.testnet"]` at block 276189596), `cancel` by the first promotes it (`get_registered` `["bumm-guard-demo.testnet","bumm-guard-bob.testnet"]`, `get_waitlist` `[]`). Each state read from the archival RPC at the receipt's block.

Not covered: a change call signed through the panel's wallet, reproducible-wasm builds (the CI build and a local build differ in bytes, so the gate accepts evidence only for the exact file that was simulated; CI uploads that file as the `event_registration_waitlist-wasm` artifact), and mainnet.

## The problem

Coding agents now write and deploy NEAR contracts, and change requests on a live contract often change its stored state. Code that adds a field or a collection to the contract struct reads the old Borsh bytes as the new layout and panics unless a `#[init(ignore_state)]` migration reads the old layout first. The deploy succeeds; the next call fails. Separately, three defect classes from the NEAR security checklist are easy to produce and easy to miss in review: a callback that is not `#[private]` (anyone can call it with a forged result), a callback that never handles a failed cross-contract call (state changed before the call is never rolled back), and authorisation with `env::signer_account_id()` instead of `env::predecessor_account_id()` (a malicious intermediate contract passes the check). Today nothing stands between an agent and `near contract deploy` when its code has one of these problems. BUMM Guard puts deterministic checks in that position: the rule-pack for the three classes, and the scenario runner with its `upgrade` step for the state-layout problem.

References: [security checklist](https://docs.near.org/smart-contracts/security/checklist), [callbacks](https://docs.near.org/smart-contracts/security/cross-contract-calls/callbacks), [callback failures](https://docs.near.org/smart-contracts/security/cross-contract-calls/callback-failures).

## Try it in 5 minutes (do X, see Y)

Needs Python 3.12+ (verified locally on 3.14.7; CI uses 3.12. Ubuntu 22.04 ships 3.10, which is too old). No network, Docker or NEAR account is needed for steps 1-7; the real sandbox run is CI (section "Prove an upgrade" above). All commands run from the repository root. Fixtures live in `tests/fixtures/near/`.

**1. Install.** The package has no dependencies; `python3 -m bumm_guard` works from the repository root without installing anything, and every example below uses it. The install only adds the `bumm-guard` command. Ubuntu 24.04 refuses `pip install` outside a virtual environment (PEP 668), so create one first.
```sh
git clone https://github.com/GodBooster/bumm-guard.git && cd bumm-guard
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -e .
python3 -m bumm_guard --version
```
```
bumm-guard 0.5.0
```

**2. Scan a vulnerable contract.** Exit code `1` (a high finding exists).
```sh
python3 -m bumm_guard scan tests/fixtures/near/d1_vuln
```
```
tests/fixtures/near/d1_vuln/src/lib.rs:23: [HIGH] BG-NEAR-001 non-private-callback: Callback `on_paid` (&mut self) is publicly callable: it is a callback (scheduled via .then() in `pay` (tests/fixtures/near/d1_vuln/src/lib.rs:13)) but has no #[private]. Any account can invoke it with attacker-chosen arguments and a forged promise result.
    | pub fn on_paid(&mut self, #[callback_result] r: Result<(), PromiseError>) {
    fix: Add `#[private]` directly above `pub fn on_paid` (after existing attributes), or assert env::predecessor_account_id() == env::current_account_id().
1 finding(s) in 1 file(s): high=1
```
Add `--format json` for machine-readable output (`summary.by_rule`, `findings[].{rule_id,severity,file,line,message,fix_hint,snippet}`). Scanning all fixtures, `python3 -m bumm_guard scan tests/fixtures/near`, gives 3 findings (one per rule) in 11 files, exit `1`. A safe fixture, `scan tests/fixtures/near/d1_safe`, prints `0 finding(s) in 1 file(s)`, exit `0`.

**3. Preview the fix (dry run, writes nothing).** Exit `1` (the finding is still there).
```sh
python3 -m bumm_guard fix tests/fixtures/near/d1_vuln
```
```
--- a/tests/fixtures/near/d1_vuln/src/lib.rs
+++ b/tests/fixtures/near/d1_vuln/src/lib.rs
@@ -20,6 +20,7 @@
     }
 
     // VULNERABLE: no #[private] -> anyone can call and set paid = true
+    #[private]
     pub fn on_paid(&mut self, #[callback_result] r: Result<(), PromiseError>) {
         if r.is_ok() { self.paid = true; }
     }
tests/fixtures/near/d1_vuln/src/lib.rs:23: BG-NEAR-001 fixable (dry-run, use --apply): insert #[private] above `on_paid`
1 file(s) would change (dry-run, nothing written)
```

**4. Apply the fix on a temporary copy and re-scan.** The repo fixture stays untouched (the tests depend on it).
```sh
cp -R tests/fixtures/near/d1_vuln /tmp/d1_fix
python3 -m bumm_guard fix /tmp/d1_fix --apply
python3 -m bumm_guard scan /tmp/d1_fix
```
```
(same diff as above)
/tmp/d1_fix/src/lib.rs:23: BG-NEAR-001 applied: insert #[private] above `on_paid`
1 file(s) changed; 0 finding(s) remain after re-scan
0 finding(s) in 1 file(s)
```
Both commands exit `0`.

**5. Gate a vulnerable crate: the deploy is blocked.** Exit `3`.
```sh
python3 -m bumm_guard gate tests/fixtures/near/d1_vuln
```
```
(the finding, as in step 2)
1 finding(s) in 1 file(s): high=1
DEPLOY BLOCKED: 1 high/critical finding(s) in tests/fixtures/near/d1_vuln. Fix them (see `bumm-guard fix`) and run the gate again.
```

**6. Gate a clean crate: report plus the deploy command.** Use a copy, because a pass writes `<crate>/.bumm-guard/gate-report.json` into the crate. `contract.wasm` is a placeholder that does not exist, so the gate says so twice: it cannot record a sha256, and it cannot compare the file with the code on `demo.testnet` (an upgrade check needs an existing `--wasm`; no network call is made). With a real build, pass its path. Exit `0`; nothing is executed.
```sh
cp -R tests/fixtures/near/d1_safe /tmp/d1_safe
python3 -m bumm_guard gate /tmp/d1_safe --wasm contract.wasm --account demo.testnet
```
```
bumm-guard: warning: wasm not found: contract.wasm (sha256 not recorded)
warning: no --wasm: the code on demo.testnet was not checked; an upgrade of a live contract needs --wasm and a --simulate-report with a passing upgrade scenario
warning: no scenario report: behaviour not verified
gate passed: report written to /tmp/d1_safe/.bumm-guard/gate-report.json
deploy command (NOT executed; pass --execute with --network testnet to run it):
  near contract deploy demo.testnet use-file contract.wasm without-init-call network-config testnet sign-with-keychain send
```
The report (`/tmp/d1_safe/.bumm-guard/gate-report.json`) holds: tool version, `rule_ids_checked`, findings counts, sha256 of every scanned `*.rs`, the wasm path and sha256, network, account, UTC timestamp. With an existing `--wasm` the gate also queries the account's code over read-only RPC; see "Deploy gate on testnet".

**7. Run the unit tests.**
```sh
python3 -m unittest discover -s tests
```
```
Ran 170 tests in 23.994s

OK (skipped=14)
```
The 14 skipped tests are the 13 MCP tests (the `mcp` package is not installed) and one live-RPC test (enabled with `BUMM_GUARD_LIVE_RPC=1`). After `python3 -m pip install -e '.[mcp]'` the MCP tests run too. If `cargo` is installed and the crates are in its cache, a compile check of the patched fixtures also runs (the run then takes longer). CI runs the same command with the `mcp` extra: `Ran 170 tests`, `OK (skipped=3)` on run 41627653109 (the live-RPC test and two fixture compile checks whose crates are not in the runner's offline cargo cache). Do not run `cargo test` inside `tests/fixtures/near` with its pinned rustc 1.85.0: the fixtures build to wasm with it, but their `unit-testing` dev-dependencies need rustc 1.88 or newer. The panel tests need Node 22: `node --test ui/test` (18 tests).

## Use from a coding agent (MCP)

`bumm-guard-mcp` is an MCP server over stdio with six tools. It needs the optional `mcp` package (tested with mcp 1.30.0 and 2.3.0 by the MCP test suite).

1. `python3 -m pip install -e '.[mcp]'` creates the command `bumm-guard-mcp` (it waits on stdin; that is normal).
2. Open Claude Code in this directory: `.mcp.json` registers the server (project scope; approve it once). Rehearsed 2026-11-03 with the desktop app: the agent found the six tools and stopped on `DEPLOY BLOCKED` (`docs/DEMO_AGENT.md`). Or register it with `claude mcp add bumm-guard -- bumm-guard-mcp`, or load the plugin in `clients/claude-plugin/` (`claude --plugin-dir clients/claude-plugin`), which bundles the server config and the skill `near-deploy-gate`. For Cursor or Codex, copy `clients/claude-plugin/examples/mcp-config.json` into their MCP config. The server must start in the directory that holds the contract. **Not verified:** the `claude` CLI commands and the Cursor/Codex configs have not been run by the author (only the stdio protocol, via `tests/test_mcp.py`, and JSON validity of the plugin files).
3. Ask the agent `scan tests/fixtures/near/d1_vuln` and it calls `scan_contract`; the result is the JSON of step 2 (1 finding, `BG-NEAR-001`, `high`). `fix it` calls `fix_contract` and returns the diff of step 3 without writing. `gate tests/fixtures/near/d1_vuln` calls `gate_deploy` and returns `blocked: true`, `deploy_command: null`.

| tool | arguments | returns |
|---|---|---|
| `list_rules` | none | `{rules: [{id, name, severity, summary}]}` |
| `scan_contract` | `path`, `exclude?` | same JSON as `scan --format json` |
| `fix_contract` | `path`, `apply=false`, `exclude?` | same JSON as `fix --format json` (`diffs` per file, `remaining` after apply) |
| `gate_deploy` | `crate_dir`, `wasm?`, `account?`, `network="testnet"`, `simulate_report?` | `{blocked, passed, blocking_findings, findings, report_path, deploy_command, deploy_executed: false, upgrade, upgrade_check, deploy: {status: "printed"|"no-change", command}, messages, note}`. With `account` and `wasm` it reads the account's code (one read-only `view_code`); if the account already runs other code, it blocks unless `simulate_report` holds a passing upgrade scenario for exactly that pair. `upgrade` is then `{onchain_code_sha256, candidate_sha256, scenario, migrate, passed, post_upgrade_steps}` and `deploy_command` repeats the `migrate` call; `null` for a fresh deploy; `deploy_command` is also `null` with `deploy.status: "no-change"` when the account already runs exactly `wasm` |
| `estimate_storage` | `wasm`, `network="testnet"` | `{code_bytes, state_bytes_after_init, storage_amount_per_byte_yocto, source, locked_near_for_code, note}`; one read-only RPC query, falls back to the documented 1e19 yocto/byte |
| `verify_deployed` | `account`, `wasm`, `network="testnet"`, `smoke?` (`METHOD[:ARGS_JSON][=EXPECTED_JSON]`) | `{account, code_hash_on_chain, matches_artifact, smoke: [{view, args?, expected?, ok, result|error}], note}`; with `=EXPECTED` the view must return that JSON value; read-only, refuses `new`, `migrate` and other change methods as smoke names |

**Human in the loop.** The skill `near-deploy-gate` tells the agent: scan; show findings; call `fix_contract` with `apply=false` and show the diff; call it with `apply=true` only after the human says yes; run the crate's tests; call `gate_deploy`; on `blocked: true` stop and never bypass; on `passed: true` show the command and let the human run it; after the human deployed, call `verify_deployed`. Mainnet is used only if the human asks, and the deploy is always the human's step. Be precise about what enforces this: the server enforces that `gate_deploy` cannot execute a deploy (no `execute` parameter exists, `deploy_executed` is always `false`), that paths must resolve inside the server's working directory (override: `BUMM_GUARD_ALLOW_ANY_PATH=1`, only for projects you trust the agent with), and that `fix_contract` writes only with `apply=true` and never under `target/`. The "ask the human before `apply=true`" step is enforced by the tool description and the skill, not by the server.

## Deploy gate on testnet

On a pass, `gate` prints (near-cli-rs 0.30 syntax):

```
near contract deploy <account> use-file <wasm> without-init-call network-config <network> sign-with-keychain send
```

or, for a proven upgrade, the same command with `with-init-call <migrate method> json-args '<args>' prepaid-gas '<gas>' attached-deposit '0 NEAR'` in place of `without-init-call`. For such an upgrade the gate also checks in the crate's sources that the migration method is not callable by anyone (`#[private]`, or a `predecessor_account_id()` check with a recognised rejection branch at the top of its body) and blocks when it is or when the check cannot be recognised (`owner-check-unknown`). It also lists what the scenario proved after the upgrade (`proved after upgrade: …`, `upgrade.post_upgrade_steps` in the report) and blocks when the proven `migrate` args name a scenario alias such as `alice` (the sandbox resolved it, testnet would not). If the account already runs exactly this wasm the gate prints `NO CHANGE … nothing to deploy`, no command, exit 0 (`deploy.status: "no-change"`). This deploys the given wasm to `<account>`, signing with a key from the local near-cli keychain. The human runs it with their own account and key. This repository ships no key and no account. `--execute` on the CLI runs the command for you; it is off by default, works only with `--network testnet`, requires `--account` and an existing `--wasm`, and requires `near` on PATH. `--network mainnet --execute` is refused (verified: `bumm-guard: --execute is only allowed with --network testnet; refusing to deploy to mainnet.`, exit `2`). `--execute` is not reachable from MCP. `--dry-run` skips writing the report. The real testnet deploys were done by the owner with their own key (see "End to end on testnet"); this README's commands do not perform one.

Exit codes. `scan`: `0` no high/critical, `1` at least one, `2` bad path. `fix`: `0` nothing left to fix, `1` a high/critical finding remains (always so in a dry run that has something to fix), `2` bad path. `gate`: `0` clean, `2` bad input or refused (not a directory, bad account id, `--execute` on mainnet or without `--account`/`--wasm`), `3` DEPLOY BLOCKED (findings, a simulate report that does not match the wasm or has failed scenarios, an upgrade without a passing proof or whose only proof has a rolled-back upgrade transaction, migrate args naming a sandbox alias, or a migration method callable by anyone or without a recognised owner check), `4` `--execute` but `near` not on PATH, `5` `near` ran and failed.

## Rules

| id | class | matched | excluded / downgraded | severity |
|---|---|---|---|---|
| `BG-NEAR-001` `non-private-callback` | callback publicly callable | a plain `pub` method with `&self`/`&mut self` in a `#[near]`/`#[near_bindgen]` impl that is a callback (target of `.then(Self::ext(..).cb(..))`, or has a `#[callback_result\|callback_unwrap\|callback_vec]` parameter, or reads the promise result) and has no `#[private]` | `#[private]`; explicit `predecessor == current_account_id` check (also via local alias) or `assert_self()`; `#[init]`; `self`-by-value; `pub(crate)`; `#[cfg(test)]` | high if `&mut self` or the body moves value; otherwise info |
| `BG-NEAR-002` `callback-ignores-failure` | state changed before a cross-contract call, callback never handles failure | a method mutates `self.` state before the `.then(..)` to callback C, and C neither has `#[callback_result]` nor reads the promise result (`promise_result`, `is_promise_success`) nor calls a `self.`/`Self::` method named like `resolve/rollback/revert/refund/restore` | C handles the result or uses such a handler; calls to other contracts' `ext_*` are not treated as callbacks | high; medium with `#[callback_unwrap]`/`#[callback_vec]`; info if no mutated value reaches C |
| `BG-NEAR-003` `signer-auth` | authorisation by signer instead of direct caller | `signer_account_id()` inside `require!`/`assert*!` arguments, next to `==`/`!=`, or in an `if` condition, in a `pub` method of a `#[near]` impl | `// signer-ok` comment above or inside the method; logging-only uses; `#[cfg(test)]`; an alias bound with `let` (reported only as REVIEW) | high; low (message starts `REVIEW:`) for identity/key use |

The authoritative list is `python3 -m bumm_guard rules`. Fixers (`bumm-guard fix`): 001 inserts `#[private]` above the callback; 003 replaces `signer_account_id` with `predecessor_account_id` in the flagged guard only (REVIEW findings are never patched); 002 is **not** auto-fixed: it returns a `suggested_patch` skeleton (`#[callback_result] r: Result<(), PromiseError>` plus an `if r.is_err()` rollback TODO) with `applied: false`.

## Scenario runner

Static checks say what the code looks like; a scenario says what the contract does. `contracts/sim-runner` (crate `bumm-sim`, Rust, near-workspaces 0.24.0 / near-sandbox 0.4.0) executes JSON scenarios (format: `docs/FORMATS.md` §2) against a wasm in a **fresh local sandbox per scenario file** and writes the §3 report. A scenario has named accounts with balances, the wasm and an init call, then linear steps: `call` (by an account, with args, deposit, gas), `view`, `balance`, `upgrade`. Each step carries an expectation: `ok`, `fail_contains`, `equals`, `contains`, `at_least`, `at_most`. Steps keep running after a failed expectation, so the report shows the whole trace.

**Do X, see Y.** Validate without a runner (`python3 -m bumm_guard simulate scenarios/n06/*.json --validate-only`; the upgrade scenarios are in "Prove an upgrade") and get `valid ...` for the three files in `scenarios/n06/`, which target the fixture contract `tests/fixtures/near/event_registration` (N06: event registration with a capacity; methods `new(capacity)`, `register`, `cancel`, `get_capacity`, `get_registered_count`, `get_registered`, `is_registered`, `get_seats_left`). The second fixture contract, `tests/fixtures/near/escrow_v1` (a buyer locks a deposit for a seller; `new`, `create_deal(seller)` payable, `release(id)`, `get_deal(id)`, `deal_count`, `get_owner`), has `scenarios/escrow/01_create_release.json` (13 steps: deposit, `get_deal`, release by the buyer, seller and contract balances) and `scenarios/escrow/02_refusals.json` (12 steps: no deposit, release by a non-buyer, unknown deal, second release); its upgrade pair with `escrow_v2` is in "Prove an upgrade". The real run, on Linux:
```sh
cd tests/fixtures/near && cargo build --release --locked --target wasm32-unknown-unknown -p event_registration   # rustc 1.85.0, pinned there
cd ../../../contracts && cargo build --release --locked -p bumm-sim
cd .. && python3 -m bumm_guard simulate scenarios/n06/*.json --runner contracts/target/release/bumm-sim --out simulate-report.json
```
`simulate` validates the files, runs the binary, checks the report's hashes against the inputs and prints a summary. Exit `0` all passed or all files valid, `1` a scenario failed (or no readable report, or a hash mismatch), `2` invalid file or missing wasm, `4` runner not found.

**What was observed** (CI job `scenario runner`, 9 min 16 s including compile on a cold cache, run 41428851380): 3/3 scenarios, 24/24 steps passed. The refusals happened on-chain with the contract's own messages: `Event is full: capacity 2 reached...` (third registration), `... is already registered` (duplicate), `... is not registered` (cancel without registering). Durations 5.1 s, 3.3 s, 4.0 s. The report is the `simulate-report` artifact of that run and is committed as `evidence/simulate-report-ci-41428851380-series.json`.

**Limits.** The format is linear: no loops, no scripting, no conditionals. One contract, named accounts, JSON args only (no borsh, no cross-contract setup, no time travel or state patching). Only the final status of a transaction is reported (a handled callback failure inside a successful transaction is not shown). Linux only: `near-sandbox` ships no macOS x86_64 binary, so locally only the unit tests run, via `scripts/linux-test.sh contracts test -p bumm-sim --lib` (needs Docker; this is the one Docker step); the real run is CI. The wasm must be built first; the fixture builds with rustc 1.85.0, but its own unit tests (`cargo test -p event_registration`) need rustc 1.88 or newer because of the `unit-testing` dev-dependencies. The Python and Rust validators are kept in sync by hand. Details: `SIMULATE_REPORT.md`.

## ABI interaction panel

`ui/` is a static page (`index.html`, `panel.js`, `panel.css`; no backend, no build step) that reads a near-abi file and renders one form per contract function: view calls go to a JSON-RPC endpoint, change calls through a wallet redirect. It is an interaction panel generated from the ABI, **not a product UI**; the wallet write path is written but **not verified**, and the bundled ABI fixture is hand-written, not `cargo near abi` output. Open it with `python3 -m http.server -d ui`; the step-by-step walkthrough, the supported argument types and the verified/unverified list are in [`ui/README.md`](ui/README.md) (18 node tests: `node --test ui/test`).

## Testing

- **Counts** (run on 2026-11-03): Python 170 (`python3 -m unittest discover -s tests`: 14 skipped without `mcp`, 13 MCP tests and 1 live-RPC test; CI, with `mcp`: 3 skipped), node 18 (`node --test ui/test`), Rust `bumm-sim` lib 17 (`grep -c '#\[test\]' contracts/sim-runner/src/lib.rs`; not run locally, CI runs them), sandbox tests 2 (`bg_near_001`, `upgrade_sandbox`).
- **Unit tests** (170). They cover each `*_vuln` fixture giving exactly its own finding and each `*_safe` fixture giving none (the scan of `tests/fixtures/near` covers 11 files and gives exactly 3 findings), the CLI exit codes, JSON/text output, 30 synthetic edge cases, fixers (dry run, apply, re-scan, CRLF, `target/` untouched), the gate (blocked, pass, report, mainnet refusal, storage estimate and `--verify-deployed` with a stubbed RPC), the gate's upgrade proof (24 tests in `tests/test_gate_upgrade.py`: pair matching, failing or mismatched reports, the printed `with-init-call` command), `simulate` (amounts, validation, the `upgrade` step, hashing, summary, the CLI driven by a fake runner) and the MCP server over a real stdio subprocess.
- **Fixtures compile.** `tests/fixtures/near/` is a workspace of nine near-sdk 5.17.2 crates (the eighth is `event_registration`, the ninth `event_registration_waitlist`, the N10 build with `migrate()`). Verified locally: `cd tests/fixtures/near && cargo check --workspace --locked --offline` finishes without errors (host target). The wasm32 target is checked only in CI. `cargo test` in that workspace needs rustc 1.88 or newer (dev-dependencies), while the pinned toolchain is 1.85.0; CI does not run it.
- **Linux runner.** `scripts/linux-test.sh <workspace-dir> [cargo args]` runs cargo in a `rust:1.99-slim-bookworm` Docker container with the repo mounted, for example `scripts/linux-test.sh tests/fixtures/near check --workspace --locked`. It exists because `near-sandbox` (used by `near-workspaces` sandbox tests) ships no macOS x86_64 binary, and the author develops on such a Mac. Verified on 2026-10-30 with Docker Desktop 4.97 on an Intel Mac: `scripts/linux-test.sh tests/fixtures/near check --workspace --locked` finished in 1 min 41 s (44 s of it `cargo check`, the rest apt and image setup).
- **CI** (`.github/workflows/ci.yml`, on every push and PR), 5 jobs: `rule-pack tests (python)` (Python 3.12, MCP extra, unit tests, scans the fixtures and asserts exactly the three rule ids and exit `1`), `ABI panel tests (node)` (`node --test ui/test`), `fixtures compile (cargo check, wasm32)` (`cargo check --workspace --locked`, host and `wasm32-unknown-unknown`), `sandbox tests (near-workspaces)` and `scenario runner (bumm-sim on near-sandbox)` (builds the N06, N10, escrow v1 and v2 wasm and `bumm-sim`, runs the three N06 scenarios and the two escrow scenarios, the gate with each report, and the four upgrade scenarios, two expected to pass and two expected to fail after the upgrade step; uploads `simulate-report`, `event_registration-wasm`, `event_registration_waitlist-wasm`, `escrow_v1-wasm` and `escrow_v2-wasm`). The five most recent runs on `main`, up to 41627653109 (2026-11-03, commit `17e9bb3`), were green on all 5 jobs; the run cited above, 41619178923, is one of them. Wall clock with a warm cache: about 2 min for the whole workflow. A second workflow, `evaluate` (`.github/workflows/evaluate.yml`, manual dispatch), builds a crate from a given branch (`-p <its name>`, `--locked`, toolchain from the `toolchain` input, default 1.85.0) and runs given scenario files against it with the same runner; it scored the A/B pilot.
- **`contracts/` sandbox tests** (`contracts/sandbox/tests/bg_near_001.rs`, near-workspaces 0.24): deploys the real `d1_vuln`, `d1_safe` and `proxy` fixtures to a local near-sandbox and checks, for each fixture, that a third account calling the callback through the proxy is accepted on `d1_vuln` (state flips to paid) and rejected on `d1_safe` with `Method on_paid is private`, that the seller's balance is untouched by the spoof, and that the legitimate `pay` path works on both. `contracts/sim-runner/tests/upgrade_sandbox.rs` runs the two upgrade scenarios through `bumm-sim` as a Rust test. Both run in CI on ubuntu (job `sandbox tests`, 1 min 46 s with a warm cache on run 41627653109; the job also runs the 17 `bumm-sim` lib tests) and locally on Linux via `scripts/linux-test.sh contracts test --workspace --locked`. The fixtures build with rustc 1.85.0 (pinned in `tests/fixtures/near/rust-toolchain.toml`) because near-sdk 5.17.2 wasm imports do not link on rustc 1.99.

## Honest limits

- Detection and fixes are regex/brace-level: no type information, no name resolution, no macro expansion (methods generated by macros or `include!` are invisible). Callback scheduling is recognised only as `.then(<Self|own type|*self*|*callback*>::ext[_self](..)[.with_*(..)]*.cb(..))`. The old `ext_self::cb(..)` syntax, `Promise::function_call("cb", ..)` with string names, `.and()` joins and callbacks stored in variables are missed by 002 (001 may still fire via parameter attributes). 002 mutation detection is textual; mutation through a local alias or a helper is not seen. 003 does not follow the signer through helpers or struct fields.
- Fixes are textual patches for the exact patterns flagged. They are checked by re-scan and, for the fixtures, `cargo check`; that is not proof the contract is safe. Read the diff.
- BG-NEAR-002 is not auto-fixed (a correct rollback needs semantic knowledge).
- **Scenario runner:** linear scenarios only (no loops, no scripting), one contract, JSON args; Linux only (no local real run on macOS x86_64). A passing scenario shows behaviour on the listed steps, not absence of bugs. The scenarios shipped (3 N06, 2 escrow, 4 upgrade, 3 N10 acceptance) were written by the author for two fixture contracts of the author's own (event registration and its waitlist variant, escrow v1 and v2); no third-party contract has been run through the runner.
- **Upgrade proof:** shown for two contract pairs of the author's own (N06 to N10, escrow v1 to v2) in the sandbox on every push, and for both pairs once live on testnet (2026-11-03 and 2026-11-04, `evidence/UPGRADE_TESTNET.md`). Not on mainnet, and not on state copied from a live network: the sandbox state is the transactions the scenario makes before its `upgrade` step, so the proof covers the listed post-upgrade steps on that scenario-made state (printed as `proved after upgrade: …` and recorded in `upgrade.post_upgrade_steps`), not the live state and not every stored state. A passing scenario whose steps after the upgrade are all views (or balance reads) is accepted with a warning (`only view calls were made on the migrated state; no state change was exercised`), not refused. The proof is accepted only when the upgrade transaction itself succeeded; a scenario that expected the upgrade to fail, and saw it rolled back, proves nothing and is skipped. Migrate args in the proof must be literal values: a scenario alias (`alice`) in them blocks, because testnet would not resolve it. The `upgrade` step is a batch signed by the contract account itself with a full-access key; it does not model a DAO-controlled or access-key-restricted upgrade, and a cross-contract call made by `migrate` would not be rolled back with a failing batch. The migration-guard check is regex and brace level, not a parser: it accepts `#[private]` on the method, or a direct `predecessor_account_id()` comparison in the first 15 lines of its body when it sits inside `require!`/`assert!`/`assert_eq!`/`assert_ne!`/`require_eq!`/`require_ne!`, or in an `if`/`match` whose body (any branch) contains `panic!`, `env::panic_str`, `require!`/`assert!`, `return` or `unreachable!`. A comparison without such a rejection, or a tautology (`pred == pred`), is `owner-check-unknown` and blocks like `unprotected`; what the predecessor is compared with is not judged. A check delegated to a helper (`self.assert_owner()`), to a DAO policy or to an access-key restriction is not recognised: if the method is found and has none of these, the gate blocks even though the contract may be safe; add `#[private]` to pass. A method it does not find in the sources is only a warning, so the check cannot vouch for a wasm built from another crate. The gate requires an upgrade proof for accounts that already have code (read-only `view_code`); live on testnet it was seen blocking (no proof, failing proof) and passing (migrate proof) once per pair, two pairs. A proof must do something after the upgrade: a scenario whose last step is the `upgrade` step, or a report without the deployed `code_sha256`, is rejected. When `view_code` reports no code, the gate also reads `view_account`: an account that uses a global contract (NEP-591) is refused, not treated as empty; upgrading such accounts is out of scope. The A/B pilot is one run per arm, one task, one model: it shows that the failure is real and that a fresh-deploy check cannot see it, not how often agents make it.
- **ABI panel:** the wallet (change-call) path is written but not verified; the bundled ABI is hand-written, not `cargo near abi` output. It is an interaction panel, not a finished UI.
- **False-positive rate on production code is not measured.** The only real-code run (recorded in `RULEPACK_REPORT.md`, 2026-10-30) used 290 `.rs` files from near-sdk-rs examples and standards, near/core-contracts and three near-examples repos (99 exported impls, 332 exported methods, about 1.4 s, no crashes). Results: BG-NEAR-001 5 findings (2 high, 3 info), BG-NEAR-002 0, BG-NEAR-003 7 (all low, REVIEW). Both high findings are true positives by pattern; one has real impact (`mpc-contract` `sign_on_finish`), the other has low practical impact (public `&mut self` callback that mutates nothing). Of the 7 REVIEW items, 3 are signer values forwarded as arguments (noise), 1 is a legitimate per-user key, 3 are review-worthy. `core-contracts` gave 0 because of the old `ext_self::cb` syntax the detector does not recognise. This is a small, educational sample and a sanity check, not an FP rate. The corpus itself is not included in this repository.
- The gate is **pipeline control, not an on-chain guarantee**. It means "no high/critical finding from these three rules in the scanned source, and, for an account with code, a passing upgrade scenario for this pair of hashes". It does not build the wasm, does not check that the wasm matches the source (it records both hashes), checks nothing beyond three rules, and is not an audit. Anyone with shell access can still run `near contract deploy` by hand.

## Prior work and what was built during the hackathon

**Prior work (not included here).** [BUMM](https://github.com/bumm-ai/bumm) is an AI smart-contract builder for Solana/Anchor by the same author, developed 2025-2026, before this hackathon. It is a separate project on another chain and is **not part of this repository**; no BUMM source code was copied in.

**Built during the hackathon window** (announcement 2026-10-19 to 2026-11-14). This repository was created on 2026-10-30, and everything in it was written in that window: the NEAR rule-pack (`bumm_guard/rulepack/near/`), the three detectors, the fixers (`bumm_guard/fix/`), the deploy gate (`bumm_guard/gate.py`) with its upgrade proof, the storage estimate and post-deploy check (`bumm_guard/storage.py`, `bumm_guard/verify.py`), the CLI, the MCP server (`bumm_guard/mcp_server.py`), the Claude Code plugin and skill (`clients/claude-plugin/`), the fixtures (including `tests/fixtures/near/event_registration` and its N10 variant) and tests (`tests/`), the Linux runner script and CI, the scenario runner (`contracts/sim-runner`, `bumm_guard/simulate.py`, `scenarios/`), the `upgrade` step with its two scenarios (2026-11-03), the `evaluate` workflow and the A/B pilot (`evidence/`, `ab/`), and the ABI interaction panel (`ui/`). `git log --format='%h %ad %s' --date=iso` lists every commit; the first one is dated 2026-10-30. Nothing in the history predates the window.

**Ideas reused from BUMM** (ideas only, no code): the find, fix, gate pipeline shape and the style of MCP tool naming.

**Libraries and tools.** Runtime: Python standard library only. Optional: the `mcp` Python SDK for the MCP server. Fixtures depend on `near-sdk` 5.17.2; the runner and the sandbox tests use `near-workspaces` 0.24.0 / `near-sandbox` 0.4.0; the panel loads `near-api-js` 4.0.4 from a CDN only for the unverified wallet path. Parts of the code were written with AI assistance (Claude Code).

## License, track, links

- License: MIT (`LICENSE`).
- Track: Open Innovation (primary), NEAR HACKATHON; the rule-pack part also fits AI Security & Defense.
- Repository: https://github.com/GodBooster/bumm-guard
- Rules and dates: https://nearhackathon.com/HACKATHON.md
- NEAR docs used: https://docs.near.org/smart-contracts/security/checklist
- Further detail: `RULEPACK_REPORT.md` (detectors, corpus run, limitations), `MCP_REPORT.md` (MCP server, SDK versions, limitations), `SIMULATE_REPORT.md` (scenario runner), `UPGRADE_REPORT.md` (upgrade step and the gate's upgrade proof), `GATE_REPORT.md` (storage estimate, `--verify-deployed`), `evidence/` (testnet E2E, live upgrade, A/B pilot, CI reports), `ui/README.md` (panel), `docs/FORMATS.md` (file formats), `docs/SUBMISSION.md` (submission text).
