# BUMM Guard (NEAR rule-pack)

Static analysis for NEAR smart contracts (near-sdk-rs 5.x). Python 3.12+, standard library only.
Finds three defect classes before deploy, fixes two of them deterministically and gates the deploy; output is JSON for machines, text for humans.

## Quickstart (machine reader)

1. Install (from this directory): `python3 -m pip install -e .`  (or skip install and use `python3 -m bumm_guard ...` from this directory).
2. Scan a vulnerable fixture: `bumm-guard scan tests/fixtures/near/d1_vuln --format json` -> see "Expected JSON" below. Exit code `1` (a high-severity finding exists).
3. Scan everything in the fixtures: `bumm-guard scan tests/fixtures/near` -> 3 findings (BG-NEAR-001, -002, -003), exit code `1`.
4. Scan a safe fixture: `bumm-guard scan tests/fixtures/near/d1_safe` -> 0 findings, exit code `0`.
5. List rules: `bumm-guard rules` (add `--format json` for JSON).
6. Preview the fix for a vulnerable fixture: `bumm-guard fix tests/fixtures/near/d1_vuln` prints a unified diff (one inserted `#[private]` line), changes nothing, exit `1`. Add `--apply` to write it and re-scan.
7. Gate a deploy: `bumm-guard gate tests/fixtures/near/d1_vuln` -> `DEPLOY BLOCKED`, exit `3`. `bumm-guard gate tests/fixtures/near/d1_safe --wasm contract.wasm --account demo.testnet` -> writes `.bumm-guard/gate-report.json`, prints the `near contract deploy ...` command, exit `0`; nothing is executed.
8. Run tests: `python3 -m unittest discover -s tests` (the compile check runs `cargo check --offline` if `cargo` exists, and takes about a minute the first time).

`bumm-guard scan <path>...` takes files or directories (recursive `*.rs`, skips `target/`, `.git`, `node_modules`;
extra `--exclude DIRNAME`). `--format json|text` (default text). Exit codes: `0` no high/critical finding,
`1` at least one, `2` bad path. Files are grouped per crate (nearest `Cargo.toml`) so callbacks are resolved across files.

## Fix (`bumm-guard fix <path>... [--apply] [--format json|text]`)

One deterministic fixer per rule in `bumm_guard/fix/`, driven by the scan findings (only high/critical are touched):

| rule | fix | applied by `--apply` |
|---|---|---|
| BG-NEAR-001 | insert `#[private]` on its own line above `pub fn <callback>` (after existing attributes, same indentation) | yes |
| BG-NEAR-003 | `signer_account_id` -> `predecessor_account_id` in the flagged guard only (also `near_sdk::env::` form); REVIEW findings are never patched | yes |
| BG-NEAR-002 | `suggested_patch` text: add `#[callback_result] r: Result<(), PromiseError>` and an `if r.is_err() { /* TODO: revert .. */ return; }` skeleton | no: `applied: false, reason: "needs manual rollback"` |

Without `--apply` it prints unified diffs and writes nothing. With `--apply` it writes the files (CRLF kept), repeats until stable (one finding per method is reported, so a second guard shows up on the next pass), then re-scans and reports what remains. Files below a `target/` directory are never modified.

## Deploy gate (`bumm-guard gate <crate-dir> [--wasm PATH] [--network testnet|mainnet] [--account ID] [--dry-run] [--execute]`)

Scans the crate. Any high/critical finding: findings are printed, `DEPLOY BLOCKED`, exit `3`, no deploy is ever started and a stale report from an earlier run is deleted. Clean: writes `<crate-dir>/.bumm-guard/gate-report.json` (tool version, rule ids checked, findings count, sha256 of every scanned `*.rs`, sha256 of the wasm, UTC timestamp) and prints the exact command (near-cli-rs 0.30 syntax):

`near contract deploy <account> use-file <wasm> without-init-call network-config <network> sign-with-keychain send`

Default is print-only. `--execute` runs it, only with `--network testnet`, with `--account` and an existing `--wasm`, and only if `near` is on PATH. `--dry-run` skips writing the report. A `--wasm` path that does not exist is a warning in print-only mode (its sha256 is recorded as `null`).

## Exit codes

| code | `scan` | `fix` | `gate` |
|---|---|---|---|
| 0 | no high/critical finding | nothing to fix, or none left after `--apply` | clean (command printed; with `--execute`, deploy succeeded) |
| 1 | at least one high/critical | high/critical finding remains (always so in a dry run that has something to fix) | not used |
| 2 | bad path | bad path | bad input or refused: not a directory, bad account id, `--execute` on mainnet or without `--account`/`--wasm` |
| 3 | not used | not used | DEPLOY BLOCKED |
| 4 | not used | not used | `--execute` but `near` not on PATH |
| 5 | not used | not used | `near` ran and failed |

## Rules

| id | name | severity | what |
|---|---|---|---|
| BG-NEAR-001 | `non-private-callback` | high (info if read-only) | callback in a `#[near]` impl without `#[private]` |
| BG-NEAR-002 | `callback-ignores-failure` | high (medium with `#[callback_unwrap]`, info for pending markers) | state mutated before a cross-contract call, callback never handles failure |
| BG-NEAR-003 | `signer-auth` | high (low = REVIEW) | authorisation with `env::signer_account_id()` instead of `predecessor_account_id()` |

Opt-out for 003: a `// signer-ok` comment above or inside the method.

## Expected JSON

`bumm-guard scan tests/fixtures/near/d1_vuln --format json` (paths are as given on the command line):

```json
{
  "tool": "bumm-guard",
  "version": "0.2.0",
  "files_scanned": 1,
  "summary": {"total": 1, "by_severity": {"high": 1}, "by_rule": {"BG-NEAR-001": 1}},
  "findings": [
    {
      "rule_id": "BG-NEAR-001",
      "severity": "high",
      "file": "tests/fixtures/near/d1_vuln/src/lib.rs",
      "line": 23,
      "message": "Callback `on_paid` (&mut self) is publicly callable: ...",
      "fix_hint": "Add `#[private]` directly above `pub fn on_paid` ...",
      "snippet": "pub fn on_paid(&mut self, #[callback_result] r: Result<(), PromiseError>) {",
      "rule_name": "non-private-callback"
    }
  ],
  "errors": []
}
```

`bumm_guard.model.Finding.to_bumm_vulnerability()` maps a finding to the BUMM backend `Vulnerability` schema
(`severity, category, title, description, code_location, suggested_fix, source`).

## Layout

- `bumm_guard/rulepack/near/rust_src.py`: comment/string masking, brace matching, attributes, `impl`/method extraction.
- `bumm_guard/rulepack/near/{non_private_callback,callback_ignores_failure,signer_auth}.py`: one module per rule; `__init__.py` is the registry (`RULES`).
- `bumm_guard/fix/`: one fixer per rule (`non_private_callback.py`, `signer_auth.py`, `callback_ignores_failure.py`), engine and `FIXERS` registry in `__init__.py`.
- `bumm_guard/gate.py`: scan, report, deploy command.
- `tests/fixtures/near/`: seven compilable near-sdk crates (`d1..d3_vuln|safe`, `proxy`; `cargo check --workspace` from that folder).

## Honest limits

- Detection and fixes are regex/brace-level: no type information, no macro expansion. See `RULEPACK_REPORT.md` for detector limitations.
- Fixes are textual patches for the exact patterns the detectors flag. They are checked by re-scanning and, for the fixtures, by `cargo check`; they are not proof the contract is safe. Always read the diff.
- BG-NEAR-002 is not auto-fixed: a correct rollback needs semantic knowledge (what to restore, the callback's return type). You get a skeleton with a TODO, and the finding stays until you write the rollback.
- The gate only says "no high/critical finding from these three rules". It does not build the wasm, check that the wasm matches the scanned source (it records both hashes), or check anything beyond the three rules. `--execute` is testnet only by design.
