---
name: near-deploy-gate
description: Use before building or deploying a NEAR (near-sdk-rs) smart contract, or when asked to check, fix or ship one. Runs the BUMM Guard scan, fix and deploy gate through the bumm-guard MCP tools.
---

# NEAR deploy gate

Tools (MCP server `bumm-guard`): `list_rules`, `scan_contract`, `fix_contract`, `gate_deploy`.
Paths are relative to the directory the agent was started in; paths outside it are refused.

Before building or deploying a NEAR contract, in this order:

1. `scan_contract(path=<crate dir>)`. No findings: go to step 4. Findings: show the human the rule id, file, line and message of each.
2. `fix_contract(path=<crate dir>)` with the default `apply=false`. This writes nothing and returns diffs. Show the diff to the human. Call it again with `apply=true` only after the human says yes.
   - BG-NEAR-002 is never auto-fixed: the result has a `suggested_patch` skeleton. Write the rollback yourself, with the human, then rescan.
3. `scan_contract` again until no high/critical finding remains.
4. Run the crate's tests (`cargo test`, and the project's sandbox tests if it has them). Do not continue on failing tests.
5. `gate_deploy(crate_dir=<crate dir>, wasm=<built .wasm>, account=<account id>, network="testnet")`.
   - `blocked: true`: stop. Go back to step 1. Never bypass, skip or work around a blocked gate (no editing the report, no running the deploy by hand).
   - `passed: true`: show the human `deploy_command`. The tool never runs a deploy. The human runs the command.

Rules:
- Use `network="mainnet"` only when the human asks for it. Deploying to mainnet is always the human's decision; do not suggest running the command for them.
- The gate covers three rules (see `list_rules`). A pass is not a security audit.
- Do not set `BUMM_GUARD_ALLOW_ANY_PATH` or change the server's configuration to get around a refused path.
