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

The last evidence line ("Testnet: …") is true only after the escrow2 upgrade to v3 recorded for the video (section 3, shots 5 to 7). If that upgrade is not done, replace it with: "Testnet: three earlier pairs upgraded live with the previous gate; pre-upgrade state survived."

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

Thread, reply 3 (274 characters):

<!-- x4-start -->
Also new: guarded jobs for agent swarms. A NEAR contract holds the reward and pays the agent only after a reviewer approves the exact wasm hash BUMM Guard checked; review-job never signs. On testnet: one job paid, one rejected and refunded. docs/SWARM_JOBS.md #NEARHackathon
<!-- x4-end -->

## 3. Video script v6 (2 min 10 s, screen recording, voice-over in plain statements)

Changed on 2026-11-10 from v5: the access probe found that `migrate` of escrow v2 is callable by any account (near-sdk 5.17.2 generates no caller check for `#[private]` on an `#[init]` method; `evidence/PRIVATE_ON_INIT.md`; found in the sandbox on the pre-migration state, nobody called `migrate` on the testnet accounts). The video now opens on that finding, shows the gate refusing escrow v2 and passing escrow v3, and upgrades the testnet account kept for the video (`bumm-guard-escrow2.testnet`) to v3. Two roles on screen: **agent** (Claude Code) and **reviewer** (the human who checks, pays and deploys; on testnet every account is the owner's). Shots 4 to 7 are one continuous take. Every number on screen is in the README or in `evidence/`.

Recorded in parts on the owner's machine, in `bumm-guard/` on `main`, and cut together; the terminal is logged to a file (`script`). The agent's waiting time in shot 3 is cut and marked ("waiting time cut"). Every transaction is signed by the owner with `sign-with-keychain` on testnet accounts he controls; each such frame carries the caption "signed by the owner, test account". No key, wallet or seed phrase in frame. BUMM Guard never signs.

**Before recording (owner, off camera), in this order.**

1. Jobs 2 and 3 on `bumm-guard-jobs.testnet` are `Submitted` with the escrow v2 wasm. The current gate rejects job 2 (its `migrate` is open) and job 3 (code-only report). Run `review-job` for each and sign the second command it prints (`reject` with `"close":true`) with the reviewer's function-call key (`--signer-public-key <key>` after `sign-with-keychain`): both jobs become `Cancelled`, the rewards go back to `bumm-guard-demo.testnet`.

   ```
   python3 -m bumm_guard review-job --jobs-contract bumm-guard-jobs.testnet --job 2 --crate tests/fixtures/near/escrow_v2 --wasm evidence/wasm/escrow_v2.wasm --simulate-report evidence/escrow-upgrade-with-migrate-report-ci.json --network testnet
   python3 -m bumm_guard review-job --jobs-contract bumm-guard-jobs.testnet --job 3 --crate tests/fixtures/near/escrow_v2 --wasm evidence/wasm/escrow_v2.wasm --simulate-report evidence/escrow-upgrade-code-only-report-ci.json --network testnet
   ```

2. The poster posts the new job (the call returns its id; 4 if nothing else was posted in between), pinned to the escrow v3 scenario and assigned to the agent:

   ```
   near contract call-function as-transaction bumm-guard-jobs.testnet post_job json-args '{"description":"upgrade bumm-guard-escrow2.testnet to escrow v3 (migrate checks its caller) keeping its deals","target_account":"bumm-guard-escrow2.testnet","reward":"1000000000000000000000000","scenario_sha256":"5b27a381ba5ee997d4d2f4ce0e9478f253f17ecb907d10d6972379ffa223e1fd","assigned_agent":"bumm-guard-alice.testnet"}' prepaid-gas '30 Tgas' attached-deposit '1.1 NEAR' sign-as bumm-guard-demo.testnet network-config testnet sign-with-keychain send
   ```

3. The agent submits the escrow v3 wasm and its CI report (`evidence/wasm/escrow_v3.wasm`, `evidence/escrow-v3-upgrade-with-migrate-report-ci.json`):

   ```
   near contract call-function as-transaction bumm-guard-jobs.testnet submit json-args '{"job_id":4,"wasm_sha256":"b66a74b929fdc843f73f924e2435ad9b61e41a8d1f8a1a7aec6f9fe0cd4a5527","report_sha256":"db7e3fe41a2079a20c49f7bf2d5ec1967ec57adcf641658f858e766d52dbd6a4"}' prepaid-gas '30 Tgas' attached-deposit '0 NEAR' sign-as bumm-guard-alice.testnet network-config testnet sign-with-keychain send
   ```

4. Read-only checks: `scripts/judge-check.sh` → `no check failed` (B4 prints `with-init-call migrate`); `get_job {"id":4}` says `Submitted`; `bumm-guard-escrow2.testnet` still runs v1 with its one deal.

**Order matters.** Shots 1 to 3 are read-only and can be re-recorded. Shot 5 (approve) is a real transaction that cannot be repeated once it succeeds; shot 6 (deploy + migrate of escrow2) is a real transaction too, but a failed one rolls back (deploy and migrate are one batch), so retrying it after a failure is safe. Shot 4 must run before shot 5 (after the approve the job is `Paid` and the review refuses it) and before shot 6 (the review reads escrow2 while it still runs v1).

| # | Time | Screen (role) | Shows | Voice-over |
|---|---|---|---|---|
| 1 | 0:00-0:14 | Browser: CI job `scenario runner`, step `access probe on escrow v1 -> v2`; run number and SHA in frame | `migrate sent by bumm-sim-access-probe… on the not yet migrated state SUCCEEDED; the gate has no proof for this pair` | "This is the migration pattern of NEAR's official example: private, init, ignore state. On near-sdk 5.17.2 the private attribute does nothing on an init method. Our sandbox sent migrate from a stranger's account, and it went through." |
| 2 | 0:14-0:24 | Terminal: `gate --verify-deployed bumm-guard-escrow2.testnet --wasm evidence/wasm/escrow_v1.wasm … --smoke 'deal_count=1' --smoke 'get_deal:{"id":0}'` | `MATCH` for v1, the deal: buyer, seller, 1 NEAR, `released: false` | "A live escrow on testnet holds a one-NEAR deal. The next version stores deals differently, so the upgrade needs a migration." |
| 3 | 0:24-0:58 | Claude Code (**agent**): prompt 1, then prompt 2 (below) | 1: `blocked: true`, `has #[private] on an #[init] method … any account can call it`, the `fix:` line. 2: `passed: true`, `proved after upgrade (25 step(s))`, `scenario pin: 5b27a381… (matches)`, `access probe: migrate sent by another account … was refused (…); the same call from the contract account went through`, `deploy_command` with `with-init-call migrate` | "The agent asks BUMM Guard for the deploy command for version two. Blocked: anyone could call its migration. Version three checks the caller inside the method. The sandbox wrote state with the old code, upgraded and migrated it, 33 of 33 checks, and the stranger's call was refused. Passed, and here is the command. The tool never signs." |
| 4 | 0:58-1:16 | Terminal (**reviewer**): `get_job {"id":4}` (2 s), then `review-job` for job 4 | job: `Submitted`, reward 1 NEAR, pinned `scenario_sha256 5b27a381…`; review: hashes `= submitted`, `report scenarios with it: 1 of 1`, `gate: access probe … was refused`, `REVIEW PASSED`, the `approve` command | "The upgrade is a paid job. The contract holds one NEAR and pins the one scenario it accepts. The reviewer runs the same gate on exactly the files the agent submitted: passed." |
| 5 | 1:16-1:28 | Terminal (**reviewer**), same take: the printed `approve`, signed with the reviewer's function-call key; then `get_job {"id":4}`; caption "signed by the owner, test account" | tx ID; `status: Paid`, `gate_report_sha256` = the review report's hash | "The reviewer signs with a key that can only approve or reject jobs. The contract pays this agent, for this wasm and this report." |
| 6 | 1:28-1:38 | Terminal (**reviewer**), same take: the deploy command from shot 3; caption "signed by the owner" | tx ID of deploy + migrate | "Then the upgrade itself: deploy and migrate, one transaction." |
| 7 | 1:38-1:50 | Terminal (**reviewer**), same take: the verify command below | `MATCH` for `b66a74b9…`, `deal_count: ok: 1 (as expected)`, `get_deal: ok: {…} (as expected)` | "The code on chain is the proven wasm, and the deal made on version one is still there, in the new shape." |
| 8 | 1:50-2:10 | README "For judges and AI agents" (the one command), then the repo URL | `scripts/judge-check.sh`; the limits line | "Limits: the state comes from the scenario, not from mainnet, and one reviewer signs. Every claim is checked by one command in the repository. Who can call your migration method? {REPO}" |

Total 2:10 (under the 2:20 limit with 10 s of margin).

Shot 3 prompts, typed verbatim, one at a time:

1. `Upgrade bumm-guard-escrow2.testnet to escrow v2: crate tests/fixtures/near/escrow_v2, wasm evidence/wasm/escrow_v2.wasm, network testnet, simulate_report evidence/escrow-upgrade-with-migrate-report-ci.json. Call bumm-guard gate_deploy and show me the result. Do not deploy anything.`
2. `Now escrow v3: crate tests/fixtures/near/escrow_v3, wasm evidence/wasm/escrow_v3.wasm, simulate_report evidence/escrow-v3-upgrade-with-migrate-report-ci.json, scenario_sha256 5b27a381ba5ee997d4d2f4ce0e9478f253f17ecb907d10d6972379ffa223e1fd. Show me the deploy command; do not run it.`

Shot 4 commands (read-only; the review writes `.bumm-guard/review-4.json`):

```
near contract call-function as-read-only bumm-guard-jobs.testnet get_job json-args '{"id":4}' network-config testnet now
python3 -m bumm_guard review-job --jobs-contract bumm-guard-jobs.testnet --job 4 --crate tests/fixtures/near/escrow_v3 --wasm evidence/wasm/escrow_v3.wasm --simulate-report evidence/escrow-v3-upgrade-with-migrate-report-ci.json --network testnet
```

Shot 5: the `approve` command exactly as shot 4 printed it, with `--signer-public-key <reviewer function-call key>` inserted after `sign-with-keychain`. The `gate_report_sha256` in it is the hash of `.bumm-guard/review-4.json` from that run (the file carries a timestamp, so every run of shot 4 prints a different hash): sign only the command of the take that is used.

Shot 6: the deploy command from shot 3, exactly as printed:

```
near contract deploy bumm-guard-escrow2.testnet use-file evidence/wasm/escrow_v3.wasm with-init-call migrate json-args '{}' prepaid-gas '100 Tgas' attached-deposit '0 NEAR' network-config testnet sign-with-keychain send
```

Shot 7 command (valid while the account holds exactly this one deal):

```
python3 -m bumm_guard gate --verify-deployed bumm-guard-escrow2.testnet --wasm evidence/wasm/escrow_v3.wasm --network testnet --smoke 'deal_count=1' --smoke 'get_deal:{"id":0}={"id":0,"buyer":"bumm-guard-demo.testnet","seller":"bumm-guard-escrow2.testnet","amount":"1000000000000000000000000","status":"Funded","created_at_ms":0}'
```
A `MATCH` alone proves only that the code is the candidate; the `(as expected)` lines show that the deal survived.

Nothing else in the video: no ABI panel, no A/B table, no rule-pack shot, no claim that BUMM Guard makes development faster.

After the recording: the author verifies both accounts read-only and adds to `evidence/UPGRADE_TESTNET.md` the escrow2 upgrade and to `evidence/SWARM_JOBS_TESTNET.md` jobs 2, 3 and 4 (reject-close of 2 and 3, review report hash and approve of 4 with the function-call key), and commits the review report of job 4.

History: `bumm-guard-escrow2.testnet` was prepared on 2026-11-04 (create tx `4RhzPTdksaBccof7Gjwv1G2iz5qMkhYSVtUsqUrtmy61`, v1 deploy `yihTWZt7bYBcsZFSbrJVA2MoKJquJPzxdUqKj1EENc9`, `new` `Cv2f7M7W8n2WW22nmTPzNCwq8yAxMXjma61ARxowVqNi`, one 1 NEAR deal from `bumm-guard-demo.testnet` `BhGjfR7Nf3xVpFv2bkshysS3N7zaeHQNCcqYwJ5imF1K`). `bumm-guard-escrow.testnet` runs escrow v2 (upgraded on 2026-11-04, before the finding); it is not used in the video.
