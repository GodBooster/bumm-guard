# Shared formats: scenario runner, ABI panel, gate report (v0.1, 2026-11-02)

These formats are the contract between the three parts built in parallel: the scenario
runner (`bumm-guard simulate`), the ABI interaction panel (`ui/`), and the gate
(`bumm-guard gate`). Change them here first; code follows the file.

## 1. Build artifact (`artifact.json`)

**Not implemented in this release:** there is no `bumm-guard build` subcommand (the CLI has
`scan`, `rules`, `fix`, `gate`, `simulate`). `artifact.json` is produced by CI or written by
hand; `gate` takes `--wasm` and `--abi` paths directly and records both hashes in its report.
The format stays here because the panel and the runner read its fields.

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

### 2a. Upgrade step (v0.2, 2026-11-03)

Proves a code upgrade of a contract that already holds state. Deploys new code to the
**same** contract account, signed by the contract account itself, optionally followed by
a migration call in the same transaction (a NEAR batch: `DeployContract` + `FunctionCall`).
State written by earlier steps stays in place, so the steps after it run against migrated
state.

```json
{"upgrade": "path/to/new.wasm",
 "migrate": {"method": "migrate", "args": {}, "gas": "100 Tgas"},
 "expect": "ok"}
```
- `upgrade` (required): wasm path, resolved like `contract.wasm`; `bumm-guard simulate
  --upgrade-wasm PATH` overrides it for every upgrade step (used to score a candidate build).
- `migrate` (optional): omit for a code-only upgrade. `args` default `{}`, `gas` default
  `"100 Tgas"`.
- `expect`: same vocabulary as `call` (`"ok"`, `{"fail_contains": …}`), applied to the
  deploy transaction. A code-only upgrade "succeeds" even when the new code cannot read the
  old state; the failure then shows up in the following steps, which is exactly what the
  scenario is for.
- Result (`actual`): `status`, `error`, `gas_burnt_tgas`, plus `code_sha256` of the wasm that
  was deployed. The report carries `upgrade_wasm_sha256` at scenario and top level (null when
  a scenario has no upgrade step), so a gate can bind evidence to both the old and new wasm.

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
`version` is the version of the runner's report format (`bumm-sim` writes `0.4.0`), not the
version of the Python package (`pyproject.toml`: `0.5.0`).
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

**Upgrade proof (v0.2, 2026-11-03).** With `--account` and `--wasm` the gate reads the
account's code (read-only RPC `view_code`). No code (`NO_CONTRACT_CODE` / `UNKNOWN_ACCOUNT`):
a fresh deploy, `"upgrade": null`, judged as above. The same code as `--wasm`: nothing changes,
`"upgrade": null`. Other code: an upgrade of a live contract. The gate then needs
`--simulate-report` with a **passing** scenario that has an `upgrade` step (§2a) whose scenario
`wasm_sha256` equals the on-chain code hash and whose `upgrade_wasm_sha256` (and the step's
`code_sha256`) equals the `--wasm` hash; otherwise exit 3 (`DEPLOY BLOCKED: account X already
runs code <hash>; this is an upgrade and no passing upgrade scenario proves <onchain> -> <candidate>`).
An unreadable `view_code` answer also blocks (exit 3): unknown is never treated as empty.
```json
{
  "upgrade": {
    "onchain_code_sha256": "c3f50addfb06…",
    "candidate_sha256": "…",
    "scenario": "Upgrade N06 → N10 (upgrade with migrate()): …",
    "migrate": {"method": "migrate", "args": {}, "gas": "100 Tgas"},
    "passed": true
  }
}
```
`migrate` is copied from the upgrade step's `migrate` in the §3 report (`{method, args, gas}`,
args as written in the scenario), or `null` for a code-only upgrade. The printed command is
then one batch transaction like the simulated step: `near contract deploy <acc> use-file <wasm>
with-init-call <method> json-args '<args>' prepaid-gas '<gas>' attached-deposit '0 NEAR'
network-config <net> sign-with-keychain send` (`without-init-call` when `migrate` is `null`).
In upgrade mode `simulate.passed` is true only if every scenario in the report passed (a
deliberately failing code-only scenario is allowed next to the proof and noted).

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
