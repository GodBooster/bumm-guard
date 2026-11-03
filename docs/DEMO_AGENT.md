# Demo: an agent asks to upgrade, the gate refuses (K5)

Rehearsal script for the video frame "the agent is told to upgrade a live contract; BUMM Guard
blocks it until an upgrade proof exists". Everything here is reproducible by a judge with the
public repository, a testnet account and Claude Code. Owner-only parts (keys, transactions)
are marked **owner**.

## Setup (once)

1. `python3 -m venv .venv-mcp && .venv-mcp/bin/pip install -e '.[mcp]'`, then make
   `bumm-guard-mcp` reachable on `PATH` (e.g. `ln -s "$PWD/.venv-mcp/bin/bumm-guard-mcp" ~/.local/bin/`).
2. `.mcp.json` in the repository root registers the server for Claude Code (project scope); the
   first session in this directory asks to approve it. Alternative: `claude mcp add bumm-guard -- bumm-guard-mcp`.
3. The wasm files the proofs are bound to are CI artifacts of run
   [41679210002](https://github.com/GodBooster/bumm-guard/actions/runs/41679210002):
   `escrow_v1-wasm` (sha256 `ecd8a805ea32…`) and `escrow_v2-wasm` (`13425a042dec…`). Local builds differ;
   download these. In this rehearsal they sit in `e2e/escrow/v1/escrow_v1.wasm` and `e2e/escrow/v2/escrow_v2.wasm`
   (inside the repository, gitignored: the MCP server refuses paths outside its start directory).

## Part A — needs no new account (works today)

Open Claude Code in the repository directory. Prompt, verbatim:

> Онови контракт на bumm-guard-demo.testnet до escrow v2: крейт `tests/fixtures/near/escrow_v2`,
> wasm `e2e/escrow/v2/escrow_v2.wasm`. Перед деплоєм перевір через bumm-guard.

Expected: the agent calls `scan_contract` (0 findings), then `gate_deploy(crate_dir, wasm, account="bumm-guard-demo.testnet")`
and gets `blocked: true` with:

```
DEPLOY BLOCKED: account bumm-guard-demo.testnet already runs code 8526195a…; this is an upgrade and no
passing upgrade scenario proves 8526195a… -> 13425a04…
```

If the agent then passes `simulate_report=evidence/escrow-upgrade-with-migrate-report-ci.json`, it is still
blocked: the note says that scenario proves `ecd8a805… -> 13425a04…`, not `8526195a… -> 13425a04…`. The
proof is bound to the code that is actually on the account. The agent must stop here (skill
`near-deploy-gate`, step 6) and tell the human what is missing; it must not deploy by hand.

Observed 2026-11-03 with the CLI (`python3 -m bumm_guard gate … --account bumm-guard-demo.testnet --dry-run`): exactly these two messages, exit 3.

## Part B — a real upgrade of escrow on testnet (**owner**)

B0. **owner** creates `bumm-guard-escrow.testnet` (faucet) and deploys escrow v1 through the gate:

```
python3 -m bumm_guard gate tests/fixtures/near/escrow_v1 --wasm e2e/escrow/v1/escrow_v1.wasm \
  --account bumm-guard-escrow.testnet --network testnet --estimate-storage
```
prints `account … has no contract code: fresh deploy` and the `near contract deploy … without-init-call …`
command; **owner** runs it, then initialises and creates one deal (buyer = `bumm-guard-demo.testnet`, seller = the escrow account):

```
near contract call-function as-transaction bumm-guard-escrow.testnet new json-args '{}' prepaid-gas '30 Tgas' attached-deposit '0 NEAR' sign-as bumm-guard-escrow.testnet network-config testnet sign-with-keychain send
near contract call-function as-transaction bumm-guard-escrow.testnet create_deal json-args '{"seller":"bumm-guard-escrow.testnet"}' prepaid-gas '30 Tgas' attached-deposit '1 NEAR' sign-as bumm-guard-demo.testnet network-config testnet sign-with-keychain send
```

B1. Prompt to the agent, verbatim:

> Онови bumm-guard-escrow.testnet до escrow v2: крейт `tests/fixtures/near/escrow_v2`, wasm `e2e/escrow/v2/escrow_v2.wasm`.
> Доказ оновлення з CI лежить у `evidence/`. Перевір через bumm-guard і дай мені команду.

Expected, in order:
1. `gate_deploy` without a report → blocked (`no passing upgrade scenario proves ecd8a805… -> 13425a04…`).
2. With `evidence/escrow-upgrade-code-only-report-ci.json` → blocked: `upgrade scenario … for this pair did not pass`.
3. With `evidence/escrow-upgrade-with-migrate-report-ci.json` → `passed: true`, `migration migrate: #[private] (src/lib.rs:…)`,
   `upgrade` block naming the pair and the scenario, and `deploy_command` =
   `near contract deploy bumm-guard-escrow.testnet use-file e2e/escrow/v2/escrow_v2.wasm with-init-call migrate json-args '{}' prepaid-gas '100 Tgas' attached-deposit '0 NEAR' network-config testnet sign-with-keychain send`.
   The tool never runs it.

B2. **owner** runs the printed command. Then:

```
python3 -m bumm_guard gate --verify-deployed bumm-guard-escrow.testnet --wasm e2e/escrow/v2/escrow_v2.wasm --network testnet \
  --smoke deal_count --smoke 'get_deal:{"id":0}'
```
Expected: `matches_artifact: true`, `deal_count` = 1, `get_deal` shows the pre-upgrade deal with `status: "Funded"` and `created_at_ms: 0`
(the deal made on v1 survived the migration). Record tx hashes in `evidence/UPGRADE_TESTNET.md`.

## Video frames this gives

- Agent prompt → `DEPLOY BLOCKED` with the pair of hashes (Part A or B1.1).
- The same with the code-only report → "did not pass" (B1.2).
- With the migrate proof → command printed, `#[private]` confirmed (B1.3).
- `--verify-deployed`: the deal survived (B2).
