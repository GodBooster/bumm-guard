# Shared formats: scenario runner, ABI panel, gate report (v0.1, 2026-11-02)

These formats are the contract between the three parts built in parallel: the scenario
runner (`bumm-guard simulate`), the ABI interaction panel (`ui/`), and the gate
(`bumm-guard gate`). Change them here first; code follows the file.

## 1. Build artifact (`artifact.json`, produced by `bumm-guard build`)

```json
{
  "crate_dir": "contracts/event_registration",
  "wasm": "target/near/event_registration.wasm",
  "wasm_sha256": "…",
  "abi": "target/near/event_registration_abi.json",
  "abi_sha256": "…",
  "toolchain": {"rustc": "1.85.0", "cargo_near": "0.22.0", "near_sdk": "5.17.2"},
  "built_at": "2026-11-03T10:00:00Z"
}
```
`abi` is the `cargo near abi` output (near-abi schema). The panel reads only `abi`;
the runner reads `wasm`; the gate records both hashes.

## 2. Scenario file (`scenarios/*.json`, input to `bumm-guard simulate`)

One file = one independent scenario; the runner starts a fresh sandbox per file.
No loops, no scripting, no variables other than the named accounts and the single
contract. Values are JSON; NEAR amounts are strings in yoctoNEAR or `"1 NEAR"`.

```json
{
  "name": "N06 capacity 2, third refused, cancel frees a seat",
  "accounts": {"alice": "10 NEAR", "bob": "10 NEAR", "carol": "10 NEAR"},
  "contract": {"wasm": "target/near/event_registration.wasm",
               "init": {"method": "new", "args": {"capacity": 2}}},
  "steps": [
    {"call": "register", "by": "alice", "args": {}, "deposit": "0", "gas": "30 Tgas",
     "expect": "ok"},
    {"call": "register", "by": "bob", "expect": "ok"},
    {"call": "register", "by": "carol",
     "expect": {"fail_contains": "Event is full"}},
    {"view": "get_registered_count", "expect": {"equals": 2}},
    {"call": "cancel", "by": "alice", "expect": "ok"},
    {"call": "register", "by": "carol", "expect": "ok"},
    {"view": "get_registered", "expect": {"contains": "carol"}},
    {"balance": "bob", "expect": {"at_least": "9 NEAR"}}
  ]
}
```
Step kinds: `call` (change method; defaults `deposit: "0"`, `gas: "30 Tgas"`), `view`,
`balance`. Expectations: `"ok"`, `{"fail_contains": str}`, `{"equals": json}`,
`{"contains": json}`, `{"at_least"|"at_most": amount}`. `by` is an account from
`accounts`; `"contract"` may be used as the caller for self-calls.

## 3. Scenario result (`<out>/simulate-report.json`, output of `bumm-guard simulate`)

```json
{
  "tool": "bumm-guard", "version": "0.4.0",
  "wasm_sha256": "…", "scenario_sha256": "…",
  "sandbox": {"near_workspaces": "0.24.0", "near_sandbox": "0.4.0"},
  "scenarios": [
    {"name": "…", "passed": true, "duration_s": 12.4,
     "steps": [
       {"index": 2, "kind": "call", "method": "register", "by": "carol",
        "passed": true, "expected": {"fail_contains": "Event is full"},
        "actual": {"status": "failure", "error": "Smart contract panicked: Event is full: capacity 2 reached", "gas_burnt_tgas": 2.9}},
       {"index": 3, "kind": "view", "method": "get_registered_count",
        "passed": true, "expected": {"equals": 2}, "actual": {"value": 2}}
     ]}
  ],
  "summary": {"scenarios": 1, "passed": 1, "failed": 0, "steps": 8, "steps_failed": 0}
}
```
`actual` always carries what the chain returned (status, error text, return value,
gas), so a failed expectation is auditable without re-running. The hashes bind the
report to one wasm and one scenario file; the gate refuses a report whose
`wasm_sha256` differs from the artifact it is gating.

## 4. Gate report (`.bumm-guard/gate-report.json`, extended)

Existing fields stay. Added:
```json
{
  "artifact": {"wasm_sha256": "…", "abi_sha256": "…"},
  "simulate": {"report": ".bumm-guard/simulate-report.json", "passed": true, "scenarios": 3},
  "storage_estimate": {
    "code_bytes": 225057,
    "state_bytes_after_init": null,
    "storage_amount_per_byte_yocto": "10000000000000000000",
    "source": "EXPERIMENTAL_protocol_config via RPC testnet, 2026-11-03",
    "locked_near_for_code": "2.25",
    "note": "storage staking is a locked balance, not a fee; state bytes are measured by simulate when available"
  },
  "post_deploy": {
    "account": "demo.testnet",
    "code_hash_on_chain": "…", "matches_artifact": true,
    "smoke": [{"view": "get_registered_count", "ok": true}],
    "note": "init is never re-called; a contract that fails the smoke view is reported, not fixed"
  }
}
```
`post_deploy` is filled by `bumm-guard gate --verify-deployed <account>` after the
human has run the printed deploy command with their own key (deploy needs a
full-access key; the gate never holds one).

## 5. ABI panel inputs (`ui/`, static page, no backend)

Reads `artifact.abi` (or a URL to it) plus `{network, contract_id}`. Renders one form
per `body.functions[]`: `kind: view` → free call via RPC; `kind: call` → wallet
transaction with `deposit` and `gas` fields (defaults 0 / 30 Tgas; `payable` methods
show deposit prominently; `private` methods are listed but disabled). Argument fields
come from `params.args[].type_schema`: string, integer, boolean, AccountId-like
strings, arrays of those; `$ref` into `body.root_schema.definitions` is followed one
level. Anything else gets a raw JSON textarea with the schema shown next to it and a
visible "unsupported type, enter JSON" marker. This is an interaction panel, not a
product UI, and the README says so.

## 6. Independent acceptance for N06 / N10 (study and demo)

N06 scenarios: the file in §2 plus "duplicate registration refused" and "cancel
without registration refused". N10 starts from the frozen N06 starter commit and adds
waitlist scenarios: "third registers → waitlisted", "cancel promotes first waitlisted",
"order preserved". Acceptance = all scenario files pass in `simulate`, the panel can
perform `register` and show `get_registered` on testnet, and `gate --verify-deployed`
reports `matches_artifact: true`. Both study arms (with and without BUMM) get this
full definition, not only "contract compiles".
