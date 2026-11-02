# BUMM Guard

**Find, fix, prove, gate for NEAR smart contracts (near-sdk-rs 5.x).** BUMM Guard is a static-analysis rule-pack with deterministic auto-fixers and a deploy gate, built for coding agents and for humans. It scans Rust contract source for three known NEAR defect classes, patches two of them with a minimal diff, re-scans to prove the finding is gone, and refuses to print a deploy command while any high or critical finding remains. Python 3.12+, standard library only. An optional MCP server (`bumm-guard-mcp`) exposes the same four operations to Claude Code, Cursor, Codex or any MCP client. Track: AI Security & Defense (NEAR HACKATHON).

## The problem

Coding agents now write and deploy NEAR contracts. Three defect classes that the NEAR docs list in their security checklist are easy to produce and easy to miss in review: a callback that is not `#[private]` (anyone can call it with a forged result), a callback that never handles a failed cross-contract call (state changed before the call is never rolled back), and authorisation with `env::signer_account_id()` instead of `env::predecessor_account_id()` (a malicious intermediate contract passes the check). Today nothing stands between an agent and `near contract deploy` when its code contains one of these. BUMM Guard puts a deterministic check in that position.

References: [security checklist](https://docs.near.org/smart-contracts/security/checklist), [callbacks](https://docs.near.org/smart-contracts/security/cross-contract-calls/callbacks), [callback failures](https://docs.near.org/smart-contracts/security/cross-contract-calls/callback-failures).

## Try it in 5 minutes (do X, see Y)

Needs Python 3.12+ (verified locally on 3.14.7). No network or NEAR account is needed for steps 1-7. All commands run from the repository root. Fixtures live in `tests/fixtures/near/`.

**1. Install.**
```sh
git clone https://github.com/GodBooster/bumm-guard.git && cd bumm-guard
python3 -m pip install -e .
bumm-guard --version
```
```
bumm-guard 0.3.0
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
Add `--format json` for machine-readable output (`summary.by_rule`, `findings[].{rule_id,severity,file,line,message,fix_hint,snippet}`). Scanning all fixtures, `python3 -m bumm_guard scan tests/fixtures/near`, gives 3 findings (one per rule) in 7 files, exit `1`. A safe fixture, `scan tests/fixtures/near/d1_safe`, prints `0 finding(s) in 1 file(s)`, exit `0`.

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
Ran 83 tests in 16.702s
OK (skipped=12)
```
The 12 skipped tests are the MCP tests (the `mcp` package is not installed). After `python3 -m pip install -e '.[mcp]'` the same command gives `Ran 83 tests ... OK` with nothing skipped (36 s on the verifying machine). If `cargo` is installed, a compile check of the patched fixtures also runs; first run may take longer.

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

This deploys the given wasm to `<account>` without calling an init method, signing with a key from the local near-cli keychain. The human runs it with their own account and key. This repository ships no key and no account. `--execute` on the CLI runs the command for you; it is off by default, works only with `--network testnet`, requires `--account` and an existing `--wasm`, and requires `near` on PATH. `--network mainnet --execute` is refused (verified: `bumm-guard: --execute is only allowed with --network testnet; refusing to deploy to mainnet.`, exit `2`). `--execute` is not reachable from MCP. `--dry-run` skips writing the report. Not run by the author: an actual `near contract deploy` (no testnet deploy was performed for this README).

Exit codes. `scan`: `0` no high/critical, `1` at least one, `2` bad path. `fix`: `0` nothing left to fix, `1` a high/critical finding remains (always so in a dry run that has something to fix), `2` bad path. `gate`: `0` clean, `2` bad input or refused (not a directory, bad account id, `--execute` on mainnet or without `--account`/`--wasm`), `3` DEPLOY BLOCKED, `4` `--execute` but `near` not on PATH, `5` `near` ran and failed.

## Rules

| id | class | matched | excluded / downgraded | severity |
|---|---|---|---|---|
| `BG-NEAR-001` `non-private-callback` | callback publicly callable | a plain `pub` method with `&self`/`&mut self` in a `#[near]`/`#[near_bindgen]` impl that is a callback (target of `.then(Self::ext(..).cb(..))`, or has a `#[callback_result\|callback_unwrap\|callback_vec]` parameter, or reads the promise result) and has no `#[private]` | `#[private]`; explicit `predecessor == current_account_id` check (also via local alias) or `assert_self()`; `#[init]`; `self`-by-value; `pub(crate)`; `#[cfg(test)]` | high if `&mut self` or the body moves value; otherwise info |
| `BG-NEAR-002` `callback-ignores-failure` | state changed before a cross-contract call, callback never handles failure | a method mutates `self.` state before the `.then(..)` to callback C, and C neither has `#[callback_result]` nor reads the promise result (`promise_result`, `is_promise_success`) nor calls a `self.`/`Self::` method named like `resolve/rollback/revert/refund/restore` | C handles the result or uses such a handler; calls to other contracts' `ext_*` are not treated as callbacks | high; medium with `#[callback_unwrap]`/`#[callback_vec]`; info if no mutated value reaches C |
| `BG-NEAR-003` `signer-auth` | authorisation by signer instead of direct caller | `signer_account_id()` inside `require!`/`assert*!` arguments, next to `==`/`!=`, or in an `if` condition, in a `pub` method of a `#[near]` impl | `// signer-ok` comment above or inside the method; logging-only uses; `#[cfg(test)]`; an alias bound with `let` (reported only as REVIEW) | high; low (message starts `REVIEW:`) for identity/key use |

The authoritative list is `python3 -m bumm_guard rules`. Fixers (`bumm-guard fix`): 001 inserts `#[private]` above the callback; 003 replaces `signer_account_id` with `predecessor_account_id` in the flagged guard only (REVIEW findings are never patched); 002 is **not** auto-fixed: it returns a `suggested_patch` skeleton (`#[callback_result] r: Result<(), PromiseError>` plus an `if r.is_err()` rollback TODO) with `applied: false`.

## Testing

- **Unit tests** (83): `python3 -m unittest discover -s tests`. They cover each `*_vuln` fixture giving exactly its own finding and each `*_safe` fixture giving none, the CLI exit codes, JSON/text output, 30 synthetic edge cases, fixers (dry run, apply, re-scan, CRLF, `target/` untouched), the gate (blocked, pass, report, mainnet refusal) and the MCP server over a real stdio subprocess.
- **Fixtures compile.** `tests/fixtures/near/` is a workspace of seven near-sdk 5.17.2 crates. Verified locally: `cd tests/fixtures/near && cargo check --workspace --locked --offline` finishes without errors (host target). The wasm32 target is checked only in CI.
- **Linux runner.** `scripts/linux-test.sh <workspace-dir> [cargo args]` runs cargo in a `rust:1.99-slim-bookworm` Docker container with the repo mounted, for example `scripts/linux-test.sh tests/fixtures/near check --workspace --locked`. It exists because `near-sandbox` (used by `near-workspaces` sandbox tests) ships no macOS x86_64 binary, and the author develops on such a Mac. Verified on 2026-10-30 with Docker Desktop 4.97 on an Intel Mac: `scripts/linux-test.sh tests/fixtures/near check --workspace --locked` finished in 1 min 41 s (44 s of it `cargo check`, the rest apt and image setup).
- **CI** (`.github/workflows/ci.yml`, on every push and PR): `rulepack` (Python 3.12, installs the MCP extra, unit tests, scans the fixtures and asserts exactly the three rule ids and exit `1`), `fixtures-compile` (`cargo check --workspace --locked`, host and `wasm32-unknown-unknown`), `sandbox` (runs `cargo test` in `contracts/` only if it exists). CI results are visible on the repository's Actions tab; they were not re-run for this README.
- **`contracts/` sandbox tests** (`contracts/sandbox/tests/bg_near_001.rs`, near-workspaces 0.24): deploys the real `d1_vuln`, `d1_safe` and `proxy` fixtures to a local near-sandbox and checks, for each fixture, that a third account calling the callback through the proxy is accepted on `d1_vuln` (state flips to paid) and rejected on `d1_safe` with `Method on_paid is private`, that the seller's balance is untouched by the spoof, and that the legitimate `pay` path works on both. Runs in CI on ubuntu (job `sandbox tests`, about 4 min: 3 min compile, 31 s test) and locally on Linux via `scripts/linux-test.sh contracts test --workspace --locked`. Verified green on 2026-11-02 (Actions run 41422672919). The fixtures build with rustc 1.85.0 (pinned in `tests/fixtures/near/rust-toolchain.toml`) because near-sdk 5.17.2 wasm imports do not link on rustc 1.99.

## Honest limits

- Detection and fixes are regex/brace-level: no type information, no name resolution, no macro expansion (methods generated by macros or `include!` are invisible). Callback scheduling is recognised only as `.then(<Self|own type|*self*|*callback*>::ext[_self](..)[.with_*(..)]*.cb(..))`. The old `ext_self::cb(..)` syntax, `Promise::function_call("cb", ..)` with string names, `.and()` joins and callbacks stored in variables are missed by 002 (001 may still fire via parameter attributes). 002 mutation detection is textual; mutation through a local alias or a helper is not seen. 003 does not follow the signer through helpers or struct fields.
- Fixes are textual patches for the exact patterns flagged. They are checked by re-scan and, for the fixtures, `cargo check`; that is not proof the contract is safe. Read the diff.
- BG-NEAR-002 is not auto-fixed (a correct rollback needs semantic knowledge).
- **False-positive rate on production code is not measured.** The only real-code run (recorded in `RULEPACK_REPORT.md`, 2026-10-30) used 290 `.rs` files from near-sdk-rs examples and standards, near/core-contracts and three near-examples repos (99 exported impls, 332 exported methods, about 1.4 s, no crashes). Results: BG-NEAR-001 5 findings (2 high, 3 info), BG-NEAR-002 0, BG-NEAR-003 7 (all low, REVIEW). Both high findings are true positives by pattern; one has real impact (`mpc-contract` `sign_on_finish`), the other has low practical impact (public `&mut self` callback that mutates nothing). Of the 7 REVIEW items, 3 are signer values forwarded as arguments (noise), 1 is a legitimate per-user key, 3 are review-worthy. `core-contracts` gave 0 because of the old `ext_self::cb` syntax the detector does not recognise. This is a small, educational sample and a sanity check, not an FP rate. The corpus itself is not included in this repository.
- The gate is **pipeline control, not an on-chain guarantee**. It means "no high/critical finding from these three rules in the scanned source". It does not build the wasm, does not check that the wasm matches the source (it records both hashes), checks nothing beyond three rules, and is not an audit. Anyone with shell access can still run `near contract deploy` by hand.

## Prior work and what was built during the hackathon

**Prior work (not included here).** [BUMM](https://github.com/bumm-ai/bumm) is an AI smart-contract builder for Solana/Anchor by the same author, developed 2025-2026, before this hackathon. It is a separate project on another chain and is **not part of this repository**; no BUMM source code was copied in.

**Built during the hackathon window** (announcement 2026-10-19 to 2026-11-14). This repository was created on 2026-10-30, and everything in it was written in that window: the NEAR rule-pack (`bumm_guard/rulepack/near/`), the three detectors, the fixers (`bumm_guard/fix/`), the deploy gate (`bumm_guard/gate.py`), the CLI, the MCP server (`bumm_guard/mcp_server.py`), the Claude Code plugin and skill (`clients/claude-plugin/`), the fixtures and tests (`tests/`), the Linux runner script and CI. `git log --format='%h %ad %s' --date=iso` lists every commit; the first one is dated 2026-10-30. Nothing in the history predates the window.

**Ideas reused from BUMM** (ideas only, no code): the find, fix, gate pipeline shape and the style of MCP tool naming. `Finding.to_bumm_vulnerability()` in `bumm_guard/model.py` maps a finding to the BUMM backend `Vulnerability` schema field names; it is a mapping function, not BUMM code.

**Libraries and tools.** Runtime: Python standard library only. Optional: the `mcp` Python SDK for the MCP server. Fixtures depend on `near-sdk` 5.17.2 (compile checks only). Parts of the code were written with AI assistance (Claude Code).

## License, track, links

- License: MIT (`LICENSE`).
- Track: AI Security & Defense (`security`), NEAR HACKATHON.
- Repository: https://github.com/GodBooster/bumm-guard
- Rules and dates: https://nearhackathon.com/HACKATHON.md
- NEAR docs used: https://docs.near.org/smart-contracts/security/checklist
- Further detail: `RULEPACK_REPORT.md` (detectors, corpus run, limitations), `MCP_REPORT.md` (MCP server, SDK versions, limitations).
