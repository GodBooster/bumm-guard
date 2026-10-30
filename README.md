# BUMM Guard (NEAR rule-pack)

Static analysis for NEAR smart contracts (near-sdk-rs 5.x). Python 3.12+, standard library only.
Finds three defect classes before deploy; output is JSON for machines, text for humans.

## Quickstart (machine reader)

1. Install (from this directory): `python3 -m pip install -e .`  (or skip install and use `python3 -m bumm_guard ...` from this directory).
2. Scan a vulnerable fixture: `bumm-guard scan tests/fixtures/near/d1_vuln --format json` -> see "Expected JSON" below. Exit code `1` (a high-severity finding exists).
3. Scan everything in the fixtures: `bumm-guard scan tests/fixtures/near` -> 3 findings (BG-NEAR-001, -002, -003), exit code `1`.
4. Scan a safe fixture: `bumm-guard scan tests/fixtures/near/d1_safe` -> 0 findings, exit code `0`.
5. List rules: `bumm-guard rules` (add `--format json` for JSON).
6. Run tests: `python3 -m unittest discover -s tests`

`bumm-guard scan <path>...` takes files or directories (recursive `*.rs`, skips `target/`, `.git`, `node_modules`;
extra `--exclude DIRNAME`). `--format json|text` (default text). Exit codes: `0` no high/critical finding,
`1` at least one, `2` bad path. Files are grouped per crate (nearest `Cargo.toml`) so callbacks are resolved across files.

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
  "version": "0.1.0",
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
- `tests/fixtures/near/`: seven compilable near-sdk crates (`d1..d3_vuln|safe`, `proxy`; `cargo check --workspace` from that folder).

Regex-level analysis: no type information, no macro expansion. See `RULEPACK_REPORT.md` for limitations.
