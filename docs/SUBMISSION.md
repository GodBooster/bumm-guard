# Submission text: BUMM Guard

Drafts for the owner to edit and publish. Nothing here has been posted or submitted. Every claim is backed by a file in this repository or by the CI run named next to it; anything not done is listed as not done. Placeholders in `{braces}` are filled by the owner on 13.11 (the repository is private until then).

## 1. Form field "What did you build?" (limit 2000 characters; this text: 1896 characters)

<!-- form-text-start -->
BUMM Guard proves that a change to a NEAR contract works on the state that is already deployed, before an agent or a human deploys it.

The problem: in NEAR, redeploying code over a contract whose stored state has a different layout, without a migration, leaves a contract that panics on every call ("Cannot deserialize the contract state."). Unit tests and fresh-deploy scenarios do not see it.

What it does: the new `upgrade` step of the scenario runner deploys the old wasm in a local near-sandbox, writes state with real transactions, deploys the new wasm on the same account (optionally in one batch with a `migrate` call) and runs checks on the upgraded state. Around it: a static rule-pack (3 NEAR defect classes, 2 with deterministic fixers), a deploy gate bound to wasm hashes, an MCP server and an ABI interaction panel.

Evidence:
- A/B pilot (add a waitlist to a live contract), same model and task, ONE run per arm: a pilot, not statistics. Fresh deploy: both arms 3/3 hidden scenarios, 35/35 steps. Upgrade of a contract holding state: the arm with migrate() passed 16/16; the arm that had passed every fresh-deploy check had no migration and failed.
- The upgrade runs in CI on every push (near-sandbox): with migrate 16/16; code-only 4/16, every call after the upgrade panics, and CI requires exactly that.
- Testnet end to end (2026-11-02): CI-built wasm, simulate, gate, deploy by the owner, on-chain code hash matched.

Not done: the proof was run in the sandbox for one contract pair, and on testnet only the blocking direction of the gate was checked; the panel's wallet path is unverified.

Prior work: BUMM, the author's Solana builder, is a separate project, not in this repo. Built in the hackathon window; written with Claude Code.

Try: pip install -e . && bumm-guard simulate scenarios/upgrade/*.json --validate-only. The real run is the CI job "scenario runner".
<!-- form-text-end -->

Field notes: the form allows one submission per repository and no edits, so fill it only after the repository is public and the numbers above are rechecked against the latest CI run (this text cites run 41619178923 in the README; the form text deliberately names no run id).

## 2. X post

Main post (limit 280 characters; a link counts as 23 on X; this text counts 267 with the repo link; attach the video):

<!-- x1-start -->
A NEAR contract can pass every test and still panic on every call after a redeploy: "Cannot deserialize the contract state." BUMM Guard proves an upgrade on real state in near-sandbox before deploy. Pilot: with migrate() 16/16, code-only 4/16. {REPO}
<!-- x1-end -->

Thread, reply 1 (259 characters):

<!-- x2-start -->
Pilot: same task (add a waitlist), same model, one run per arm. Fresh deploy: both 3/3, 35/35, the plain agent faster. Upgrade of a contract with state: only the arm that wrote migrate() survived. One run each, not statistics. Record: evidence/AB_PILOT_N10.md
<!-- x2-end -->

Thread, reply 2 (229 characters; check the official hashtag on the day):

<!-- x3-start -->
Not done yet: a live upgrade on testnet, the panel's wallet path. The upgrade scenarios run in CI on every push. Rule-pack, fixers, MCP server, ABI panel: same repo. #NEARHACKATHON
<!-- x3-end -->

## 3. Video script (2 min 50 s, screen recording, voice-over in plain statements)

Record once on a clean clone with no keys, wallets or seed phrases in frame. The CI run is shown from the owner's logged-in browser (run 41619178923, job `scenario runner`); the logs become public to others only when the repository is. All numbers on screen are the ones in the README.

| # | Time | Screen | Shows | Voice-over |
|---|---|---|---|---|
| 1 | 0:00-0:12 | README, first paragraph | The thesis sentence and the error text `Cannot deserialize the contract state.` | "Redeploying a NEAR contract over existing state without a migration leaves a contract that panics on every call. Unit tests do not see it." |
| 2 | 0:12-0:30 | Terminal | `python3 -m bumm_guard simulate scenarios/upgrade/*.json --validate-only` prints `valid` for two files, 16 steps each, exit 0 | "Two upgrade scenarios. Validated locally; the real run needs Linux, so it runs in CI." |
| 3 | 0:30-0:55 | Editor: `scenarios/upgrade/n06_to_n10_with_migrate.json` | Steps: alice and bob register, carol is refused (event full), then the `upgrade` step with `migrate`, then 12 checks | "Old contract, real state, then the new code on the same account, then checks on the upgraded state." |
| 4 | 0:55-1:25 | Browser: CI run, job `scenario runner`, step "upgrade N06 -> N10 code only" | Log: `FAIL ... (4/16 steps)`, step lines ending in `Cannot deserialize the contract state.`, `12 of 16 step(s) failed`, then `code-only upgrade: steps 0..3 passed, 12 step(s) after it failed, as expected` | "Code-only upgrade. The deploy succeeds. Every call after it panics. CI expects exactly this failure and checks it." |
| 5 | 1:25-1:45 | Same job, step "upgrade N06 -> N10 with migrate()" | `PASS ... (16/16 steps, 5.7s)`, `upgrade_wasm_sha256` line | "The same upgrade with `migrate`: 16 of 16. The registrations survive and the waitlist works." |
| 6 | 1:45-2:15 | README, section "Why this matters (measured)" | The table: fresh deploy equal (3/3, 35/35 for both), upgrade differs (PASS 16/16 vs FAIL). Cursor on "One run per arm: a pilot" | "Pilot, one run per arm. On a fresh deploy both agents passed. On the upgrade only the one with migrate survived. The agent that used BUMM had no upgrade step to check with yet; this is what the step is for." |
| 7 | 2:15-2:35 | Terminal | `python3 -m bumm_guard gate tests/fixtures/near/d1_vuln` prints `DEPLOY BLOCKED`, exit 3 | "The rest of the pipeline: rule-pack, fixers, gate, MCP server. Here the gate blocks a callback without `#[private]`." |
| 8 | 2:35-2:50 | README, "Honest limits" and the repo URL | Lines: sandbox proof, one contract pair; wallet path unverified | "The gate requires the proof for live accounts. Not done: the wallet path. One contract pair. Repository: {REPO}." |

Shots 4 and 5 are the two required frames: code-only upgrade ending in `Cannot deserialize the contract state.`, and the same upgrade with `migrate` at 16/16. Do not show or mention anything beyond these claims.
