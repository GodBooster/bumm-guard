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

Thread, reply 2 (275 characters):

<!-- x3-start -->
Live on testnet, two contract pairs: the gate refused without proof, passed with the migrate() proof, the owner ran the printed tx, a registration and a 1-NEAR escrow deal survived (evidence/UPGRADE_TESTNET.md). Limits: scenario-made state, regex-level checks. #NEARHackathon
<!-- x3-end -->

Thread, reply 3 (271 characters; after the testnet run, replace "Sandbox: 81/81 steps" with the testnet result if it fits):

<!-- x4-start -->
Also new: guarded jobs for agent swarms. A NEAR contract holds the reward; the agent is paid only after a reviewer approves exactly the wasm hash BUMM Guard checked. review-job prints approve or reject, never signs. Sandbox: 81/81 steps. docs/SWARM_JOBS.md #NEARHackathon
<!-- x4-end -->

## 3. Video script v4 (about 2 min 55 s, screen recording, voice-over in plain statements)

Changed on 2026-11-06: v3 plus guarded jobs for agent swarms (`docs/SWARM_JOBS.md`). The escrow upgrade is now a paid job: agent alice does it, a reviewer checks it with BUMM Guard, alice is paid only after the review passed, then the owner deploys and the deal survives. Order follows what actually happens; everything is on `bumm-guard-escrow2.testnet` and `bumm-guard-jobs.testnet`. The A/B pilot is not in the video (it did not show a speed-up: the tool of that day was slower and missed the problem; the upgrade check was built after it).

Recorded in parts on the owner's machine, in `bumm-guard/` on `main`, and cut together. The agent's waiting time in shot 4 is cut and marked on screen ("waiting time cut"). Every transaction is signed by the owner with `sign-with-keychain` on testnet accounts he controls (alice, bob, demo are his test accounts); each such frame carries the caption "signed by the owner, test account". No key, wallet or seed phrase in frame. BUMM Guard never signs. The CI log in shot 3 is shown with run number and commit SHA visible; use the latest green run on `main` at recording time. Every number on screen is in the README or in `evidence/`.

**Before recording (owner, off camera, once).** `bumm-guard-jobs.testnet` already runs v2 (upgraded live on 2026-11-07, `evidence/SWARM_JOBS_TESTNET.md`); jobs 0 and 1 are finished there (Paid, Cancelled). For the video, demo posts two fresh v2 jobs and alice submits to both (commands in `docs/SWARM_JOBS.md`, section 7 of the v1 plan, adapted): job 2 with `reward` 1 NEAR, `assigned_agent` alice and the pinned `scenario_sha256` `7e7d87ce…` of the escrow migrate scenario, attached 1.1 NEAR (the storage fee is about 0.003 NEAR, the excess comes back); job 3 with the same text and reward, no pin; alice submits escrow v2 to job 2 with the migrate report hash (`f305461f…`) and to job 3 with the code-only report hash (`e7cfcfd8…`). Do not approve, reject or deploy anything yet. The ids are returned by `post_job`; if they are not 2 and 3, replace them everywhere below.

**Order matters.** Shots 1 to 5 are read-only and can be re-recorded any number of times. Shot 6 (approve) and shot 7 (deploy + migrate of escrow2) are real transactions, done once each, in that order: the review in shot 5 reads escrow2 while it still runs v1. Shot 8 is read-only again. If a recording of shot 6 or 7 fails after the transaction went through, show the transaction on the explorer instead; do not stage a second one.

Pre-record checks (read-only): escrow2 still on v1 with its deal (command below, `MATCH` for `ecd8a805…`); `get_job {"id":2}` and `{"id":3}` say `Submitted` by `bumm-guard-alice.testnet`; `evidence/wasm/SHA256SUMS` checks OK; the `bumm-guard` MCP server shows as connected in Claude Code.

```
python3 -m bumm_guard gate --verify-deployed bumm-guard-escrow2.testnet --wasm evidence/wasm/escrow_v1.wasm --network testnet --smoke 'deal_count=1' --smoke 'get_deal:{"id":0}'
```

| # | Time | Screen | Shows | Voice-over |
|---|---|---|---|---|
| 1 | 0:00-0:15 | Terminal: the pre-record command above | `MATCH` for v1, `deal_count: ok: 1 (as expected)`, the deal: buyer, seller, 1 NEAR, `released: false` | "This escrow contract is live on NEAR testnet and holds a one-NEAR deal. Version two stores deals differently. We want the upgrade, and we want this deal to survive." |
| 2 | 0:15-0:30 | Terminal: `near contract call-function as-read-only bumm-guard-jobs.testnet get_job json-args '{"id":2}' network-config testnet now` | description, reward 1 NEAR, `status: Submitted`, agent `bumm-guard-alice.testnet`, `assigned_agent` alice, `scenario_sha256 7e7d87ce…`, `wasm_sha256 13425a04…` | "The upgrade is posted as a paid job for an agent swarm: the contract holds one NEAR, names the agent and pins the one scenario it accepts as proof. Alice submitted the hash of her wasm and of her sandbox report. She is not paid yet." |
| 3 | 0:30-0:55 | Browser: CI job `scenario runner`, steps `upgrade escrow v1 -> v2 code only` and `… with migrate()`; run number and SHA in frame | `FAIL … (9/17 steps)`: `deal_count` right after the upgrade **passes** with `3`, then `get_deal`, `release`, `refund` end in `Cannot deserialize element`. Then `PASS … (33/33 steps)` | "What alice's report proves, in a sandbox, on state the scenario wrote with the old code. New code without a migration: deal count still answers, every call that reads a deal panics. A health check that only calls deal count misses it. With migrate in the same transaction: 33 of 33." |
| 4 | 0:55-1:25 | Claude Code (alice's agent), three prompts below; waiting time cut | `gate_deploy`: 1 blocked, no proof for `ecd8a805… -> 13425a04…`; 2 blocked, scenario did not pass; 3 `passed: true`, `proved after upgrade (25 step(s))`, `migrate #[private]` | "Alice's agent checks its own work through MCP before submitting. No proof: blocked. Failing report: blocked. The migrate proof: passed." |
| 5 | 1:25-1:55 | Terminal: `review-job` for job 3, then for job 2 (commands below) | job 3: `REVIEW BLOCKED: … no passing upgrade scenario proves …`, exit 3, a `reject` command. Job 2: `wasm sha256 … = submitted`, `report sha256 … = submitted`, the pinned `scenario_sha256` matched, `REVIEW PASSED`, exit 0, an `approve` command naming agent, wasm, report and the review report hash | "Now the reviewer. Same gate, run on exactly the files the agent submitted. The submission with the failing report: blocked, reject. The one with the migrate proof: passed. The tool prints the command. It never signs." |
| 6 | 1:55-2:10 | Terminal: bob runs the printed approve; then `get_job {"id":2}`; caption "signed by the owner, test account" | tx ID; `status: Paid`, `gate_report_sha256` recorded | "The reviewer signs. The contract pays only this agent, for this wasm and this report. Alice gets her one NEAR." |
| 7 | 2:10-2:22 | Terminal: the owner runs the deploy command the gate printed in shot 4; caption "signed by the owner" | tx ID of deploy + migrate | "The owner deploys the approved upgrade. Deploy and migrate, one transaction." |
| 8 | 2:22-2:40 | Terminal: the verify command below | `MATCH` for `13425a04…`, `deal_count: ok: 1 (as expected)`, `get_deal: ok: {…} (as expected)` with buyer, seller, amount readable | "Same account. The code on chain is the proven wasm, and the deal made on version one is still there, in the version-two shape." |
| 9 | 2:40-2:55 | `docs/SWARM_JOBS.md` "Honest limits", then README "Honest limits", then the repo URL | review runs off-chain, one trusted reviewer, no real inference; scenario-made state, regex-level checks | "Limits, written down: the review runs off-chain and one reviewer signs; the state is made by the scenario, not copied from the live network. Every transaction is linked in the repository. {REPO}" |

Shot 4 prompts, typed verbatim, one at a time (three explicit requests, because one general prompt does not guarantee exactly three tool calls):

1. `Upgrade bumm-guard-escrow2.testnet to escrow v2: crate tests/fixtures/near/escrow_v2, wasm evidence/wasm/escrow_v2.wasm, network testnet. Call bumm-guard gate_deploy without a simulate report and show me the result. Do not deploy anything.`
2. `Call it again with simulate_report evidence/escrow-upgrade-code-only-report-ci.json.`
3. `And with evidence/escrow-upgrade-with-migrate-report-ci.json. Show me the deploy command; do not run it.`

Shot 5 commands (read-only; they write `.bumm-guard/review-<job>.json`):

```
python3 -m bumm_guard review-job --jobs-contract bumm-guard-jobs.testnet --job 3 --crate tests/fixtures/near/escrow_v2 --wasm evidence/wasm/escrow_v2.wasm --simulate-report evidence/escrow-upgrade-code-only-report-ci.json --network testnet
python3 -m bumm_guard review-job --jobs-contract bumm-guard-jobs.testnet --job 2 --crate tests/fixtures/near/escrow_v2 --wasm evidence/wasm/escrow_v2.wasm --simulate-report evidence/escrow-upgrade-with-migrate-report-ci.json --network testnet
```
After shot 6, bob runs the printed `reject … "close":true` for job 3 (off camera): the job is cancelled and demo gets the reward back in the same transaction.

Shot 8 command (valid while the account holds exactly this one deal; the expected JSON is the v1 deal read on 2026-11-06 with the v2 fields a migrated deal gets):

```
python3 -m bumm_guard gate --verify-deployed bumm-guard-escrow2.testnet --wasm evidence/wasm/escrow_v2.wasm --network testnet --smoke 'deal_count=1' --smoke 'get_deal:{"id":0}={"id":0,"buyer":"bumm-guard-demo.testnet","seller":"bumm-guard-escrow2.testnet","amount":"1000000000000000000000000","status":"Funded","created_at_ms":0}'
```
A `MATCH` alone proves only that the code is the candidate; the `(as expected)` lines are what show that the deal survived. The same expected-JSON syntax was run on 2026-11-06 against `bumm-guard-escrow.testnet` (upgraded on 2026-11-04) and passed.

Nothing else in the video: no ABI panel, no A/B table, no rule-pack shot unless time is left, no claim that BUMM Guard makes development faster.

After the recording: the author verifies both accounts read-only and adds to `evidence/UPGRADE_TESTNET.md` the escrow2 upgrade and to `evidence/SWARM_JOBS_TESTNET.md` the two video jobs (post, submit, review report hash, approve, reject-close). The finished file still has to be watched once for readability and against these outputs before it is posted.

History: `bumm-guard-escrow2.testnet` was prepared on 2026-11-04 (create tx `4RhzPTdksaBccof7Gjwv1G2iz5qMkhYSVtUsqUrtmy61`, v1 deploy `yihTWZt7bYBcsZFSbrJVA2MoKJquJPzxdUqKj1EENc9`, `new` `Cv2f7M7W8n2WW22nmTPzNCwq8yAxMXjma61ARxowVqNi`, one 1 NEAR deal from `bumm-guard-demo.testnet` `BhGjfR7Nf3xVpFv2bkshysS3N7zaeHQNCcqYwJ5imF1K`). `bumm-guard-escrow.testnet` already runs v2, so the gate answers `NO CHANGE` there; it is not used in the video.
