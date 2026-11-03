# BUMM Guard

**BUMM Guard proves that a change to a NEAR contract works on the state that is already deployed, before an agent or a human deploys it.** In NEAR, redeploying new code over a contract whose stored state has a different layout, without a migration, leaves a contract that panics on every call (`Cannot deserialize the contract state.`). Unit tests do not see this, and neither do scenarios that deploy fresh. The `upgrade` step of the scenario runner deploys the old wasm in a local near-sandbox, puts real state into it with transactions, deploys the new wasm on the same account (with or without a `migrate` call) and runs the checks against the upgraded state. Around that sits the rest of the "after the contract compiles" path for coding agents on NEAR (near-sdk-rs 5.x): a static-analysis rule-pack with deterministic fixers, scenarios in a sandbox, a deploy gate, and an ABI interaction panel. Contract generation is deliberately left to the agent. Python 3.12+, standard library only, for the CLI; an optional MCP server (`bumm-guard-mcp`) exposes scan/fix/gate to Claude Code, Cursor, Codex or any MCP client. Track: Open Innovation (primary), NEAR HACKATHON; the rule-pack also fits AI Security & Defense.

```
scan -> fix -> simulate scenarios -> prove the upgrade of live state -> gate -> deploy -> ABI panel
```

**In progress (separate branch, not merged):** a gate that requires an upgrade proof whenever the target account already has code. Today the gate checks the rule-pack and, optionally, a simulate report bound to the wasm hash.

## Why this matters (measured)

An A/B pilot on one realistic change, N10 ("add a waitlist to the N06 event-registration contract"), same task text, same model (Sonnet), same frozen starter, fresh context per arm. A1: Rust + NEAR docs. B1: the same plus BUMM Guard (scenarios, `simulate`, a remote near-sandbox in CI). **One run per arm: a pilot, not a statistical result.** Full record: [`evidence/AB_PILOT_N10.md`](evidence/AB_PILOT_N10.md); the task: [`evidence/AB_TASK_N10.md`](evidence/AB_TASK_N10.md).

| Scored against hidden scenarios in near-sandbox | A1 (no BUMM) | B1 (with BUMM) |
|---|---|---|
| **Fresh deploy**, `scenarios/n10` (3 files, 35 steps) | 3/3, 35/35 (run 41608409754) | 3/3, 35/35 (run 41609795646) |
| Wall clock to a finished change | about 4.5 min | about 15 min (about 10 of it waiting for CI) |
| **Upgrade of a live N06 contract**, with `migrate()` (A1's own procedure) | **PASS 16/16** | **FAIL**: B1 has no `migrate`, the batch reverts, `Event is full` later |
| Upgrade, code only | FAIL 4/16: `Cannot deserialize the contract state.` | FAIL 4/16, the same |

Reading, as hypothesis from one pair: on a fresh deploy both arms were equal and the plain agent was faster. On the upgrade of a contract that already holds state, only the implementation with `migrate()` survived. B1 had passed every fresh-deploy check, and none of its checks (or the unit tests) showed that it had no working upgrade path; only the new `upgrade` step does. The scoring runs are 41617679746 (A1) and 41617688666 (B1). Not measured: pilot 2 with both arms told that the contract is live.

## The problem

Coding agents now write and deploy NEAR contracts, and change requests on a live contract often change its stored state. Code that adds a field or a collection to the contract struct reads the old Borsh bytes as the new layout and panics unless a `#[init(ignore_state)]` migration reads the old layout first. The deploy succeeds; the next call fails. Separately, three defect classes from the NEAR security checklist are easy to produce and easy to miss in review: a callback that is not `#[private]` (anyone can call it with a forged result), a callback that never handles a failed cross-contract call (state changed before the call is never rolled back), and authorisation with `env::signer_account_id()` instead of `env::predecessor_account_id()` (a malicious intermediate contract passes the check). Today nothing stands between an agent and `near contract deploy` when its code has one of these problems. BUMM Guard puts deterministic checks in that position: the rule-pack for the three classes, and the scenario runner with its `upgrade` step for the state-layout problem.

References: [security checklist](https://docs.near.org/smart-contracts/security/checklist), [callbacks](https://docs.near.org/smart-contracts/security/cross-contract-calls/callbacks), [callback failures](https://docs.near.org/smart-contracts/security/cross-contract-calls/callback-failures).

## Try it in 5 minutes (do X, see Y)

Needs Python 3.12+ (verified locally on 3.14.7). No network, Docker or NEAR account is needed for steps 1-7; the real sandbox run is CI (next section). All commands run from the repository root. Fixtures live in `tests/fixtures/near/`.

**1. Install.**
```sh
git clone https://github.com/GodBooster/bumm-guard.git && cd bumm-guard
python3 -m pip install -e .
bumm-guard --version
```
```
bumm-guard 0.5.0
```
(Without installing, `python3 -m bumm_guard ...` from the repo root is equivalent. The examples below use it.)

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
Add `--format json` for machine-readable output (`summary.by_rule`, `findings[].{rule_id,severity,file,line,message,fix_hint,snippet}`). Scanning all fixtures, `python3 -m bumm_guard scan tests/fixtures/near`, gives 3 findings (one per rule) in 9 files, exit `1`. A safe fixture, `scan tests/fixtures/near/d1_safe`, prints `0 finding(s) in 1 file(s)`, exit `0`.

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

**6. Gate a clean crate: report plus the deploy command.** Use a copy, because a pass writes `<crate>/.bumm-guard/gate-report.json` into the crate. `contract.wasm` is a placeholder; with a real build, pass its path and the report records its sha256. Exit `0`; nothing is executed.
```sh
cp -R tests/fixtures/near/d1_safe /tmp/d1_safe
python3 -m bumm_guard gate /tmp/d1_safe --wasm contract.wasm --account demo.testnet
```
```
bumm-guard: warning: wasm not found: contract.wasm (sha256 not recorded)
warning: no scenario report: behaviour not verified
gate passed: report written to /tmp/d1_safe/.bumm-guard/gate-report.json
deploy command (NOT executed; pass --execute with --network testnet to run it):
  near contract deploy demo.testnet use-file contract.wasm without-init-call network-config testnet sign-with-keychain send
```
The report (`/tmp/d1_safe/.bumm-guard/gate-report.json`) holds: tool version, `rule_ids_checked`, findings counts, sha256 of every scanned `*.rs`, the wasm path and sha256, network, account, UTC timestamp.

**7. Run the unit tests.**
```sh
python3 -m unittest discover -s tests
```
```
Ran 146 tests in 26.836s
OK (skipped=14)
```
The skipped tests are the MCP tests (the `mcp` package is not installed). After `python3 -m pip install -e '.[mcp]'` the same command skips nothing. If `cargo` is installed, a compile check of the patched fixtures also runs (the first run is longer). The panel tests need Node 22: `node --test ui/test` (18 tests). Scenarios and the upgrade proof are the next section.

## Prove an upgrade (do X, see Y)

The scenario format has an `upgrade` step (`docs/FORMATS.md` §2a): it deploys new code to the **same** account that holds the state written by the earlier steps, optionally in one batch with a `migrate` call, and the steps after it run against the upgraded state. Two scenarios ship in `scenarios/upgrade/`: N06 contract (capacity 2) with alice and bob registered and carol refused, then the N10 build (`tests/fixtures/near/event_registration_waitlist`, adds a waitlist) is deployed over it, then 12 checks (old registrations survive, carol joins the waitlist, bob's cancel promotes her).

**Validate locally (no Docker, no runner).** Exit `0`; nothing is executed. The wasm files are not built locally, so each is marked `(missing)` with a build hint on stderr; with `--validate-only` that is a warning.
```sh
python3 -m bumm_guard simulate scenarios/upgrade/*.json --validate-only
```
```
valid  scenarios/upgrade/n06_to_n10_code_only.json  'Upgrade N06 → N10 (code-only upgrade): ...': 4 account(s), 16 step(s), sha256 7bd2f4909f08, wasm ... (missing)
valid  scenarios/upgrade/n06_to_n10_with_migrate.json  'Upgrade N06 → N10 (upgrade with migrate()): ...': 4 account(s), 16 step(s), sha256 32bebb6e17bf, wasm ... (missing)
scenario_sha256 48ddf57ee4853bb11ca48810d5e8cd4bdac1a8f23dc5910127245f6d3bf60aaa
2 scenario file(s) valid (validate-only, nothing executed)
```
(Same for `scenarios/n06/*.json`: 3 files, `scenario_sha256 378d90e1...`.)

**See the real sandbox run (Linux only, so CI).** Open the `scenario runner (bumm-sim on near-sandbox, linux only)` job of CI run [41619178923](https://github.com/GodBooster/bumm-guard/actions/runs/41619178923) on `main` (all 5 jobs green) and read the steps `upgrade N06 -> N10 with migrate() (expected PASS)` and `... code only (expected FAIL ...)`; the `simulate-report` artifact holds both reports. What the log shows:

| scenario | result | what happened |
|---|---|---|
| upgrade **with** `migrate` | **PASS, 16/16** (5.7 s) | registrations survive, waitlist works, `upgrade_wasm_sha256` bound to the deployed file |
| upgrade **code only** | **FAIL, 4/16**: all 12 steps after the upgrade fail (8.0 s) | the deploy itself succeeds; every call then panics with `Cannot deserialize the contract state.` (views report the RPC's `unable to fulfill the query request`) |

The CI step for the code-only case is written to *expect* the failure: it requires exit `1` and checks the report (steps 0-3 passed, all 12 after them failed with status `failure`, no infrastructure error), so a different failure turns the job red. To run it yourself on Linux, build the two wasm files and `bumm-sim` as in "Scenario runner" below (`-p event_registration` and `-p event_registration_waitlist`), then `python3 -m bumm_guard simulate scenarios/upgrade/n06_to_n10_with_migrate.json --runner contracts/target/release/bumm-sim`. To score a candidate build, pass it with `--upgrade-wasm PATH` (not `--wasm`, which replaces the old code). The same is available from CI for any branch: the `evaluate` workflow (`.github/workflows/evaluate.yml`, input `upgrade=candidate`).

**Atomicity, observed.** The upgrade is one batch (`DeployContract` + `FunctionCall`). When `migrate` fails (in the pilot: B1's build has no such method, `MethodNotFound`), NEAR reverts the whole batch and the old code stays live. A failed migration is therefore a safe failure; a missing one is not.

## End to end on testnet

One path with every step bound to the same wasm by its sha256: CI build, `simulate` in near-sandbox, `gate` with the simulate report and a storage estimate, owner's deploy to `bumm-guard-demo.testnet` (tx `A4FxLHmLW7aPBYnGgzn2fV5oaHYhydkp3QwaE6y61KP8`), `gate --verify-deployed` (on-chain code sha256 matches the artifact), smoke views, and the ABI panel pointed at the contract. Recorded on 2026-11-02 with the owner's own key in [`evidence/E2E_TESTNET.md`](evidence/E2E_TESTNET.md), with the gate report. Not covered there: a change call signed through the panel's wallet, an upgrade performed on testnet (the upgrade proof so far is the sandbox run above), and reproducible-wasm builds.

## Use from a coding agent (MCP)

`bumm-guard-mcp` is an MCP server over stdio with four tools. It needs the optional `mcp` package (tested with mcp 1.30.0 and 2.3.0 by the MCP test suite).

1. `python3 -m pip install -e '.[mcp]'` creates the command `bumm-guard-mcp` (it waits on stdin; that is normal).
2. Register it in Claude Code with `claude mcp add bumm-guard -- bumm-guard-mcp`, or load the plugin in `clients/claude-plugin/` (`claude --plugin-dir clients/claude-plugin`), which bundles the server config and the skill `near-deploy-gate`. For Cursor or Codex, copy `clients/claude-plugin/examples/mcp-config.json` into their MCP config. The server must start in the directory that holds the contract. **Not verified:** the `claude` CLI commands and the Cursor/Codex configs have not been run by the author (only the stdio protocol, via `tests/test_mcp.py`, and JSON validity of the plugin files).
3. Ask the agent `scan tests/fixtures/near/d1_vuln` and it calls `scan_contract`; the result is the JSON of step 2 (1 finding, `BG-NEAR-001`, `high`). `fix it` calls `fix_contract` and returns the diff of step 3 without writing. `gate tests/fixtures/near/d1_vuln` calls `gate_deploy` and returns `blocked: true`, `deploy_command: null`.

| tool | arguments | returns |
|---|---|---|
| `list_rules` | none | `{rules: [{id, name, severity, summary}]}` |
| `scan_contract` | `path`, `exclude?` | same JSON as `scan --format json` |
| `fix_contract` | `path`, `apply=false`, `exclude?` | same JSON as `fix --format json` (`diffs` per file, `remaining` after apply) |
| `gate_deploy` | `crate_dir`, `wasm?`, `account?`, `network="testnet"` | `{blocked, passed, blocking_findings, findings, report_path, deploy_command, deploy_executed: false, messages, note}` |

**Human in the loop.** The skill `near-deploy-gate` tells the agent: scan; show findings; call `fix_contract` with `apply=false` and show the diff; call it with `apply=true` only after the human says yes; run the crate's tests; call `gate_deploy`; on `blocked: true` stop and never bypass; on `passed: true` show the command and let the human run it. Mainnet is used only if the human asks, and the deploy is always the human's step. Be precise about what enforces this: the server enforces that `gate_deploy` cannot execute a deploy (no `execute` parameter exists, `deploy_executed` is always `false`), that paths must resolve inside the server's working directory (override: `BUMM_GUARD_ALLOW_ANY_PATH=1`, only for projects you trust the agent with), and that `fix_contract` writes only with `apply=true` and never under `target/`. The "ask the human before `apply=true`" step is enforced by the tool description and the skill, not by the server.

## Deploy gate on testnet

On a pass, `gate` prints (near-cli-rs 0.30 syntax):

```
near contract deploy <account> use-file <wasm> without-init-call network-config <network> sign-with-keychain send
```

This deploys the given wasm to `<account>` without calling an init method, signing with a key from the local near-cli keychain. The human runs it with their own account and key. This repository ships no key and no account. `--execute` on the CLI runs the command for you; it is off by default, works only with `--network testnet`, requires `--account` and an existing `--wasm`, and requires `near` on PATH. `--network mainnet --execute` is refused (verified: `bumm-guard: --execute is only allowed with --network testnet; refusing to deploy to mainnet.`, exit `2`). `--execute` is not reachable from MCP. `--dry-run` skips writing the report. A real testnet deploy was done once, by the owner with their own key (see "End to end on testnet"); this README's commands do not perform one.

Exit codes. `scan`: `0` no high/critical, `1` at least one, `2` bad path. `fix`: `0` nothing left to fix, `1` a high/critical finding remains (always so in a dry run that has something to fix), `2` bad path. `gate`: `0` clean, `2` bad input or refused (not a directory, bad account id, `--execute` on mainnet or without `--account`/`--wasm`), `3` DEPLOY BLOCKED, `4` `--execute` but `near` not on PATH, `5` `near` ran and failed.

## Rules

| id | class | matched | excluded / downgraded | severity |
|---|---|---|---|---|
| `BG-NEAR-001` `non-private-callback` | callback publicly callable | a plain `pub` method with `&self`/`&mut self` in a `#[near]`/`#[near_bindgen]` impl that is a callback (target of `.then(Self::ext(..).cb(..))`, or has a `#[callback_result\|callback_unwrap\|callback_vec]` parameter, or reads the promise result) and has no `#[private]` | `#[private]`; explicit `predecessor == current_account_id` check (also via local alias) or `assert_self()`; `#[init]`; `self`-by-value; `pub(crate)`; `#[cfg(test)]` | high if `&mut self` or the body moves value; otherwise info |
| `BG-NEAR-002` `callback-ignores-failure` | state changed before a cross-contract call, callback never handles failure | a method mutates `self.` state before the `.then(..)` to callback C, and C neither has `#[callback_result]` nor reads the promise result (`promise_result`, `is_promise_success`) nor calls a `self.`/`Self::` method named like `resolve/rollback/revert/refund/restore` | C handles the result or uses such a handler; calls to other contracts' `ext_*` are not treated as callbacks | high; medium with `#[callback_unwrap]`/`#[callback_vec]`; info if no mutated value reaches C |
| `BG-NEAR-003` `signer-auth` | authorisation by signer instead of direct caller | `signer_account_id()` inside `require!`/`assert*!` arguments, next to `==`/`!=`, or in an `if` condition, in a `pub` method of a `#[near]` impl | `// signer-ok` comment above or inside the method; logging-only uses; `#[cfg(test)]`; an alias bound with `let` (reported only as REVIEW) | high; low (message starts `REVIEW:`) for identity/key use |

The authoritative list is `python3 -m bumm_guard rules`. Fixers (`bumm-guard fix`): 001 inserts `#[private]` above the callback; 003 replaces `signer_account_id` with `predecessor_account_id` in the flagged guard only (REVIEW findings are never patched); 002 is **not** auto-fixed: it returns a `suggested_patch` skeleton (`#[callback_result] r: Result<(), PromiseError>` plus an `if r.is_err()` rollback TODO) with `applied: false`.

## Scenario runner

Static checks say what the code looks like; a scenario says what the contract does. `contracts/sim-runner` (crate `bumm-sim`, Rust, near-workspaces 0.24.0 / near-sandbox 0.4.0) executes JSON scenarios (format: `docs/FORMATS.md` §2) against a wasm in a **fresh local sandbox per scenario file** and writes the §3 report. A scenario has named accounts with balances, the wasm and an init call, then linear steps: `call` (by an account, with args, deposit, gas), `view`, `balance`. Each step carries an expectation: `ok`, `fail_contains`, `equals`, `contains`, `at_least`, `at_most`. Steps keep running after a failed expectation, so the report shows the whole trace.

**Do X, see Y.** Validate without a runner (`python3 -m bumm_guard simulate scenarios/n06/*.json --validate-only`; the upgrade scenarios are in "Prove an upgrade") and get `valid ...` for the three files in `scenarios/n06/`, which target the fixture contract `tests/fixtures/near/event_registration` (N06: event registration with a capacity; methods `new(capacity)`, `register`, `cancel`, `get_capacity`, `get_registered_count`, `get_registered`, `is_registered`, `get_seats_left`). The real run, on Linux:
```sh
cd tests/fixtures/near && cargo build --release --locked --target wasm32-unknown-unknown -p event_registration   # rustc 1.85.0, pinned there
cd ../../../contracts && cargo build --release --locked -p bumm-sim
cd .. && python3 -m bumm_guard simulate scenarios/n06/*.json --runner contracts/target/release/bumm-sim --out simulate-report.json
```
`simulate` validates the files, runs the binary, checks the report's hashes against the inputs and prints a summary. Exit `0` all passed or all files valid, `1` a scenario failed (or no readable report, or a hash mismatch), `2` invalid file or missing wasm, `4` runner not found.

**What was observed** (CI job `scenario runner`, about 9 min including compile, run 41428851380): 3/3 scenarios, 24/24 steps passed. The refusals happened on-chain with the contract's own messages: `Event is full: capacity 2 reached...` (third registration), `... is already registered` (duplicate), `... is not registered` (cancel without registering). Durations 5.1 s, 3.3 s, 4.0 s. The report is the `simulate-report` artifact of that run.

**Limits.** The format is linear: no loops, no scripting, no conditionals. One contract, named accounts, JSON args only (no borsh, no cross-contract setup, no time travel or state patching). Only the final status of a transaction is reported (a handled callback failure inside a successful transaction is not shown). Linux only: `near-sandbox` ships no macOS x86_64 binary, so locally only the unit tests run, via `scripts/linux-test.sh contracts test -p bumm-sim --lib` (needs Docker; this is the one Docker step); the real run is CI. The wasm must be built first; the fixture builds with rustc 1.85.0, but its own unit tests (`cargo test -p event_registration`) need rustc 1.88 or newer because of the `unit-testing` dev-dependencies. The Python and Rust validators are kept in sync by hand. Details: `SIMULATE_REPORT.md`.

## ABI interaction panel

`ui/` is a static page (`index.html`, `panel.js`, `panel.css`; no backend, no build step) that reads a near-abi file and renders one form per contract function. It is an interaction panel generated from the ABI, **not a product UI**. View calls go straight to a JSON-RPC endpoint with zero dependencies; change calls go through a wallet redirect.

**Do X, see Y** (no wallet needed for these):
```sh
python3 -m http.server -d ui      # then open http://localhost:8000
```
Type a testnet account where the N06 contract is deployed into "Contract account id", click **Load bundled N06 fixture**: 8 cards appear (`new` marked init, `register`, `cancel`, five views). **Call** on `get_capacity` shows the capacity as JSON. Typing `Alice` into `is_registered` shows `account_id: "Alice" is not a valid NEAR account id ...` and sends no request; `-1` into `new` shows `capacity: -1 is below the minimum 0`. Step-by-step: `ui/README.md`.

**Limits.** The wallet write path (`signIn`, `sendTransaction`, MyNearWallet via `near-api-js` from a CDN) is written but **not verified**: nobody has run it against a wallet. The bundled ABI `ui/fixtures/event_registration_abi.json` is hand-written and should be replaced by real `cargo near abi` output. Unsupported argument types fall back to a raw JSON textarea. Mainnet defaults are untested. Verified: 18 node tests (`node --test ui/test`), and `viewCall` against live testnet RPC and in a browser over `http://localhost` (ABI load, validation messages, rendering).

## Testing

- **Counts** (run on 2026-11-03): Python 146 (14 skipped without `mcp`), node 18, Rust `bumm-sim` lib 17 (not re-run for this README; CI runs them), sandbox tests 2 (`bg_near_001`, `upgrade_sandbox`).
- **Unit tests** (146): `python3 -m unittest discover -s tests`. They cover each `*_vuln` fixture giving exactly its own finding and each `*_safe` fixture giving none (the scan of `tests/fixtures/near` covers 9 files and gives exactly 3 findings), the CLI exit codes, JSON/text output, 30 synthetic edge cases, fixers (dry run, apply, re-scan, CRLF, `target/` untouched), the gate (blocked, pass, report, mainnet refusal), `simulate` (30 tests: amounts, validation, the `upgrade` step, hashing, summary, the CLI driven by a fake runner) and the MCP server over a real stdio subprocess.
- **Fixtures compile.** `tests/fixtures/near/` is a workspace of nine near-sdk 5.17.2 crates (the eighth is `event_registration`, the ninth `event_registration_waitlist`, the N10 build with `migrate()`). Verified locally: `cd tests/fixtures/near && cargo check --workspace --locked --offline` finishes without errors (host target). The wasm32 target is checked only in CI.
- **Linux runner.** `scripts/linux-test.sh <workspace-dir> [cargo args]` runs cargo in a `rust:1.99-slim-bookworm` Docker container with the repo mounted, for example `scripts/linux-test.sh tests/fixtures/near check --workspace --locked`. It exists because `near-sandbox` (used by `near-workspaces` sandbox tests) ships no macOS x86_64 binary, and the author develops on such a Mac. Verified on 2026-10-30 with Docker Desktop 4.97 on an Intel Mac: `scripts/linux-test.sh tests/fixtures/near check --workspace --locked` finished in 1 min 41 s (44 s of it `cargo check`, the rest apt and image setup).
- **CI** (`.github/workflows/ci.yml`, on every push and PR), 5 jobs: `rule-pack tests (python)` (Python 3.12, MCP extra, unit tests, scans the fixtures and asserts exactly the three rule ids and exit `1`), `ABI panel tests (node)` (`node --test ui/test`), `fixtures compile (cargo check, wasm32)` (`cargo check --workspace --locked`, host and `wasm32-unknown-unknown`), `sandbox tests (near-workspaces)` and `scenario runner (bumm-sim on near-sandbox)` (builds the N06 and N10 wasm and `bumm-sim`, runs the three N06 scenarios, the gate with their report, and the two upgrade scenarios, one expected to pass and one expected to fail after the upgrade step; uploads `simulate-report`). All 5 jobs were green on run 41619178923 (2026-11-03, `main`), visible on the Actions tab. A second workflow, `evaluate` (`.github/workflows/evaluate.yml`, manual dispatch), builds any crate from any branch and runs given scenario files against it with the same runner; it scored the A/B pilot.
- **`contracts/` sandbox tests** (`contracts/sandbox/tests/bg_near_001.rs`, near-workspaces 0.24): deploys the real `d1_vuln`, `d1_safe` and `proxy` fixtures to a local near-sandbox and checks, for each fixture, that a third account calling the callback through the proxy is accepted on `d1_vuln` (state flips to paid) and rejected on `d1_safe` with `Method on_paid is private`, that the seller's balance is untouched by the spoof, and that the legitimate `pay` path works on both. Runs in CI on ubuntu (job `sandbox tests`, about 4 min: 3 min compile, 31 s test; it now also runs the 13 `bumm-sim` lib tests) and locally on Linux via `scripts/linux-test.sh contracts test --workspace --locked`. Verified green on 2026-11-02 (Actions run 41422672919). The fixtures build with rustc 1.85.0 (pinned in `tests/fixtures/near/rust-toolchain.toml`) because near-sdk 5.17.2 wasm imports do not link on rustc 1.99.

## Honest limits

- Detection and fixes are regex/brace-level: no type information, no name resolution, no macro expansion (methods generated by macros or `include!` are invisible). Callback scheduling is recognised only as `.then(<Self|own type|*self*|*callback*>::ext[_self](..)[.with_*(..)]*.cb(..))`. The old `ext_self::cb(..)` syntax, `Promise::function_call("cb", ..)` with string names, `.and()` joins and callbacks stored in variables are missed by 002 (001 may still fire via parameter attributes). 002 mutation detection is textual; mutation through a local alias or a helper is not seen. 003 does not follow the signer through helpers or struct fields.
- Fixes are textual patches for the exact patterns flagged. They are checked by re-scan and, for the fixtures, `cargo check`; that is not proof the contract is safe. Read the diff.
- BG-NEAR-002 is not auto-fixed (a correct rollback needs semantic knowledge).
- **Scenario runner:** linear scenarios only (no loops, no scripting), one contract, JSON args; Linux only (no local real run on macOS x86_64). A passing scenario shows behaviour on the listed steps, not absence of bugs. The scenarios shipped (3 N06, 2 upgrade, 3 N10 acceptance) were written by the author for one fixture contract and its waitlist variant; no third-party contract has been run through the runner.
- **Upgrade proof:** shown for one contract pair (N06 to N10) in the sandbox, not on testnet or mainnet. It proves the listed steps on the state those steps create, not that every possible stored state migrates. The `upgrade` step is a batch signed by the contract account itself; it does not model a DAO-controlled or access-key-restricted upgrade. The gate does not yet require an upgrade proof (in progress). The A/B pilot is one run per arm, one task, one model: it shows that the failure is real and that a fresh-deploy check cannot see it, not how often agents make it.
- **ABI panel:** the wallet (change-call) path is written but not verified; the bundled ABI is hand-written, not `cargo near abi` output. It is an interaction panel, not a finished UI.
- **False-positive rate on production code is not measured.** The only real-code run (recorded in `RULEPACK_REPORT.md`, 2026-10-30) used 290 `.rs` files from near-sdk-rs examples and standards, near/core-contracts and three near-examples repos (99 exported impls, 332 exported methods, about 1.4 s, no crashes). Results: BG-NEAR-001 5 findings (2 high, 3 info), BG-NEAR-002 0, BG-NEAR-003 7 (all low, REVIEW). Both high findings are true positives by pattern; one has real impact (`mpc-contract` `sign_on_finish`), the other has low practical impact (public `&mut self` callback that mutates nothing). Of the 7 REVIEW items, 3 are signer values forwarded as arguments (noise), 1 is a legitimate per-user key, 3 are review-worthy. `core-contracts` gave 0 because of the old `ext_self::cb` syntax the detector does not recognise. This is a small, educational sample and a sanity check, not an FP rate. The corpus itself is not included in this repository.
- The gate is **pipeline control, not an on-chain guarantee**. It means "no high/critical finding from these three rules in the scanned source". It does not build the wasm, does not check that the wasm matches the source (it records both hashes), checks nothing beyond three rules, and is not an audit. Anyone with shell access can still run `near contract deploy` by hand.

## Prior work and what was built during the hackathon

**Prior work (not included here).** [BUMM](https://github.com/bumm-ai/bumm) is an AI smart-contract builder for Solana/Anchor by the same author, developed 2025-2026, before this hackathon. It is a separate project on another chain and is **not part of this repository**; no BUMM source code was copied in.

**Built during the hackathon window** (announcement 2026-10-19 to 2026-11-14). This repository was created on 2026-10-30, and everything in it was written in that window: the NEAR rule-pack (`bumm_guard/rulepack/near/`), the three detectors, the fixers (`bumm_guard/fix/`), the deploy gate (`bumm_guard/gate.py`), the CLI, the MCP server (`bumm_guard/mcp_server.py`), the Claude Code plugin and skill (`clients/claude-plugin/`), the fixtures (including `tests/fixtures/near/event_registration`) and tests (`tests/`), the Linux runner script and CI, the scenario runner (`contracts/sim-runner`, `bumm_guard/simulate.py`, `scenarios/`), the `upgrade` step with its two scenarios (2026-11-03), the `evaluate` workflow and the A/B pilot (`evidence/`, `ab/`), and the ABI interaction panel (`ui/`). `git log --format='%h %ad %s' --date=iso` lists every commit; the first one is dated 2026-10-30. Nothing in the history predates the window.

**Ideas reused from BUMM** (ideas only, no code): the find, fix, gate pipeline shape and the style of MCP tool naming. `Finding.to_bumm_vulnerability()` in `bumm_guard/model.py` maps a finding to the BUMM backend `Vulnerability` schema field names; it is a mapping function, not BUMM code.

**Libraries and tools.** Runtime: Python standard library only. Optional: the `mcp` Python SDK for the MCP server. Fixtures depend on `near-sdk` 5.17.2; the runner and the sandbox tests use `near-workspaces` 0.24.0 / `near-sandbox` 0.4.0; the panel loads `near-api-js` 4.0.4 from a CDN only for the unverified wallet path. Parts of the code were written with AI assistance (Claude Code).

## License, track, links

- License: MIT (`LICENSE`).
- Track: Open Innovation (primary), NEAR HACKATHON; the rule-pack part also fits AI Security & Defense.
- Repository: https://github.com/GodBooster/bumm-guard
- Rules and dates: https://nearhackathon.com/HACKATHON.md
- NEAR docs used: https://docs.near.org/smart-contracts/security/checklist
- Further detail: `RULEPACK_REPORT.md` (detectors, corpus run, limitations), `MCP_REPORT.md` (MCP server, SDK versions, limitations), `SIMULATE_REPORT.md` (scenario runner), `UPGRADE_REPORT.md` (upgrade step), `evidence/` (testnet E2E, A/B pilot), `ui/README.md` (panel), `docs/FORMATS.md` (file formats), `docs/SUBMISSION.md` (submission text).
