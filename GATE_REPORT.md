# Deploy gate extension (BUMM Guard 0.5.0): report, 2026-11-02

> Build report of 2026-11-02; numbers are of that date, the README is current.

Contract: `docs/FORMATS.md` §4 (gate report). Nothing was committed; `README.md`, `.github/`, `scripts/`,
`docs/`, `ui/`, `contracts/`, `scenarios/` were not touched. No NEAR account, deploy or transaction was used.

## What was built

| path | what |
|---|---|
| `bumm_guard/storage.py` | The single RPC URL table (`RPC_URLS`: testnet/mainnet, `--rpc-url` overrides), `rpc_call` (urllib, 10 s timeout, http(s) only), `fetch_per_byte` (`EXPERIMENTAL_protocol_config`, `{"finality":"final"}`, falls back to 1e19 yocto/byte with `source: "default (RPC unavailable)"`), exact integer `locked_near` (4 decimals, half up), `account_info` (`view_account`), `estimate_storage`. |
| `bumm_guard/verify.py` | `verify_deployed` (`view_code` -> base64 -> sha256 -> compare with the local wasm; each `--smoke` is a `call_function` view call), `parse_smoke`. |
| `bumm_guard/gate.py` | `artifact`, `simulate`, `storage_estimate`, `post_deploy` keys in the report; `load_simulate_report`; `run_verify_deployed`. |
| `bumm_guard/cli.py` | New `gate` flags (below). |
| `bumm_guard/mcp_server.py` | Tools `estimate_storage(wasm, network)` and `verify_deployed(account, wasm, network, smoke?)`, read-only, no execute parameter, same path-confinement as the others. |
| tests | `tests/test_gate_ext.py` (new, 31 tests, one of them the live-RPC test skipped by default), `tests/test_mcp.py` (+2 tests, tools/list now 6). All urllib calls stubbed. |
| version | 0.5.0 in `pyproject.toml`, `bumm_guard/__init__.py`, `clients/claude-plugin/.claude-plugin/plugin.json`. |

### CLI

```
bumm-guard gate <crate-dir> --wasm PATH [--abi PATH] [--simulate-report PATH]
                [--estimate-storage] [--account ID] [--network testnet|mainnet] [--rpc-url URL]
                [--dry-run | --execute]
bumm-guard gate [<dir>] --verify-deployed ACCOUNT --wasm PATH [--network N] [--rpc-url URL]
                [--smoke METHOD[:ARGS_JSON]]...
```
Exit codes: `0` ok; `2` bad input or refusal; `3` DEPLOY BLOCKED (high/critical finding, simulate report for a
different wasm, failed or no scenarios); `4` `--execute` without `near`; `5` in deploy mode: `near` failed, in
`--verify-deployed` mode: hash mismatch, a failed smoke view, or the RPC/contract unreachable. The two modes never
mix (`--verify-deployed` with `--execute`, `--simulate-report`, `--abi`, `--account` or `--estimate-storage` is
exit 2), so 5 is unambiguous per mode, but it is the same number; scripts must know which mode they ran.

### Decisions where the brief left room (please check)
- **Storage estimate is opt-in (`--estimate-storage`)**, not run on every `gate`. Reason: the existing gate tests and
  every dry run would otherwise call the public RPC (the owner's traffic is limited), and `--network` already has a
  default. Without the flag `"storage_estimate": null`. If you want it default-on, change one line in `run_gate`.
- `--simulate-report` and `--estimate-storage` need an existing `--wasm` (exit 2 otherwise): the binding is by hash.
- A blocked simulate binding behaves like a blocked scan: nothing printed to run, a stale `gate-report.json` is deleted.
- `state_bytes_after_init` is read from `storage_usage` at the top level of the simulate report, else the largest
  per-scenario `storage_usage`; `bumm-sim` does not emit it today, so it is `null` in practice (the output says so).
- `--smoke` refuses method names `new`, `init*`, `migrate*` (exit 2). A view call cannot change state anyway; this is
  a second guard against pointing the smoke at an init method.
- Report keys are exactly §4. Extra keys appear only when asked or on failure: `storage_estimate.account` (with
  `--account`), `post_deploy.error` (code not fetchable), `smoke[].result/error/args`.
- `--verify-deployed` merges `post_deploy` into `<dir or .>/.bumm-guard/gate-report.json` (creates a minimal report if
  absent). It does not rescan the crate. The MCP `verify_deployed` does not write the report (read-only).

## README snippet (proposed at the time)

```markdown
### Gate with scenario evidence and storage estimate
Do:
    bumm-guard simulate scenarios/n06/*.json --out .bumm-guard/simulate-report.json
    bumm-guard gate contracts/event_registration \
        --wasm target/near/event_registration.wasm --abi target/near/event_registration_abi.json \
        --simulate-report .bumm-guard/simulate-report.json --estimate-storage --account demo.testnet
See: `gate passed`, the printed deploy command (never executed unless you pass `--execute` on testnet) and
`.bumm-guard/gate-report.json` with `artifact`, `simulate` and `storage_estimate`. A report made for another wasm or
with a failed scenario gives `DEPLOY BLOCKED` (exit 3). Without `--simulate-report` the gate warns
"no scenario report: behaviour not verified". Storage staking is a locked balance, not a fee.

### Verify after you deployed
Do (after running the printed deploy command with your own key):
    bumm-guard gate --verify-deployed demo.testnet --wasm target/near/event_registration.wasm \
        --network testnet --smoke get_registered_count --smoke 'is_registered:{"account_id":"alice.testnet"}'
See: `MATCH` plus `smoke get_registered_count: ok: 0`, exit 0, and `post_deploy` in the gate report
(`matches_artifact: true`). Exit 5 if the on-chain code differs, a smoke view fails or the RPC is unreachable.
init is never called.
```

## Live RPC check (2026-11-02, public testnet)

`EXPERIMENTAL_protocol_config` `{"finality":"final"}` on `https://rpc.testnet.near.org` returned
`runtime_config.storage_amount_per_byte = 10000000000000000000` (1e19 yocto/byte), equal to the documented default.
Run once via `BUMM_GUARD_LIVE_RPC=1 python3 -m unittest tests.test_gate_ext.LiveRpcTests` (2 RPC calls, the second
through `fetch_per_byte`). For the 181 KB `event_registration` wasm that is about 1.8 NEAR locked (181 000 x 1e19 / 1e24).

## Limits

- No state-size estimate before init: state bytes are only known if a simulate report carries `storage_usage`
  (not emitted by `bumm-sim` yet), otherwise `null`. `locked_near_for_code` covers the code only, not state, access keys
  or the account's own storage.
- No gas estimate at all (the simulate report has per-call burnt gas; the gate does not interpret it).
- `--verify-deployed` and `--smoke` were tested against a stubbed RPC only; no real deployed contract was queried
  (there is no deployed N06 contract yet). The `view_code` and `call_function` response shapes follow the NEAR RPC docs
  as implemented in the stub, and have not been seen from a live node.
- `--account` balance is reported next to the lock, never judged.
- Not wired into the MCP `gate_deploy` tool: it still takes no `--simulate-report`/`--abi`, so over MCP the report says
  `simulate: null`. Adding two optional params is small if wanted.

## CI suggestion (`simulate` job, after the "run N06 scenarios" step)

```yaml
      - name: gate with simulate evidence
        run: |
          python -m bumm_guard gate tests/fixtures/near/event_registration \
            --wasm tests/fixtures/near/target/wasm32-unknown-unknown/release/event_registration.wasm \
            --simulate-report simulate-out/simulate-report.json --dry-run
```
`--dry-run` keeps the runner clean; drop it to upload `gate-report.json` as an artifact. Do not add `--estimate-storage`
in CI (it calls the public RPC; it falls back safely, but adds a flaky network dependency). A wasm built by the CI step
is the one the simulate report was produced for, so a hash mismatch here means the two steps used different builds.
