# Submission text: BUMM Guard

Drafts for the owner to edit and publish. Nothing here has been posted or submitted. Every claim is backed by a file in this repository or by the CI run named next to it; anything not done is listed as not done. Placeholders in `{braces}` are filled by the owner on 13.11 (the repository is private until then).

## 1. Form field "What did you build?" (limit 2000 characters; this text: 1992 characters, counted with `wc -m` on the text between the markers)

<!-- form-text-start -->
BUMM Guard proves that a change to a NEAR contract works on the state that is already deployed, before an agent or a human deploys it.

The problem: in NEAR, redeploying code over a contract whose stored state has a different layout, without a migration, leaves a contract that panics on every call ("Cannot deserialize the contract state."). Unit tests and fresh-deploy scenarios do not see it.

What it does: the `upgrade` step of the scenario runner deploys the old wasm in a local near-sandbox, writes state with transactions, deploys the new wasm on the same account (with its `migrate` call) and runs checks on the upgraded state. The deploy gate reads the account's code hash and, if it already runs code, prints the deploy command only when a passing upgrade scenario proves that exact pair of hashes. Around it: a static rule-pack (3 NEAR defect classes, 2 auto-fixed), an MCP server (6 tools) and an ABI panel.

Evidence:
- Sandbox, CI on every push, two pairs: migrate 16/16 and 33/33; code-only 4/16 and 9/17 (the escrow smoke view still answers, record reads panic).
- A/B pilot (add a waitlist), same model and task, ONE run per arm: a pilot, not statistics. Fresh deploy: both arms 3/3, 35/35 steps. Upgrade of a contract with state: the arm with migrate() passed 16/16; the arm that passed every fresh-deploy check had no migration.
- Testnet, live, two pairs: gate blocked without proof and with a failing one, passed with the migrate proof; the owner ran the printed deploy+migrate tx; pre-upgrade state survived (a registration; a 1 NEAR escrow deal).

Not done: sandbox state is the scenario's own transactions, not copied from a live network; the panel's wallet path is unverified; no third-party contract run.

Prior work: BUMM, the author's Solana builder, is a separate project, not in this repo. Built in the hackathon window; written with Claude Code.

Try: python3 -m bumm_guard simulate scenarios/upgrade/*.json --validate-only. Real run: CI job "scenario runner".
<!-- form-text-end -->

Field notes: the form allows one submission per repository and no edits, so fill it only after the repository is public and the numbers above are rechecked against the latest CI run (this text cites run 41619178923 in the README; the form text deliberately names no run id).

## 2. X post

Main post (limit 280 characters; a link counts as 23 on X; this text counts 274 with the repo link; attach the video):

<!-- x1-start -->
A NEAR contract can pass every test and still panic on every call after a redeploy: "Cannot deserialize the contract state." BUMM Guard proves the upgrade on real state in near-sandbox first. Sandbox: migrate() 16/16, code-only 4/16. Done on testnet. {REPO}
<!-- x1-end -->

Thread, reply 1 (259 characters):

<!-- x2-start -->
Pilot: same task (add a waitlist), same model, one run per arm. Fresh deploy: both 3/3, 35/35, the plain agent faster. Upgrade of a contract with state: only the arm that wrote migrate() survived. One run each, not statistics. Record: evidence/AB_PILOT_N10.md
<!-- x2-end -->

Thread, reply 2 (278 characters, recounted 04.11; check the official hashtag on the day):

<!-- x3-start -->
Live on testnet, two pairs: the gate refused without proof, passed with the migrate() proof, owner ran the printed tx, a registration and a 1-NEAR escrow deal survived (evidence/UPGRADE_TESTNET.md). Not done: wallet path. Rule-pack, fixers, MCP, panel: same repo. #NEARHACKATHON
<!-- x3-end -->

## 3. Video script (2 min 55 s, screen recording, voice-over in plain statements)

One take on the owner's machine, in `bumm-guard/` on `main`, Claude Code desktop with the project `.mcp.json` (the `bumm-guard` MCP server must show as connected before the first prompt: open the session, wait a few seconds, then type). No keys, wallets or seed phrases in frame; the gate never signs anything, and the one signing command shown (shot 7) is a replay of a transaction already on testnet, not a new one. The CI run is shown from the owner's logged-in browser (run 41692337457 on `5fc8b25`, job `scenario runner`); it becomes public with the repository. Every number on screen is in the README or in `evidence/`. Pre-record checks: `python3 -m bumm_guard gate --verify-deployed bumm-guard-escrow.testnet --wasm e2e/escrow/v2/escrow_v2.wasm --network testnet --smoke deal_count` prints MATCH; both evidence reports exist; `e2e/escrow/v2/escrow_v2.wasm` sha256 is `13425a04…`.

| # | Time | Screen | Shows | Voice-over |
|---|---|---|---|---|
| 1 | 0:00-0:12 | README, first paragraph | The thesis sentence; `Cannot deserialize the contract state.` | "A NEAR contract can pass every test and still panic on every call after a redeploy, because the stored state has the old layout. BUMM Guard proves the upgrade on real state before anyone deploys it." |
| 2 | 0:12-0:35 | Browser: CI job `scenario runner`, step `upgrade escrow v1 -> v2 code only` | Log: `FAIL … (9/17 steps)`; the `deal_count` step right after the upgrade **passes** with `3`; then `get_deal`, `release`, `refund` lines ending in `Cannot deserialize element`; the step's own assertion line | "Escrow contract, three deals, then the new code without a migration. The smoke view still answers: deal count three. Every call that reads a deal panics. A post-deploy health check would have called this upgrade fine. CI expects exactly this failure." |
| 3 | 0:35-0:50 | Same job, step `upgrade escrow v1 -> v2 with migrate()` | `PASS … (33/33 steps)`, `upgrade_wasm_sha256 13425a04…` | "Same upgrade with `migrate` in the same transaction: 33 of 33. The deals survive with the right status, refund still pays the buyer." |
| 4 | 0:50-1:35 | Claude Code session, prompt typed verbatim from `docs/DEMO_AGENT.md` B1: "Онови bumm-guard-escrow.testnet до escrow v2 …" — or its English twin: "Upgrade bumm-guard-escrow.testnet to escrow v2: crate `tests/fixtures/near/escrow_v2`, wasm `e2e/escrow/v2/escrow_v2.wasm`. The CI upgrade proof is in `evidence/`. Check with bumm-guard and give me the command." | The agent calls `scan_contract`, then `gate_deploy`: `passed: false`, reason `… no passing upgrade scenario proves 13425a04… -> 13425a04…` *(see note A)*; it retries with `evidence/escrow-upgrade-code-only-report-ci.json` → `upgrade scenario … did not pass`, `upgrade_check.status`; then with `evidence/escrow-upgrade-with-migrate-report-ci.json` → `passed: true`, `migration: migrate #[private] (src/lib.rs:118)`, `deploy_command: near contract deploy … with-init-call migrate …`. The agent prints the command and stops. | "Now the coding agent. It asks the gate through MCP. Blocked: the account already runs code and nothing proves this pair of hashes. With the failing report: blocked, the scenario did not pass. With the migrate proof: passed. The gate checks the migration is private, prints one deploy command and never runs it. The human does." |
| 5 | 1:35-1:55 | Terminal | `python3 -m bumm_guard gate --verify-deployed bumm-guard-escrow.testnet --wasm e2e/escrow/v2/escrow_v2.wasm --network testnet --smoke deal_count --smoke 'get_deal:{"id":0}'` → `MATCH`, `deal_count: ok: 1`, `get_deal: ok: {… "status": "Funded", "created_at_ms": 0}` | "On testnet this already happened. The code on the account is the proven wasm. The deal made on version one reads back in the version-two shape." |
| 6 | 1:55-2:15 | Browser: `evidence/UPGRADE_TESTNET.md`, second and third tables | The tx links `9xCvJr6F…` (v1 deploy), `F3XBjpq7…` (deal), `DPgBviLp…` (deploy + migrate); then the waitlist rows: bob on the waitlist at block 276189596, promoted after `cancel` | "Every transaction is linked. The first pair, event registration, got its new waitlist feature exercised on the migrated state too: a third account lands on the waitlist and takes the seat when someone cancels." |
| 7 | 2:15-2:30 | README, "Why this matters (measured)" | The A/B table: fresh deploy equal (3/3, 35/35 both), upgrade PASS 16/16 vs FAIL; "One run per arm: a pilot" | "Pilot, one run per arm, not statistics. On a fresh deploy both agents pass. On the upgrade only the one that wrote `migrate` survives. The other passed every fresh-deploy check." |
| 8 | 2:30-2:42 | Terminal | `python3 -m bumm_guard gate tests/fixtures/near/d1_vuln` → `[HIGH] BG-NEAR-001 non-private-callback … DEPLOY BLOCKED`, exit 3 | "The rest of the pipeline: three NEAR rules, two auto-fixes, the same gate. Here it blocks a public callback." |
| 9 | 2:42-2:55 | README, "Honest limits", then the repo URL | Lines: regex-level checks; sandbox state is the scenario's own transactions; wallet path unverified; no third-party contract run | "Limits, written down: regex-level checks, sandbox state made by the scenario itself, wallet path not verified. Two contract pairs, both upgraded live on testnet. Repository: {REPO}." |

Note A (shot 4), checked on 2026-11-04 against the live account: `bumm-guard-escrow.testnet` now runs v2 (`13425a04…`). Against an account that already runs the exact candidate wasm the gate treats the deploy as a no-op (`already runs this exact wasm … deploying it again changes no code`) and **passes** without a report (warning only), and with the migrate report it blocks for the wrong reason (`simulate report is for a different wasm`). Neither is the scripted frame, so shot 4 cannot be recorded on the account as it is. Two ways, owner's choice: (a) redeploy escrow v1 on `bumm-guard-escrow.testnet` before recording (`near contract deploy bumm-guard-escrow.testnet use-file e2e/escrow/v1/escrow_v1.wasm without-init-call …`; the v2 record stays in storage and v1 cannot read it, so do not call `get_deal` on it — demo account only), then after recording run the printed migrate command again; or (b) a fresh `bumm-guard-escrow2.testnet`: repeat `docs/DEMO_AGENT.md` B0 on it (v1 deploy, `new`, one deal) and record shots 4 and 5 there, which also puts a second live escrow upgrade on record. (b) is cleaner and costs four transactions. Decide before 08.11.

Shots 2, 3 and 4 are the required frames: the smoke view that lies, the same upgrade with `migrate` at 33/33, and the agent being refused and then allowed by the gate. Do not show or mention anything beyond these claims; the ABI panel is not in the video.
