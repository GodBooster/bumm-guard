# Submission text: BUMM Guard

Drafts for the owner to edit and publish. Nothing here has been posted or submitted. Every claim is backed by a file in this repository or by the CI run named next to it; anything not done is listed as not done. Placeholders in `{braces}` are filled by the owner on 13.11.

## 1. Form field "What did you build?" (limit 2000 characters; this text: 1954 characters, 1970 if the form counts line breaks as two; counted on the text between the markers without the final newline, 2026-11-10)

<!-- form-text-start -->
BUMM Guard checks in a sandbox that a NEAR contract upgrade works on state the old code wrote, binds that proof to the exact pair of code hashes, and only then hands an agent or a human the deploy command.

The problem: new code over state with another layout, and no migration, fails on every call that reads the data. And the migration is itself an entry point: near-sdk 5.0.0 to 5.17.2 generates no caller check for `#[private]` on an `#[init]` method, so the usual `#[private] #[init(ignore_state)] fn migrate()` can be called by anyone.

What it does: the `upgrade` scenario step deploys the old wasm in near-sandbox, writes state, deploys the new wasm with its `migrate` call and checks the upgraded state. An access probe replays the run, deploys the new code alone and sends `migrate` from another account. The gate reads the account's code hash and prints the deploy command only when a passing scenario proves that pair; a migration another account can run is refused. Also: a static rule-pack and an MCP server.

Evidence:
- Found by the probe on 2026-11-10: in a sandbox replay, `migrate` of the three contracts we had upgraded live on testnet accepts a stranger's call. The gate now refuses them; escrow v3 checks the caller and passes 33/33.
- CI on every push: four of our pairs and the official near-sdk-rs `versioned` example; code-only upgrades fail.
- Guarded jobs for agent swarms: a contract pays the agent only after the reviewer approves that agent, wasm and report; `review-job` prints the command, never signs.
- Testnet: the gate refused the open migration and passed v3; the owner ran the printed tx; a 1 NEAR escrow deal survived.

Not done: no state copied from a live network; one trusted reviewer; testnet only; the gate is pipeline control, not on-chain enforcement.

Prior work: BUMM, the author's Solana builder, is separate, not in this repo. Built in the hackathon window with Claude Code.

Try: scripts/judge-check.sh
<!-- form-text-end -->

The last evidence line ("Testnet: …") is true since 2026-11-11: `evidence/UPGRADE_TESTNET.md`, last section (escrow v1 → v3 on `bumm-guard-escrow2.testnet`), and `evidence/SWARM_JOBS_TESTNET.md` (the gate's refusal of escrow v2 is stored on chain as the reject reason of job 2).

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
The NEAR migration #[private] #[init(ignore_state)] is open to anyone on near-sdk 5.0-5.17. Built at NEAR HACKATHON: BUMM Guard proves an upgrade in near-sandbox and calls migrate() as a stranger before an agent deploys. @NEARProtocol #NEARHackathon {REPO}
<!-- x1-end -->

Thread, reply 1 (278 characters), where the upgrade check came from:

<!-- x2-start -->
Where the upgrade check came from: a pilot, same task and model, one run per arm, not statistics. The plain agent wrote migrate() and was faster. The agent with our earlier tool passed every fresh-deploy check and shipped no migration. So we built this. evidence/AB_PILOT_N10.md
<!-- x2-end -->

Thread, reply 2 (277 characters with the link; the hashtag is in the main post), how the finding was made (`evidence/PRIVATE_ON_INIT.md`):

<!-- x3-start -->
How we found it: BUMM Guard replays the upgrade in a second sandbox, deploys the new code alone and calls migrate() as a stranger. On the code of our three testnet contracts that call went through. The gate refuses them now; escrow v3 checks the caller. {REPO}
<!-- x3-end -->

Thread, reply 3 (277 characters):

<!-- x4-start -->
Also new: guarded jobs for agent swarms. A NEAR contract holds the reward and pays the agent only after a reviewer approves the exact wasm hash BUMM Guard checked; review-job never signs. On testnet: two jobs paid, three rejected and refunded. docs/SWARM_JOBS.md #NEARHackathon
<!-- x4-end -->

## 3. Demo video as recorded (1 min 51 s, 2026-11-11)

A screen recording with an English voice-over; nobody is on camera. Recorded in parts on the owner's machine, in `bumm-guard/` on `main` at `b4e73e2`, and cut together. Waiting time (network, the agent thinking) is cut; the voice says so ("Waits are cut"). Every transaction in it was signed by the owner with `sign-with-keychain` on testnet accounts the owner controls; the voice says "Test account". No key, wallet or seed phrase is in frame. BUMM Guard signs nothing. Two roles: **agent** (Claude Code with the BUMM Guard MCP server) and **reviewer** (the human who checks, pays and deploys; on testnet every account is the owner's). Everything shown is recorded with transaction ids in `evidence/UPGRADE_TESTNET.md` (last section) and `evidence/SWARM_JOBS_TESTNET.md` (last two sections).

| # | Time | Screen (role) | Shows |
|---|---|---|---|
| 1 | 0:00-0:17 | Browser: CI job `scenario runner`, step `access probe on escrow v1 -> v2` | `migrate sent by bumm-sim-access-probe-not-an-alias.test.near on the not yet migrated state SUCCEEDED; the gate has no proof for this pair` |
| 2 | 0:17-0:23 | Terminal: `gate --verify-deployed bumm-guard-escrow2.testnet --wasm evidence/wasm/escrow_v1.wasm …` | `MATCH` for v1 (`ecd8a805…`); `deal_count` 1; the deal: buyer, seller, 1 NEAR, `released: false` |
| 3 | 0:23-0:41 | Claude Code (**agent**): prompt 1, then prompt 2 (below) | 1: the gate returned `BLOCKED` (`blocked: true`, `deploy_command: null`), `#[private]` on an `#[init]` method with near-sdk 5.17.2. 2: `passed: true`, the access probe refused, the deploy command with `with-init-call migrate`, not run |
| 4 | 0:41-0:54 | Terminal (**reviewer**): `get_job {"id":4}`, then `review-job` for job 4 | job 4 `Submitted`, reward 1 NEAR, pinned `scenario_sha256 5b27a381…`; hashes `= submitted`, `report scenarios with it: 1 of 1`, `gate: access probe … was refused`, `REVIEW PASSED`, the `approve` command |
| 5 | 0:54-1:12 | Terminal (**reviewer**): the `approve` signed offline with the reviewer's function-call key, sent, then `get_job {"id":4}` | `Public key: ed25519:BhBsPnJe…` (the function-call key); tx `EHNVGz6i…`; `status: Paid`, `gate_report_sha256` = the review report's hash |
| 6 | 1:12-1:20 | Terminal (**reviewer**): the deploy command from shot 3 | tx `27sDyuPT…`: `Contract code has been successfully deployed`, `migrate … succeeded` |
| 7 | 1:20-1:25 | Terminal (**reviewer**): `gate --verify-deployed … --wasm evidence/wasm/escrow_v3.wasm` with the expected deal | `MATCH` for `b66a74b9…`, `deal_count: ok: 1 (as expected)`, `get_deal: ok: {…} (as expected)`, `post-deploy verification passed` |
| 8 | 1:25-1:51 | Browser: the repository, README "For judges and AI agents", the claim/check table | `scripts/judge-check.sh`; each claim next to its check |

**Voice-over script** (one line per shot, parts of a shot separated by a slash):

1. BUMM Guard is a deploy gate for NEAR contract upgrades. In older near-sdk versions, a private migrate can be called by anyone. Our CI proves it: a stranger's call succeeds.
2. On testnet, escrow two runs version one, with a one-NEAR deal.
3. Claude Code asks for version two. Waits are cut. / Blocked: migrate has no caller check. Nothing deployed. / Now version three: migrate checks its caller. / Passed. Command shown, not run.
4. Job four: agent Alice submitted version three for one NEAR. / The review checks the submitted files and reruns the gate. Passed.
5. The owner signs with a limited reviewer key. Test account. / The signed transaction is sent, and the approve succeeds. / Job four is Paid.
6. The owner deploys version three, with migrate in the same transaction.
7. Verified: version three on chain, the deal intact.
8. Everything is open source. One command for judges and AI agents checks the claims, signs nothing, and needs no NEAR account. / Each claim maps to a check, and CI replays them on every push. / BUMM Guard. Code on GitHub: GodBooster slash bumm-guard.

"Older near-sdk versions" means 5.0.0 to 5.17.2 (README, "Honest limits"). "Limited reviewer key" is a function-call access key for `approve` and `reject` on the jobs contract.

**What was cut, besides waiting time.** A first take of shot 7 that was run before the deploy: it printed a mismatch, correctly, because the account still ran v1. A second, mistaken send of the deploy command 22 seconds after the first: it failed in `migrate` and rolled back (tx `DpdRuA9u…`, `evidence/UPGRADE_TESTNET.md`). The macOS keychain prompt. Shots 5 and 6 are from two takes three minutes apart (08:45 and 08:48 UTC). In shot 7 the tool also prints a warning that an older local `gate-report.json` was for another wasm; it does not affect the comparison.

**Done before the recording, off camera (2026-11-10).** Jobs 2 and 3 held escrow v2, which the gate refuses since the finding; `review-job` printed a `reject` for each, and the owner signed the `close: true` variant: both `Cancelled`, rewards back to the poster. These two rejects went out under the reviewer's full-access key, not the function-call key as intended (`evidence/SWARM_JOBS_TESTNET.md` says why). Then the poster posted job 4, pinned to the escrow v3 scenario and assigned to the agent, and the agent submitted the escrow v3 wasm and its CI report:

```
python3 -m bumm_guard review-job --jobs-contract bumm-guard-jobs.testnet --job 2 --crate tests/fixtures/near/escrow_v2 --wasm evidence/wasm/escrow_v2.wasm --simulate-report evidence/escrow-upgrade-with-migrate-report-ci.json --network testnet
python3 -m bumm_guard review-job --jobs-contract bumm-guard-jobs.testnet --job 3 --crate tests/fixtures/near/escrow_v2 --wasm evidence/wasm/escrow_v2.wasm --simulate-report evidence/escrow-upgrade-code-only-report-ci.json --network testnet
near contract call-function as-transaction bumm-guard-jobs.testnet post_job json-args '{"description":"upgrade bumm-guard-escrow2.testnet to escrow v3 (migrate checks its caller) keeping its deals","target_account":"bumm-guard-escrow2.testnet","reward":"1000000000000000000000000","scenario_sha256":"5b27a381ba5ee997d4d2f4ce0e9478f253f17ecb907d10d6972379ffa223e1fd","assigned_agent":"bumm-guard-alice.testnet"}' prepaid-gas '30 Tgas' attached-deposit '1.1 NEAR' sign-as bumm-guard-demo.testnet network-config testnet sign-with-keychain send
near contract call-function as-transaction bumm-guard-jobs.testnet submit json-args '{"job_id":4,"wasm_sha256":"b66a74b929fdc843f73f924e2435ad9b61e41a8d1f8a1a7aec6f9fe0cd4a5527","report_sha256":"db7e3fe41a2079a20c49f7bf2d5ec1967ec57adcf641658f858e766d52dbd6a4"}' prepaid-gas '30 Tgas' attached-deposit '0 NEAR' sign-as bumm-guard-alice.testnet network-config testnet sign-with-keychain send
```

Shot 3 prompts, typed verbatim, one at a time:

1. `Upgrade bumm-guard-escrow2.testnet to escrow v2: crate tests/fixtures/near/escrow_v2, wasm evidence/wasm/escrow_v2.wasm, network testnet, simulate_report evidence/escrow-upgrade-with-migrate-report-ci.json. Call bumm-guard gate_deploy and show me the result. Do not deploy anything. Report only what the gate returned, briefly; no suggestions of your own.`
2. `Now escrow v3: crate tests/fixtures/near/escrow_v3, wasm evidence/wasm/escrow_v3.wasm, simulate_report evidence/escrow-v3-upgrade-with-migrate-report-ci.json, scenario_sha256 5b27a381ba5ee997d4d2f4ce0e9478f253f17ecb907d10d6972379ffa223e1fd. Show me the deploy command; do not run it. Report only what the gate returned, briefly; no suggestions of your own.`

Shot 4 commands (read-only; the review wrote the report committed as `evidence/review-job-4-2026-11-11.json`):

```
near contract call-function as-read-only bumm-guard-jobs.testnet get_job json-args '{"id":4}' network-config testnet now
python3 -m bumm_guard review-job --jobs-contract bumm-guard-jobs.testnet --job 4 --crate tests/fixtures/near/escrow_v3 --wasm evidence/wasm/escrow_v3.wasm --simulate-report evidence/escrow-v3-upgrade-with-migrate-report-ci.json --network testnet
```

Shot 5: the `approve` that shot 4 printed, signed offline so that near-cli-rs takes the function-call key (`$K` the key, `$N` its next nonce, `$H` and `$B` a recent block; how to get them: `docs/SWARM_JOBS.md`, "Reviewer key"), then sent. `$R` is the hash of the review report of that run:

```
R=$(shasum -a 256 .bumm-guard/review-4.json | cut -d' ' -f1); echo $R
near --offline contract call-function as-transaction bumm-guard-jobs.testnet approve json-args "{\"job_id\":4,\"agent\":\"bumm-guard-alice.testnet\",\"wasm_sha256\":\"b66a74b929fdc843f73f924e2435ad9b61e41a8d1f8a1a7aec6f9fe0cd4a5527\",\"report_sha256\":\"db7e3fe41a2079a20c49f7bf2d5ec1967ec57adcf641658f858e766d52dbd6a4\",\"gate_report_sha256\":\"$R\"}" prepaid-gas '100 Tgas' attached-deposit '0 NEAR' sign-as bumm-guard-bob.testnet network-config testnet sign-with-keychain --signer-public-key $K --nonce $N --block-hash $H --block-height $B save-to-file ../approve-4.json
near transaction send-signed-transaction file-with-base64-signed-transaction ../approve-4.json network-config testnet send
```

Shot 6: the deploy command from shot 3, exactly as printed:

```
near contract deploy bumm-guard-escrow2.testnet use-file evidence/wasm/escrow_v3.wasm with-init-call migrate json-args '{}' prepaid-gas '100 Tgas' attached-deposit '0 NEAR' network-config testnet sign-with-keychain send
```

Shot 7 command (valid while the account holds exactly this one deal; `scripts/judge-check.sh` repeats it as check B5):

```
python3 -m bumm_guard gate --verify-deployed bumm-guard-escrow2.testnet --wasm evidence/wasm/escrow_v3.wasm --network testnet --smoke 'deal_count=1' --smoke 'get_deal:{"id":0}={"id":0,"buyer":"bumm-guard-demo.testnet","seller":"bumm-guard-escrow2.testnet","amount":"1000000000000000000000000","status":"Funded","created_at_ms":0}'
```
A `MATCH` alone proves only that the code is the candidate; the `(as expected)` lines show that the deal survived.

Not in the video: the ABI panel, the A/B pilot, the rule-pack, any claim that BUMM Guard makes development faster.

History: `bumm-guard-escrow2.testnet` was prepared on 2026-11-04 (create tx `4RhzPTdksaBccof7Gjwv1G2iz5qMkhYSVtUsqUrtmy61`, v1 deploy `yihTWZt7bYBcsZFSbrJVA2MoKJquJPzxdUqKj1EENc9`, `new` `Cv2f7M7W8n2WW22nmTPzNCwq8yAxMXjma61ARxowVqNi`, one 1 NEAR deal from `bumm-guard-demo.testnet` `BhGjfR7Nf3xVpFv2bkshysS3N7zaeHQNCcqYwJ5imF1K`). `bumm-guard-escrow.testnet` runs escrow v2 (upgraded on 2026-11-04, before the finding); it is not used in the video.
