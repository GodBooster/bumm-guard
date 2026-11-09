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

**`actual.error_detail` (optional, 2026-11-03).** A failed `view` step is answered by the RPC
with a wrapper (`unable to fulfill the query request`); the contract's own text sits in the query
error's `vm_error`. bumm-sim extracts it, appends it to `actual.error` (`<wrapper>: <vm_error>`,
e.g. `… GuestPanic { panic_msg: "Cannot deserialize the contract state." }`) and repeats it alone in
`actual.error_detail`. `call` steps already carry the panic text in `actual.error`
(`Action #0: ExecutionError("Smart contract panicked: …")`) and have no `error_detail`; reports from
older runners have none either. `fail_contains` keeps matching against `actual.error`. CI's code-only
upgrade check requires `Cannot deserialize` in `actual.error` of every step after the upgrade step.

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
Added 2026-11-03: `migration_guard` (`{method, status, file, line}`, `null` without a migration call); `status` is
`private`, `owner-check` or, when the gate blocked, `not-found` / `unprotected` / `ambiguous`
(`not-found`: the method is not in the crate sources, so its access control cannot be checked).

**Gate hardening (v0.3, 2026-11-04).** New fields in the gate report and in the MCP `gate_deploy` /
`verify_deployed` results, and stricter proof acceptance:
- `deploy` (top level, always present in a written report): `{"status": "printed" | "no-change", "command":
  "near contract deploy …" | null}`. `printed`: the command was produced and printed (never run by the gate;
  with `--execute` the report is written before `near` runs, so its outcome is only in the exit code).
  `no-change`: the account already runs exactly `--wasm` (`view_code` hash = candidate hash): exit 0, the line
  `NO CHANGE: account <acc> (<net>) already runs this exact wasm (<hash>); nothing to deploy`, no command,
  `upgrade: null`, `simulate: null` (a report given with it is not checked against the wasm, only used for the
  storage estimate). The MCP result carries the same block as `deploy`, with `deploy_command: null`.
- A proof is accepted only when its upgrade step's `expected` is `"ok"` **and** `actual.status` is
  `"success"`. A scenario that expected the upgrade to fail (`{"fail_contains": …}`), saw it rolled back and
  therefore *passed* proves nothing; it is skipped with the note `upgrade scenario '<name>': the upgrade
  transaction did not succeed (status <s>[, expected <e>]); a rolled-back upgrade proves nothing`.
- `upgrade.post_upgrade_steps`: every step after the upgrade step, in order, as `[{index, kind, method}]`
  (`kind` is `view`, `call` or `balance`; `method` is the account for a `balance` step). Printed as
  `proved after upgrade (<n> step(s)): view deal_count, call refund, …`. A proof also needs a
  successful `call` step after the upgrade (the old warning for views only no longer exists, such a scenario is
  refused), exactly one `upgrade` step, and no step after the upgrade whose chain answer is a stored-state read
  failure (`Cannot deserialize` in `actual`, even when the step expected it; a repeated call of the migration
  method itself is exempt).
- `--scenario-sha256 HEX` (64 lowercase hex, else exit 2; upgrade only): only a scenario whose own
  `scenario_sha256` equals HEX is accepted as the proof. On an accepted proof the gate prints
  `scenario pin: <hash> (matches)`, or without the flag `note: no scenario pin (--scenario-sha256): the proof is
  whatever scenario the report's author chose to run`. MCP `gate_deploy` takes the same value as `scenario_sha256`.
- A failing upgrade scenario for the same hash pair and the same migration method as the accepted proof blocks
  (`DEPLOY BLOCKED: … does not outvote them`); a failing scenario with another migration (the code-only negative
  check next to a with-migrate proof) stays a note. With `--rpc-url` the gate prints `note: the code on <account>
  is read from a non-default RPC endpoint: <url>`. With `--account`, a `--wasm` path that does not exist is exit 2.
- Migrate args must be literal: if any string value in the proof's `migrate.args` (recursively) equals a
  scenario alias, the gate blocks (exit 3, `DEPLOY BLOCKED: migrate args of scenario '<name>' reference
  sandbox-only alias(es) 'alice': …`). Aliases are the keys of the scenario's `accounts` as bumm-sim records them
  in the §3 report (`{"alice": "alice.test.near", …, "contract": "contract.test.near"}`, not shown in the §3
  example above) plus `contract`; a report without `accounts` falls back to the `by` / `account` names of its
  steps. The runner resolves aliases only inside the sandbox; the printed command would send them as written.
- `upgrade_check` (MCP `gate_deploy` result when an upgrade is refused): `proven_pairs[]` entries gain
  `upgrade_status` (the upgrade step's `actual.status`) and `upgrade_succeeded` (expectation `ok` and status
  `success`); `status` gains `proof-upgrade-failed` (the pair is in the report but in no scenario did the upgrade
  transaction succeed) next to `no-report`, `no-proof` and `report-proves-other-pair`.
- `migration_guard.status` gains `owner-check-unknown`: a `predecessor_account_id()` comparison was seen in an
  `if`/`match` but no rejection (`panic!`, `env::panic_str`, `require!`/`assert!` family, `return`,
  `unreachable!`) was recognised in its body, or the comparison is a tautology (the same expression on both
  sides). It blocks like `unprotected`, with a message that names the cause and suggests `#[private]`.
  `owner-check` now means a recognised rejection: the comparison inside `require!`/`assert!`/`assert_eq!`/
  `assert_ne!`/`require_eq!`/`require_ne!`, or an `if`/`match` with such a rejection in its body (any branch).
- `post_deploy.smoke[]`: `--smoke METHOD[:ARGS_JSON][=EXPECTED_JSON]` (old forms unchanged). With an expectation
  the entry carries `expected` (parsed JSON) and is `ok` only if `result` equals it; on a mismatch `result` stays
  and `error` is `expected <json>, got <json>` (exit 5). Entry shape: `{view, args?, expected?, ok, result | error}`.

**Report text is cleaned before it is shown (2026-11-06).** A simulate report is supplied by whoever ran the
scenarios (in a guarded job: by the agent under review), so its strings are not trusted as terminal text. Every
string the gate takes from a §3 report (scenario `name`, `wasm_sha256` / `upgrade_wasm_sha256`, a step's `kind`,
`method`, `account` and `actual.status`, alias names) goes through one function (`bumm_guard/sanitize.py`,
`clean`) before it enters an output line, an error, `upgrade.scenario`, `upgrade.post_upgrade_steps`,
`upgrade_check.proven_pairs[]`, the MCP `messages` or a review's `reason`: characters of the Unicode categories
Cc, Cf, Cs, Zl and Zp (ESC, CR, LF, tab, NUL, DEL, C1 controls, bidi overrides, zero-width characters, lone
surrogates, line and paragraph separators) are removed and the rest is cut to 200 characters plus `...`. A
scenario name with a line break and an escape sequence therefore stays inside the one line that quotes it.
`migrate.args` stay as recorded (they are repeated in the command as JSON, where control characters are
escaped). A recorded `migrate.method` or `migrate.gas` that is empty, not a string, longer than 256 / 32
characters or changed by that cleaning is not accepted as a proof (note `the recorded migration method or gas is
not plain text …`), because it would be repeated in the printed command. `review-job` applies the same cleaning
to the text it reads from `get_job` (`agent`, `reward`, `target_account`, `status`) and strips control characters
from every gate line again; a `get_reviewer` answer that is not a valid account id is refused (exit 2). Not
covered: `scan` / `gate` print a finding's source `snippet` and file name as they are in the crate.

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

## 7. Review report (`.bumm-guard/review-<job>.json`, output of `bumm-guard review-job`, 2026-11-06)

Written by `review-job` (docs/SWARM_JOBS.md) after the job and the files checked out, whether the gate passed or
blocked; never on bad input and never when the job or the target account could not be read over RPC (exit 2, no
command: an RPC failure is not a verdict; the plain `gate` command still answers `DEPLOY BLOCKED`, exit 3, there). Its sha256 is the `gate_report_sha256` in the printed `approve` command, so the
file must be kept byte for byte (it carries a timestamp: a second run gives another hash).

**swarm_jobs v2 (2026-11-07).** The contract must be v2: `get_job` carries `scenario_sha256` (`null`, or the sha256
of the one scenario file the poster accepts) and `reward_storage_fee`. On a v1 contract (no `scenario_sha256` key in
`get_job`) the review exits 2 without a command or a report: the v2 `approve` it prints has five arguments,
`{"job_id", "agent", "wasm_sha256", "report_sha256", "gate_report_sha256"}` (`agent` is the job's agent as read;
`report_sha256` the submitted report hash), which v1 refuses. The §7 report of 2026-11-06 is unchanged except for
`checks.scenario_sha256` and the two new keys inside `job`.
```json
{
  "tool": "bumm-guard", "version": "0.5.0", "kind": "review-job",
  "timestamp_utc": "2026-11-06T12:00:00Z",
  "network": "testnet", "jobs_contract": "bumm-guard-jobs.testnet", "job_id": 0,
  "reviewer": "bumm-guard-bob.testnet",
  "job": {"id": 0, "poster": "…", "description": "…", "target_account": "bumm-guard-escrow2.testnet",
          "reward": "1000000000000000000000000", "status": "Submitted", "agent": "…",
          "wasm_sha256": "13425a04…", "report_sha256": "f305461f…", "gate_report_sha256": null, "reject_reason": null,
          "reward_storage_fee": "0", "scenario_sha256": null},
  "target_account": "bumm-guard-escrow2.testnet",
  "inputs": {"crate_dir": "tests/fixtures/near/escrow_v2", "wasm": "evidence/wasm/escrow_v2.wasm",
             "wasm_sha256": "13425a04…", "simulate_report": "evidence/escrow-upgrade-with-migrate-report-ci.json",
             "report_sha256": "f305461f…"},
  "checks": {"status_submitted": true, "wasm_sha256_matches": true, "report_sha256_matches": true,
             "scenario_sha256": null},
  "evidence_origin": "submission (not re-executed)",
  "gate": {"exit_code": 0, "passed": true, "messages": ["…"], "report": {"…": "§4 gate report"},
           "upgrade": {"…": "§4 upgrade block"}, "upgrade_check": null},
  "verdict": "approve",
  "reason": null
}
```
- `job` is `get_job` as read (read-only `call_function`, finality `final`); `reviewer` is the signer of the printed
  command (`--reviewer`, default the contract's `get_reviewer`).
- `checks.status_submitted`, `wasm_sha256_matches` and `report_sha256_matches` are always true in a written report:
  a mismatch stops the review before the gate (exit 2, no file). `checks.scenario_sha256` is `null` when the job
  has no pinned scenario, else `{"pinned": <the job's scenario_sha256>, "matches": bool, "report_scenario_sha256s":
  [<the scenario_sha256 of every scenario in the submitted §3 report, cleaned>]}`; `matches` is true only when the
  list is not empty and every entry equals `pinned`. A job whose `scenario_sha256` is present but not 64 lowercase
  hex is refused before the gate (exit 2), like a job whose `agent` is not a valid account id (the agent is
  repeated in the approve command).
- `evidence_origin` is always `"submission (not re-executed)"`: the scenario report and the crate were supplied
  with the submission. The review checked that the files are the submitted ones (the two hashes) and ran the gate
  on them; it did not re-execute the scenarios and did not rebuild the wasm from the crate. The same statement is
  printed as one `note:` line before the verdict line (`REVIEW PASSED` / `REVIEW BLOCKED`) and is in the MCP
  `review_job` result under the same key. A reviewer who needs more must take the report from a run they control
  (docs/SWARM_JOBS.md, Honest limits).
- `gate.report` is the §4 gate report the gate would have written (the gate runs with `--dry-run`, so the crate's own
  `gate-report.json` is not touched), `null` when it blocked; `gate.upgrade` / `gate.upgrade_check` as in §4 and in
  the MCP `gate_deploy` result.
- `verdict`: `approve` (gate exit 0 with a deploy command and, when pinned, `checks.scenario_sha256.matches`
  true; review exit 0) or `reject` (review exit 3): the pinned scenario did not match, the gate blocked (gate exit
  3), or the gate said NO CHANGE (gate exit 0, `gate.passed: true`, `gate.report.deploy.status: "no-change"`): the
  target account already runs exactly the submitted wasm, so the submission changed nothing. `reason` is `null` on
  approve; on reject it is, in this order of precedence, the pin sentence `the poster accepts only the scenario
  with sha256 <pinned>; the submitted report has <the distinct hashes found, sorted, or "no scenario at all">: run
  that scenario file and submit its report`, or the gate's first `DEPLOY BLOCKED` line, whitespace collapsed, cut
  to 500 bytes (the contract's limit), or, for NO CHANGE, the fixed sentence `the target account already runs this
  exact wasm: nothing was changed by this submission`; it is the `reason` argument of the printed `reject` command.
  `gate.passed` is the gate's own result, not the verdict (a pin mismatch with `gate.passed: true` is a reject).
  The printed `reject` carries `{"job_id", "reason", "close": false}`; a second line repeats it with `"close":
  true` (the job is cancelled and the poster refunded by the contract) for the reviewer to choose. The MCP result
  carries them as `command` and `close_command`.
