# Submission text: BUMM Guard

Drafts for the owner to edit and publish. Nothing here has been posted or submitted. Every claim is backed by a file in this repository or by the CI run named next to it; anything not done is listed as not done. Placeholders in `{braces}` are filled by the owner on 13.11 (the repository is private until then).

## 1. Form field "What did you build?" (limit 2000 characters; this text: 1989 characters, counted on the text between the markers without the final newline, 2026-11-07)

<!-- form-text-start -->
BUMM Guard checks in a sandbox that a NEAR contract upgrade works on state the old code wrote, binds that proof to the exact pair of code hashes, and only then hands an agent or a human the deploy command.

The problem: in NEAR, redeploying code over a contract whose stored state has a different layout, without a migration, leaves a contract that panics on every call ("Cannot deserialize the contract state."). Tests that deploy fresh do not see it.

What it does: the `upgrade` scenario step deploys the old wasm in near-sandbox, writes state with transactions, deploys the new wasm on the same account (with its `migrate` call) and runs checks on the upgraded state. The deploy gate reads the account's code hash and, if it already runs code, prints the deploy command only when a passing upgrade scenario proves that pair. Around it: a static rule-pack (3 NEAR defect classes, 2 auto-fixed; regex-level) and an MCP server (7 tools).

Evidence:
- Sandbox, CI on every push, three pairs: migrate 16/16, 33/33, 49/49; code-only fails after the upgrade (a smoke view still answers, record reads panic).
- Guarded jobs for agent swarms: a NEAR contract holds the reward and pays the agent only after the reviewer approves that agent, wasm and report; `review-job` prints approve or reject, never signs. On testnet: one job paid, one rejected and refunded.
- Testnet, live, three pairs: gate blocked without proof and with a failing one, passed with the migrate proof; the owner ran the printed tx; pre-upgrade state survived (a registration, a 1 NEAR escrow deal, two submitted swarm jobs).

Not done: no state copied from a live network; swarm review off-chain, one trusted reviewer, no real inference; testnet only; no third-party contract run.

Prior work: BUMM, the author's Solana builder, is separate, not in this repo. Built in the hackathon window with Claude Code.

Try: python3 -m bumm_guard simulate scenarios/upgrade/*.json --validate-only. Real run: CI job "scenario runner".
<!-- form-text-end -->

Field notes, checked against https://nearhackathon.com/HACKATHON.md on 2026-11-06:

- One submission per repository (a second one is rejected with 409). A mistake in a saved submission is fixed through https://nearhackathon.com/request-changes, reviewed by hand; it is not edited and not submitted again. So fill the form only after the repository is public, the demo post is up, and the numbers above are rechecked against the latest CI run (the form text deliberately names no run id).
- **Leave `website` empty.** The official API contract calls it a spam trap: any non-empty value rejects the submission. It is not an optional site field.
- Payload fields are exactly: `requestId`, `projectName`, `leadName`, `email`, `size`, `tracks`, `nearAddress`, `projectUrl`, `demoUrl`, `idea`, `consent`. `size` for one person is the string `1`. `tracks` = `["open", "security"]`, decided by the owner on 2026-11-06: Open Innovation is the positioning; AI Security & Defense fits the deploy gate (a policy check that refuses an agent a deploy command without proof and never signs).
- `consent` is `true` only after the owner has agreed to the contact notice, read to them verbatim: "I agree that the organizers may use these details to review our submission and contact us about this hackathon."
- The author submits a second, independent project, Forecast Evidence Swarm (https://github.com/GodBooster/forecast-evidence-swarm); each README names the other (rule Q-015: one person, at most two independent projects, disclosed). Both repositories must be public before the deadline.
- `nearAddress` is a mainnet account created by the owner; connecting a wallet is not needed to submit.
- Nothing in the rules read on 2026-11-06 says anything about KYC or payout by country; that is a question for the organizers, not a known restriction and not a guarantee.

## 2. X post

The rules require the main post to say the project was built at NEAR HACKATHON, to tag `@NEARProtocol` and `#NEARHackathon`, and to carry the demo video (Q-012: screenshots are not enough for a local-only demo). `@NearLegion` and `@NearBuilders` are optional. Counts below treat a link as 23 characters; recheck in the X editor.

Main post (limit 280; this text counts 273 with the repo link; attach the video):

<!-- x1-start -->
A NEAR contract can pass fresh-deploy tests and break on upgrade. Built at NEAR HACKATHON: BUMM Guard checks the upgrade in near-sandbox on old-code state, then gates the deploy. Demo: an escrow deal survives on testnet. @NEARProtocol #NEARHackathon {REPO}
<!-- x1-end -->

Thread, reply 1 (278 characters), where the upgrade check came from:

<!-- x2-start -->
Where the upgrade check came from: a pilot, same task and model, one run per arm, not statistics. The plain agent wrote migrate() and was faster. The agent with our earlier tool passed every fresh-deploy check and shipped no migration. So we built this. evidence/AB_PILOT_N10.md
<!-- x2-end -->

Thread, reply 2 (270 characters; the hashtag is in the main post):

<!-- x3-start -->
Live on testnet, three contract pairs: the gate refused without proof, passed with the migrate() proof, the owner ran the printed tx; a registration, a 1-NEAR escrow deal and two swarm jobs survived. Limits: scenario-made state, regex checks. evidence/UPGRADE_TESTNET.md
<!-- x3-end -->

Thread, reply 3 (274 characters):

<!-- x4-start -->
Also new: guarded jobs for agent swarms. A NEAR contract holds the reward and pays the agent only after a reviewer approves the exact wasm hash BUMM Guard checked; review-job never signs. On testnet: one job paid, one rejected and refunded. docs/SWARM_JOBS.md #NEARHackathon
<!-- x4-end -->

## 3. Video script v5 (2 min 10 s, screen recording, voice-over in plain statements)

Changed on 2026-11-08 from v4 (2:55): the standard X upload limit is 2 min 20 s, so the script is cut to 2:10. It opens cold on the failure, shows one complete moment (agent blocked, proof, passed with the command), drops the second blocked/passed pair, and puts two roles on screen: **agent** (Claude Code) and **reviewer** (the human who checks, pays and deploys; on testnet every account is the owner's). Shots 4 to 7 are one continuous take. Every number on screen is in the README or in `evidence/`.

Recorded in parts on the owner's machine, in `bumm-guard/` on `main`, and cut together; the terminal is logged to a file (`script`, see the runbook). The agent's waiting time in shot 3 is cut and marked ("waiting time cut"). Every transaction is signed by the owner with `sign-with-keychain` on testnet accounts he controls; each such frame carries the caption "signed by the owner, test account". No key, wallet or seed phrase in frame. BUMM Guard never signs.

**Before recording (owner, off camera).** Jobs 2 and 3 are `Submitted` on `bumm-guard-jobs.testnet` (posted and submitted on 2026-11-08); `bumm-guard-escrow2.testnet` still runs v1 with its one deal. The owner adds the reviewer's function-call key (command in `docs/SWARM_JOBS.md`, "Reviewer key"), checks it with `near account list-keys bumm-guard-bob.testnet network-config testnet now`, and notes its public key for shot 5. Read-only checks: `scripts/judge-check.sh` → `no check failed` (B4 prints `with-init-call migrate`); `get_job {"id":2}` says `Submitted`.

**Order matters.** Shots 1 to 3 are read-only and can be re-recorded. Shot 5 (approve) is a real transaction that cannot be repeated once it succeeds; shot 6 (deploy + migrate of escrow2) is a real transaction too, but a failed one rolls back (deploy and migrate are one batch), so retrying it after a failure is safe. Shot 4 must run before shot 5 (after the approve job 2 is `Paid` and the review refuses it) and before shot 6 (the review reads escrow2 while it still runs v1).

| # | Time | Screen (role) | Shows | Voice-over |
|---|---|---|---|---|
| 1 | 0:00-0:13 | Browser: CI job `scenario runner`, step `upgrade escrow v1 -> v2 code only`; run number and SHA in frame | `deal_count` right after the upgrade passes with `3`, then `get_deal`, `release`, `refund` end in `Cannot deserialize element`; `FAIL … 9/17` | "Upgrade a NEAR contract without migrating its data, and every call that reads that data fails. Here the deal count still answers. Every call that touches a deal breaks." |
| 2 | 0:13-0:28 | Terminal: `gate --verify-deployed bumm-guard-escrow2.testnet --wasm evidence/wasm/escrow_v1.wasm … --smoke 'deal_count=1' --smoke 'get_deal:{"id":0}'` | `MATCH` for v1, the deal: buyer, seller, 1 NEAR, `released: false` | "On NEAR an upgrade replaces the code and keeps the stored data. Deploy and migrate are one transaction; if the migration fails, it all rolls back. This live escrow holds a one-NEAR deal; version two stores deals differently." |
| 3 | 0:28-1:00 | Claude Code (**agent**): prompt 1, then prompt 2 (below) | 1: `blocked: true`, `no passing upgrade scenario proves ecd8a805… -> 13425a04…`. 2: `passed: true`, `proved after upgrade (25 step(s))`, `migration migrate: #[private]`, `deploy_command` with `with-init-call migrate` | "The agent asks BUMM Guard for the deploy command. No proof for this exact pair of code hashes: blocked. With a sandbox run that wrote state with the old code, upgraded it and migrated it, 33 of 33 checks: passed, and here is the command, deploy plus migrate. The tool never signs." |
| 4 | 1:00-1:18 | Terminal (**reviewer**): `get_job {"id":2}` (2 s), then `review-job` for job 2 | job: `Submitted`, reward 1 NEAR, pinned `scenario_sha256 7e7d87ce…`; review: hashes `= submitted`, `report scenarios with it: 1 of 1`, `REVIEW PASSED`, the `approve` command | "The upgrade was a paid job. The contract holds one NEAR and pins the one scenario it accepts. The reviewer runs the same gate on exactly the files the agent submitted: passed." |
| 5 | 1:18-1:30 | Terminal (**reviewer**), same take: the printed `approve`, signed with the reviewer's function-call key; then `get_job {"id":2}`; caption "signed by the owner, test account" | tx ID; `status: Paid`, `gate_report_sha256` = the review report's hash | "The reviewer signs with a key that can only approve or reject jobs. The contract pays this agent, for this wasm and this report." |
| 6 | 1:30-1:40 | Terminal (**reviewer**), same take: the deploy command from shot 3; caption "signed by the owner" | tx ID of deploy + migrate | "Then the upgrade itself: deploy and migrate, one transaction." |
| 7 | 1:40-1:52 | Terminal (**reviewer**), same take: the verify command below | `MATCH` for `13425a04…`, `deal_count: ok: 1 (as expected)`, `get_deal: ok: {…} (as expected)` | "The code on chain is the proven wasm, and the deal made on version one is still there, in the version-two shape." |
| 8 | 1:52-2:10 | README "For judges and AI agents" (the one command), then the repo URL | `scripts/judge-check.sh`; the limits line | "Limits: the state comes from the scenario, not from mainnet, and one reviewer signs. Every claim is checked by one command in the repository. How do you test an upgrade before you deploy over live state? {REPO}" |

Total 2:10 (under the 2:20 limit with 10 s of margin). Optional, only if the cut stays under 2:15: a 2 s flash of `review-job` for job 3 (`REVIEW BLOCKED`) inside shot 4.

Shot 3 prompts, typed verbatim, one at a time:

1. `Upgrade bumm-guard-escrow2.testnet to escrow v2: crate tests/fixtures/near/escrow_v2, wasm evidence/wasm/escrow_v2.wasm, network testnet. Call bumm-guard gate_deploy without a simulate report and show me the result. Do not deploy anything.`
2. `Call it again with simulate_report evidence/escrow-upgrade-with-migrate-report-ci.json. Show me the deploy command; do not run it.`

Shot 4 commands (read-only; the review writes `.bumm-guard/review-2.json`):

```
near contract call-function as-read-only bumm-guard-jobs.testnet get_job json-args '{"id":2}' network-config testnet now
python3 -m bumm_guard review-job --jobs-contract bumm-guard-jobs.testnet --job 2 --crate tests/fixtures/near/escrow_v2 --wasm evidence/wasm/escrow_v2.wasm --simulate-report evidence/escrow-upgrade-with-migrate-report-ci.json --network testnet
```

Shot 5: the `approve` command exactly as shot 4 printed it, with `--signer-public-key <reviewer function-call key>` inserted after `sign-with-keychain`. The `gate_report_sha256` in it is the hash of `.bumm-guard/review-2.json` from that run (the file carries a timestamp, so every run of shot 4 prints a different hash): sign only the command of the take that is used. After shot 5, off camera, the reviewer rejects job 3 with `close: true` (command printed by `review-job` for job 3): the job is cancelled and demo gets the reward back.

Shot 7 command (valid while the account holds exactly this one deal):

```
python3 -m bumm_guard gate --verify-deployed bumm-guard-escrow2.testnet --wasm evidence/wasm/escrow_v2.wasm --network testnet --smoke 'deal_count=1' --smoke 'get_deal:{"id":0}={"id":0,"buyer":"bumm-guard-demo.testnet","seller":"bumm-guard-escrow2.testnet","amount":"1000000000000000000000000","status":"Funded","created_at_ms":0}'
```
A `MATCH` alone proves only that the code is the candidate; the `(as expected)` lines show that the deal survived.

Nothing else in the video: no ABI panel, no A/B table, no rule-pack shot, no claim that BUMM Guard makes development faster. Recording steps, recovery and the checks after recording: the owner's runbook.

After the recording: the author verifies both accounts read-only and adds to `evidence/UPGRADE_TESTNET.md` the escrow2 upgrade and to `evidence/SWARM_JOBS_TESTNET.md` jobs 2 and 3 (review report hash, approve with the function-call key, reject-close), and commits the review report of job 2.

History: `bumm-guard-escrow2.testnet` was prepared on 2026-11-04 (create tx `4RhzPTdksaBccof7Gjwv1G2iz5qMkhYSVtUsqUrtmy61`, v1 deploy `yihTWZt7bYBcsZFSbrJVA2MoKJquJPzxdUqKj1EENc9`, `new` `Cv2f7M7W8n2WW22nmTPzNCwq8yAxMXjma61ARxowVqNi`, one 1 NEAR deal from `bumm-guard-demo.testnet` `BhGjfR7Nf3xVpFv2bkshysS3N7zaeHQNCcqYwJ5imF1K`). `bumm-guard-escrow.testnet` already runs v2, so the gate answers `NO CHANGE` there; it is not used in the video.
