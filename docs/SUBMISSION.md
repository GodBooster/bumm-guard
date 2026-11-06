# Submission text: BUMM Guard

Drafts for the owner to edit and publish. Nothing here has been posted or submitted. Every claim is backed by a file in this repository or by the CI run named next to it; anything not done is listed as not done. Placeholders in `{braces}` are filled by the owner on 13.11 (the repository is private until then).

## 1. Form field "What did you build?" (limit 2000 characters; this text: 1984 characters, counted on the text between the markers without the final newline, 2026-11-06)

<!-- form-text-start -->
BUMM Guard proves that a change to a NEAR contract works on state written by the code that is already deployed, before an agent or a human deploys it: the scenario's state, bound to the exact pair of code hashes.

The problem: in NEAR, redeploying code over a contract whose stored state has a different layout, without a migration, leaves a contract that panics on every call ("Cannot deserialize the contract state."). Tests that deploy fresh do not see it.

What it does: the `upgrade` step of the scenario runner deploys the old wasm in near-sandbox, writes state with transactions, deploys the new wasm on the same account (with its `migrate` call) and runs checks on the upgraded state. The deploy gate reads the account's code hash and, if it already runs code, prints the deploy command only when a passing upgrade scenario proves that exact pair. Around it: a static rule-pack (3 NEAR defect classes, 2 auto-fixed), an MCP server (6 tools) and an ABI panel.

Evidence:
- Sandbox, CI on every push, two pairs: migrate 16/16 and 33/33; code-only 4/16 and 9/17 (the escrow smoke view still answers, record reads panic).
- Origin, a pilot (add a waitlist; same model and task, ONE run per arm, not statistics): both arms passed fresh-deploy checks 3/3. The arm using our earlier tool was slower and shipped no migration; the plain agent wrote one. The upgrade check was built after that.
- Testnet, live, two pairs: gate blocked without proof and with a failing one, passed with the migrate proof; the owner ran the printed deploy+migrate tx; pre-upgrade state survived (a registration; a 1 NEAR escrow deal).

Not done: no state copied from a live network; the panel's wallet path is unverified; no third-party contract run.

Prior work: BUMM, the author's Solana builder, is separate and not in this repo. Built in the hackathon window; written with Claude Code.

Try: python3 -m bumm_guard simulate scenarios/upgrade/*.json --validate-only. Real run: CI job "scenario runner".
<!-- form-text-end -->

Field notes, checked against https://nearhackathon.com/HACKATHON.md on 2026-11-06:

- One submission per repository (a second one is rejected with 409). A mistake in a saved submission is fixed through https://nearhackathon.com/request-changes, reviewed by hand; it is not edited and not submitted again. So fill the form only after the repository is public, the demo post is up, and the numbers above are rechecked against the latest CI run (the form text deliberately names no run id).
- **Leave `website` empty.** The official API contract calls it a spam trap: any non-empty value rejects the submission. It is not an optional site field.
- Payload fields are exactly: `requestId`, `projectName`, `leadName`, `email`, `size`, `tracks`, `nearAddress`, `projectUrl`, `demoUrl`, `idea`, `consent`. `size` for one person is the string `1`. `tracks` takes several ids: `open` (Open Innovation) is the positioning of this project; `security` (AI Security & Defense) can be added for the deploy gate. The owner decides.
- `nearAddress` is a mainnet account created by the owner; connecting a wallet is not needed to submit.
- Nothing in the rules read on 2026-11-06 says anything about KYC or payout by country; that is a question for the organizers, not a known restriction and not a guarantee.

## 2. X post

The rules require the main post to say the project was built at NEAR HACKATHON, to tag `@NEARProtocol` and `#NEARHackathon`, and to carry the demo video (Q-012: screenshots are not enough for a local-only demo). `@NearLegion` and `@NearBuilders` are optional. Counts below treat a link as 23 characters; recheck in the X editor.

Main post (limit 280; this text counts 274 with the repo link; attach the video):

<!-- x1-start -->
A NEAR contract can pass fresh-deploy tests and break on upgrade over old state. Built at NEAR HACKATHON: BUMM Guard proves the upgrade in near-sandbox and gates the deploy on it. Demo: an escrow deal survives on testnet. @NEARProtocol #NEARHackathon {REPO}
<!-- x1-end -->

Thread, reply 1 (278 characters), where the upgrade check came from:

<!-- x2-start -->
Where the upgrade check came from: a pilot, same task and model, one run per arm, not statistics. The plain agent wrote migrate() and was faster. The agent with our earlier tool passed every fresh-deploy check and shipped no migration. So we built this. evidence/AB_PILOT_N10.md
<!-- x2-end -->

Thread, reply 2 (275 characters):

<!-- x3-start -->
Live on testnet, two contract pairs: the gate refused without proof, passed with the migrate() proof, the owner ran the printed tx, a registration and a 1-NEAR escrow deal survived (evidence/UPGRADE_TESTNET.md). Limits: scenario-made state, regex-level checks. #NEARHackathon
<!-- x3-end -->

## 3. Video script v3 (about 2 min 20 s, screen recording, voice-over in plain statements)

Changed on 2026-11-06 after an external review: the order now follows what actually happens (old deal, failing upgrade in the sandbox, passing migration, three gate decisions, the owner runs the command, the same deal on the upgraded contract), everything is on `bumm-guard-escrow2.testnet`, and the A/B pilot is not in the video (it did not show a speed-up: the tool of that day was slower and missed the problem; the upgrade check was built after it).

Recorded in parts on the owner's machine, in `bumm-guard/` on `main`, and cut together. Not one take: the agent needs about two minutes for shot 3, so its waiting time is cut and the cut is marked on screen ("waiting time cut"). Shot 4 is a real transaction signed by the owner with `sign-with-keychain`; no key, wallet or seed phrase appears in frame, and the frame carries the caption "signed by the owner". The gate never signs anything. The CI log in shot 2 is shown from the owner's logged-in browser with the run number and the commit SHA visible; use the latest green run on `main` at recording time (it becomes public with the repository). Every number on screen is in the README or in `evidence/`.

**Order matters.** Shots 1 to 3 are read-only and can be rehearsed and re-recorded any number of times. Shot 4 changes the account from v1 to v2 and can be done once. Shot 5 is read-only again and can be re-recorded after shot 4. If the recording of shot 4 fails after the transaction went through, show the transaction on the explorer page instead; do not stage a second one.

Pre-record checks (all read-only):

```
python3 -m bumm_guard gate --verify-deployed bumm-guard-escrow2.testnet --wasm e2e/escrow/v1/escrow_v1.wasm --network testnet --smoke 'deal_count=1' --smoke 'get_deal:{"id":0}'
```
must print `MATCH` for `ecd8a805…` and the deal `{"id": 0, "buyer": "bumm-guard-demo.testnet", "seller": "bumm-guard-escrow2.testnet", "amount": "1000000000000000000000000", "released": false}` (seen 2026-11-06). `e2e/escrow/v2/escrow_v2.wasm` sha256 is `13425a04…`; both `evidence/escrow-upgrade-*-report-ci.json` exist; the `bumm-guard` MCP server shows as connected in the Claude Code session before the first prompt.

| # | Time | Screen | Shows | Voice-over |
|---|---|---|---|---|
| 1 | 0:00-0:15 | Terminal: the pre-record command above | `MATCH` for the v1 hash, `deal_count: ok: 1 (as expected)`, the deal: buyer, seller, 1 NEAR, `released: false` | "This escrow contract is live on NEAR testnet and holds one deal: one NEAR, not released. Version two stores a deal differently. We want to upgrade and keep this deal." |
| 2 | 0:15-0:40 | Browser: CI job `scenario runner`, step `upgrade escrow v1 -> v2 code only`, then step `… with migrate()`; run number and SHA in frame | `FAIL … (9/17 steps)`: `deal_count` right after the upgrade **passes** with `3`, then `get_deal`, `release`, `refund` end in `Cannot deserialize element`. Then `PASS … (33/33 steps)`, `upgrade_wasm_sha256 13425a04…` | "First in a sandbox, on state the scenario wrote with the old code. New code without a migration: the deal count still answers, every call that reads a deal panics. A health check that only calls deal count misses the broken records. The same upgrade with migrate in the same transaction: 33 of 33." |
| 3 | 0:40-1:20 | Claude Code session, three prompts typed one after another (below); waiting time cut, marked on screen | `gate_deploy` three times. 1: `passed: false`, `no passing upgrade scenario proves ecd8a805… -> 13425a04…`. 2: `upgrade scenario … for this pair did not pass`. 3: `passed: true`, `proved after upgrade (25 step(s)): …`, `migration migrate: #[private] (src/lib.rs:118)`, `deploy_command: near contract deploy bumm-guard-escrow2.testnet … with-init-call migrate …` | "Now a coding agent asks the gate through MCP. No proof: blocked, the account already runs code and nothing proves this pair of code hashes. The failing report: blocked. The migrate proof: passed. The gate lists what was proved after the upgrade, checks that migrate is private, prints one deploy command and never runs it." |
| 4 | 1:20-1:35 | Terminal: the owner pastes the printed command; caption "signed by the owner" | The transaction result and its ID | "The owner runs the printed command with their own key. Deploy and migrate, one transaction." |
| 5 | 1:35-2:00 | Terminal: the verify command below | `MATCH` for `13425a04…`, `deal_count: ok: 1 (as expected)`, `get_deal: ok: {… "status": "Funded", "created_at_ms": 0} (as expected)` with buyer, seller and amount readable | "Same account. The code on chain is the proven wasm. The deal made on version one is still there: same buyer, same seller, same amount, now in the version-two shape." |
| 6 | 2:00-2:20 | README, "Honest limits", then the repo URL | Lines: scenario-made state, not live state; regex-level checks; no third-party contract run | "Limits, written down: we check the listed scenarios and these exact wasm files. The state is made by the scenario, not copied from the live network, and the source checks are regex-level. Every transaction shown is linked in the repository. {REPO}" |

Shot 3 prompts, typed verbatim, one at a time (three explicit requests, because one general prompt does not guarantee exactly three tool calls):

1. `Upgrade bumm-guard-escrow2.testnet to escrow v2: crate tests/fixtures/near/escrow_v2, wasm e2e/escrow/v2/escrow_v2.wasm, network testnet. Call bumm-guard gate_deploy without a simulate report and show me the result. Do not deploy anything.`
2. `Call it again with simulate_report evidence/escrow-upgrade-code-only-report-ci.json.`
3. `And with evidence/escrow-upgrade-with-migrate-report-ci.json. Show me the deploy command; do not run it.`

Shot 5 command (valid while the account holds exactly this one deal; the expected JSON is the v1 deal read on 2026-11-06 with the v2 fields a migrated deal gets):

```
python3 -m bumm_guard gate --verify-deployed bumm-guard-escrow2.testnet --wasm e2e/escrow/v2/escrow_v2.wasm --network testnet --smoke 'deal_count=1' --smoke 'get_deal:{"id":0}={"id":0,"buyer":"bumm-guard-demo.testnet","seller":"bumm-guard-escrow2.testnet","amount":"1000000000000000000000000","status":"Funded","created_at_ms":0}'
```
A `MATCH` alone proves only that the code is the candidate; the `(as expected)` lines are what show that the deal survived. The same expected-JSON syntax was run on 2026-11-06 against `bumm-guard-escrow.testnet` (upgraded on 2026-11-04) and passed.

If the video runs short, one optional 10-second shot may go before shot 6: `python3 -m bumm_guard gate tests/fixtures/near/d1_vuln` → `[HIGH] BG-NEAR-001 … DEPLOY BLOCKED`, exit 3 ("The same gate also blocks three classes of NEAR defects found by static rules."). Nothing else: no ABI panel, no A/B table, no claim that BUMM Guard makes development faster.

After the recording: the coordinator verifies the account read-only and adds a third table to `evidence/UPGRADE_TESTNET.md` with the transaction of shot 4. The finished file still has to be watched once for readability and against these outputs before it is posted.

History: `bumm-guard-escrow2.testnet` was prepared on 2026-11-04 (create tx `4RhzPTdksaBccof7Gjwv1G2iz5qMkhYSVtUsqUrtmy61`, v1 deploy `yihTWZt7bYBcsZFSbrJVA2MoKJquJPzxdUqKj1EENc9`, `new` `Cv2f7M7W8n2WW22nmTPzNCwq8yAxMXjma61ARxowVqNi`, one 1 NEAR deal from `bumm-guard-demo.testnet` `BhGjfR7Nf3xVpFv2bkshysS3N7zaeHQNCcqYwJ5imF1K`). `bumm-guard-escrow.testnet` already runs v2, so the gate answers `NO CHANGE` there; it is not used in the video.
