---
name: near-deploy-gate
description: Use before building, deploying or upgrading a NEAR (near-sdk-rs) smart contract, or when asked to check, fix or ship one. Runs the BUMM Guard scan, fix and deploy gate (with the upgrade proof for accounts that already run code) through the bumm-guard MCP tools.
---

# NEAR deploy gate

Tools (MCP server `bumm-guard`): `list_rules`, `scan_contract`, `fix_contract`, `gate_deploy`, `estimate_storage`, `verify_deployed`.
Paths are relative to the directory the agent was started in; paths outside it are refused.
No tool signs or sends a transaction. Only `fix_contract(apply=true)` writes source files, and `gate_deploy` writes `<crate>/.bumm-guard/gate-report.json` on a pass.

Before building or deploying a NEAR contract, in this order:

1. `scan_contract(path=<crate dir>)`. No findings: go to step 4. Findings: show the human the rule id, file, line and message of each.
2. `fix_contract(path=<crate dir>)` with the default `apply=false`. This writes nothing and returns diffs. Show the diff to the human. Call it again with `apply=true` only after the human says yes.
   - BG-NEAR-002 is never auto-fixed: the result has a `suggested_patch` skeleton. Write the rollback yourself, with the human, then rescan.
3. `scan_contract` again until no high/critical finding remains.
4. Run the crate's tests (`cargo test`, and the project's sandbox tests if it has them). Do not continue on failing tests.
5. If the target account already runs a contract, this is an upgrade of live state: the new code must read the stored state, or every call panics with `Cannot deserialize the contract state.` after the deploy. Before the gate, run an upgrade scenario (`docs/FORMATS.md` §2a): it deploys the account's current wasm in near-sandbox, writes state, deploys the candidate on the same account with its `migrate` call (`#[private] #[init(ignore_state)]`) in one batch, and checks the migrated state. `bumm-guard simulate <scenario> --runner <bumm-sim>` runs it (Linux; in this repository the CI job `scenario runner`). Keep the `simulate-report.json`. If you cannot run it where you are (no Linux, no `bumm-sim`, no cargo), do not skip to the gate as if it passed: stop and ask the human for the report of a CI run (in this repository, the `simulate-report` artifact of the `scenario runner` job), then continue with step 6.
6. `gate_deploy(crate_dir=<crate dir>, wasm=<built .wasm>, account=<account id>, network="testnet", simulate_report=<report path, if any>)`.
   - `blocked: true` with `blocking_findings > 0`: stop. Go back to step 1.
   - `blocked: true` and `messages` say the account already runs code and no passing upgrade scenario proves `<onchain> -> <candidate>`: stop. Do not deploy code-only. Write or fix the `migrate` method and the upgrade scenario (step 5), re-run it, and pass its report. Never bypass, skip or work around a blocked gate (no editing the report, no running the deploy by hand).
   - `blocked: true` and `upgrade_check` is present: the account runs `onchain_code_sha256`, the candidate is `candidate_sha256`, and `proven_pairs` lists what the report actually proves (`from -> to`, scenario name, passed). `status: "report-proves-other-pair"` means the report is real but for other code than the one on this account (for example the account runs a different contract): stop and tell the human which pair is missing; `status: "no-report"` means no report was given; `status: "no-proof"` means the report has no passing scenario for this pair.
   - `passed: true`: show the human `deploy_command`. For an upgrade it repeats the proven `migrate` call (`with-init-call`), and `upgrade` names the pair of hashes and the scenario that proved it. `estimate_storage(wasm=<built .wasm>)` tells how much NEAR the deploy locks for storage. The tool never runs a deploy. The human runs the command.
7. After the human has deployed: `verify_deployed(account=<account id>, wasm=<built .wasm>, smoke=["<view method>", ...])` checks that the code on chain is the proven file (`matches_artifact`) and that the view methods answer. It never calls `new`, `migrate` or any change method.

Rules:
- Use `network="mainnet"` only when the human asks for it. Deploying to mainnet is always the human's decision; do not suggest running the command for them.
- The gate covers three rules (see `list_rules`) plus the upgrade proof. A pass is not a security audit.
- Do not set `BUMM_GUARD_ALLOW_ANY_PATH` or change the server's configuration to get around a refused path.
