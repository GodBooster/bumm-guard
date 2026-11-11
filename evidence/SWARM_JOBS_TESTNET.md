# Guarded jobs on NEAR testnet (`bumm-guard-jobs.testnet`)

What `docs/SWARM_JOBS.md` describes, run live. Every transaction was signed by the owner with `sign-with-keychain` on test accounts the owner controls; BUMM Guard printed commands and read state, it signed nothing. Each row was checked by the author with read-only RPC calls right after the transaction.

## Setup (2026-11-06)

| # | Step | Signed by | Transaction | Checked after it |
|---|---|---|---|---|
| 1 | Create `bumm-guard-jobs.testnet` (faucet) | owner | `TfF4QrbN1wURis72MJjDetxoDA97UEjRiGbFBnuuFax` | account exists, 10 NEAR, no code |
| 2 | Gate the fresh deploy | (read-only) | — | `has no contract code: fresh deploy`; `simulate report ok: 3 scenario(s) passed for this wasm` (`evidence/swarm-jobs-report-ci.json`); storage lock about 2.5931 NEAR |
| 3 | Deploy `evidence/wasm/swarm_jobs.wasm` | `bumm-guard-jobs.testnet` | `G14baJoa7yLeLtLD2dWDHgn4piCbx9Aiy3YaL2BWyj7x` | `--verify-deployed`: on-chain code sha256 `bcace58397f2ee597dd5687187ea9e178e3cd5405dfdb810553214e416a9a92b` MATCH |
| 4 | `new` with reviewer `bumm-guard-bob.testnet` | `bumm-guard-jobs.testnet` | `DX8ULX3BucfC9wAjXMh5YsgWyZw1YoKo7aS7eATJLoBx` | `get_reviewer` = `"bumm-guard-bob.testnet"`, `job_count` = 0, `agent_count` = 0 |
| 5 | Alice joins the swarm, 0.5 NEAR budget | `bumm-guard-alice.testnet` | `6LsCSc9PMWjVzbhcETmYq4HyPHifuyNwvkSwRhCsDrvK` | `get_agent`: profile `escrow upgrade agent`, budget `500000000000000000000000`; `agent_count` = 1 |
| 6 | Demo posts job 0, 1 NEAR, target `bumm-guard-escrow2.testnet` | `bumm-guard-demo.testnet` | `5orNfBWtrrS3GJQ4rF9ZiQhthdJiW3vMyGd9tENHmZN8` | returns `0`; `get_job {"id":0}`: `Open`, reward 1 NEAR |
| 7 | Demo posts job 1, same text, 1 NEAR | `bumm-guard-demo.testnet` | `3KfEBpm2niNUng4hkviVbsVA5n4Yt1NeJycTxTdHMaGi` | returns `1`; `job_count` = 2 |
| 8 | Alice submits escrow v2 to job 0 with the migrate report | `bumm-guard-alice.testnet` | `CMEzu9xw6z4UbR4KBy7Y7EoKc1LDmsoABDxyzkbtGkAf` | job 0 `Submitted`, `wasm_sha256` `13425a04…`, `report_sha256` `f305461f…` |
| 9 | Alice submits escrow v2 to job 1 with the code-only report | `bumm-guard-alice.testnet` | `C3VQsWnnEZtu9Xm8ofhagbe7GWiBu2YhcKXAsodjL27s` | job 1 `Submitted`, `wasm_sha256` `13425a04…`, `report_sha256` `e7cfcfd8…` |

## Rehearsal of the review (read-only, 2026-11-06)

`bumm-guard-escrow2.testnet` still ran escrow v1 (`ecd8a805…`), so both reviews checked an upgrade `ecd8a805… -> 13425a04…`.

- Job 1, code-only report: `REVIEW BLOCKED: DEPLOY BLOCKED: account bumm-guard-escrow2.testnet already runs code ecd8a805…; this is an upgrade and no passing upgrade scenario proves ecd8a805… -> 13425a04…`, exit 3, a `reject` command for bob printed.
- Job 0, migrate report: `wasm sha256 … = submitted`, `report sha256 … = submitted`, `proved after upgrade (25 step(s))`, `migration migrate: #[private] (src/lib.rs:118)`, `REVIEW PASSED: job 0 may be paid`, exit 0, an `approve` command for bob printed with `gate_report_sha256` bound to that run's review report.

Nothing was approved, rejected or deployed in the rehearsal.

## Live upgrade swarm_jobs v1 -> v2 through the gate (2026-11-07): the third pair

The contract on `bumm-guard-jobs.testnet` was upgraded from v1 (`bcace583…`) to v2 (`evidence/wasm/swarm_jobs_v2.wasm`, `5dbcf8cd…`) with the two submitted jobs and the agent in its state. Proof: `evidence/swarm-jobs-upgrade-with-migrate-report-ci.json` (CI run 42390541743, 49/49 steps); the code-only variant fails as asserted (`evidence/swarm-jobs-upgrade-code-only-report-ci.json`).

| # | Step | Signed by | Transaction | Checked after it |
|---|---|---|---|---|
| 1 | Gate without a report (dry run) | (read-only) | — | `DEPLOY BLOCKED … no passing upgrade scenario proves bcace583… -> 5dbcf8cd…`, exit 3 |
| 2 | Gate with the code-only report (dry run) | (read-only) | — | blocked: `upgrade scenario … did not pass`, exit 3 |
| 3 | Gate with the migrate report | (read-only) | — | `upgrade of bumm-guard-jobs.testnet: bcace583… -> 5dbcf8cd…`, `proved after upgrade (37 step(s))`, `migration migrate: #[private] (src/lib.rs:243)`, `gate passed`, the `with-init-call migrate` command printed |
| 4 | Deploy v2 + `migrate` in one batch (the printed command) | `bumm-guard-jobs.testnet` | `6CqQJxScBEZTkcdtCW6GyRqfM4ugLpazhBGsCXgXXVbs` | 25.0 Tgas; `--verify-deployed`: code `5dbcf8cd…` MATCH; `job_count` 2, `agent_count` 1, `get_reviewer` bob; `get_job 0` and `1` still `Submitted` by alice with the same `wasm_sha256`/`report_sha256`, new fields `reward_storage_fee: "0"`, `scenario_sha256: null`, `assigned_agent: null`; `get_agent alice`: budget `500000000000000000000000`, `jobs_in_review` 2, new field `storage_cost: "0"` |

The state written by v1 (two jobs inside an `IterableMap`, one agent, counters) survived the layout change; the same change without `migrate` makes `get_job` fail with `Cannot deserialize element` while `job_count` still answers (sandbox, code-only report).

## The guarded job completed on the upgraded contract (2026-11-07)

| # | Step | Signed by | Transaction | Checked after it |
|---|---|---|---|---|
| 1 | `review-job` for job 0 (migrate report) | (read-only, reviewer's run) | — | `REVIEW PASSED`, review report sha256 `a27d07db3b4429afcbff1c523739c73af14704a84fa019bf80f51a9a539bfea6` (the file is committed as [`review-job-0-2026-11-07.json`](review-job-0-2026-11-07.json); its sha256 is the `gate_report_sha256` stored on chain for job 0), five-argument `approve` printed |
| 2 | `review-job` for job 1 (code-only report) | (read-only) | — | `REVIEW BLOCKED: … no passing upgrade scenario proves ecd8a805… -> 13425a04…`, `reject` printed in both variants (`close: false` / `close: true`) |
| 3 | `approve` job 0 (agent alice, wasm `13425a04…`, report `f305461f…`, gate report `a27d07db…`) | `bumm-guard-bob.testnet` | `na3HbkkajdpGxbRrCWTu6ecrsEr5PkFn1qpnCFMcmuF` | returns `true`; job 0 `Paid`, `gate_report_sha256` recorded; alice `jobs_paid` 1, `jobs_in_review` 1 → 0 after step 4; alice balance 9.4989 → 10.4989 NEAR (+1 NEAR) |
| 4 | `reject` job 1 with `close: true` | `bumm-guard-bob.testnet` | `ChUyAwT1qm8tjioA69MN96TU3ecCK2DehthFEKkGexZC` | job 1 `Cancelled` with the gate's reason; demo balance 5.9958 → 6.9958 NEAR (+1 NEAR refund); contract balance 12.4955 → 10.4955 NEAR |

Both reviews read `bumm-guard-escrow2.testnet` only; it ran escrow v1 (`ecd8a805…`) then and was upgraded to v3 on 2026-11-11 (`UPGRADE_TESTNET.md`). Balances were read with `view_account` right after each transaction; the small differences from round numbers are gas paid by the signers.


## Jobs for the video (2026-11-08)

Two more jobs on the v2 contract, one with an assigned agent and a pinned scenario, one without.

| # | Step | Signed by | Transaction | Checked after it |
|---|---|---|---|---|
| 1 | Demo posts job 2: reward 1 NEAR, `assigned_agent` alice, `scenario_sha256` `7e7d87ce…` (the escrow upgrade-with-migrate scenario), deposit 1.1 NEAR | `bumm-guard-demo.testnet` | `3LYkQ6MkgehoJAN4m7vdUTwrLwHXmYBnsLv1WNfe6uq8` | returns `2`; job 2 `Open`, `reward_storage_fee` `3930000000000000000000`; 0.09607 NEAR excess refunded to demo |
| 2 | Demo posts job 3: reward 1 NEAR, no agent, no pin, deposit 1.1 NEAR | `bumm-guard-demo.testnet` | `2iWKp9wjuBNDyUDyLtpJLghbZmfa57oXu4FpBtMoCoEL` | returns `3`; job 3 `Open`, `reward_storage_fee` `2970000000000000000000`; 0.09703 NEAR refunded; `job_count` 4 |
| 3 | Alice submits escrow v2 to job 2 with the migrate report | `bumm-guard-alice.testnet` | `kQYXVm4ps6ekvMCoxoQvBQEy41vtx9gKkD6uzEobC9S` | job 2 `Submitted`, `wasm_sha256` `13425a04…`, `report_sha256` `f305461f…` |
| 4 | Alice submits escrow v2 to job 3 with the code-only report | `bumm-guard-alice.testnet` | `GF8iZT4iijMFvPuzY7DtRLSKD6Vgvh1Yvc62CzEEYTTT` | job 3 `Submitted`, `wasm_sha256` `13425a04…`, `report_sha256` `e7cfcfd8…`; alice `jobs_in_review` 2, budget 0.5 NEAR |
| 5 | `review-job` for job 3 (rehearsal) | (read-only) | — | `scenario_sha256 on the job: null`; `REVIEW BLOCKED: … no passing upgrade scenario proves ecd8a805… -> 13425a04…`, exit 3, both `reject` variants printed |
| 6 | `review-job` for job 2 (rehearsal) | (read-only) | — | `report scenarios with it: 1 of 1`; `REVIEW PASSED: job 2 may be paid`, exit 0, `approve` for bob printed |

Nothing was approved, rejected or deployed on 2026-11-08. Job 2 was never approved: on 2026-11-10 the gate began to refuse escrow v2 (`PRIVATE_ON_INIT.md`), and both jobs were rejected and closed (below).

## Reviewer function-call key (2026-11-08)

| # | Step | Signed by | Transaction | Checked after it |
|---|---|---|---|---|
| 1 | Add a function-call access key to the reviewer: receiver `bumm-guard-jobs.testnet`, methods `approve`, `reject`, allowance 0.25 NEAR | `bumm-guard-bob.testnet` (full-access key) | `uVNWrSJSy9MDiP7oPdpkT6Zxbt5PKeCYePHdK4jzMZG` | `view_access_key_list`: `ed25519:BhBsPnJeC1KP1FuCJogm5gRnrN6nfGH5NgpBx1b4v3bP` with `FunctionCall {allowance: 250000000000000000000000, receiver_id: bumm-guard-jobs.testnet, method_names: [approve, reject]}`, next to the existing full-access key |

The approve of job 0 (2026-11-07) was signed with the full-access key. So were the two rejects of 2026-11-10, against the plan (next section). The approve of job 4 (2026-11-11) is the first transaction signed with this key.

## Jobs 2 and 3 rejected and closed (2026-11-10)

Both jobs held the escrow v2 wasm. Since the finding of 2026-11-10 the gate refuses that wasm even with its passing migrate report, because another account can call its `migrate`.

| # | Step | Signed by | Transaction | Checked after it |
|---|---|---|---|---|
| 1 | `review-job` for job 2 (migrate report) | (read-only) | — | `REVIEW BLOCKED: DEPLOY BLOCKED: migration method migrate in src/lib.rs:118 has #[private] on an #[init] method, but this crate builds with near-sdk 5.17.2: before 5.18.0 near-sdk generates no caller check for init methods, so any account can call it; the report has no access probe that says otherwise`, both `reject` variants printed |
| 2 | `reject` job 2 with `close: true` | `bumm-guard-bob.testnet` (full-access key, see below) | `9dZG6CKGBaGtDewX9hPs6YBPL4oDdRmZRq3ErKXkzYov` | block 277063512, 11:49:35 UTC; job 2 `Cancelled`, `reject_reason` = the text of step 1; demo balance 4.9883 → 5.9883 NEAR (+1 NEAR refund) |
| 3 | `review-job` for job 3 (code-only report) | (read-only) | — | `REVIEW BLOCKED: DEPLOY BLOCKED: account bumm-guard-escrow2.testnet already runs code ecd8a805…; this is an upgrade and no passing upgrade scenario proves ecd8a805… -> 13425a04…` |
| 4 | `reject` job 3 with `close: true` | `bumm-guard-bob.testnet` (full-access key) | `BbCkjCUcBRH829xycNfZSukwy6sFXWW6Gub8x5x98yfY` | block 277063588, 11:50:21 UTC; job 3 `Cancelled` with that reason; demo balance 5.9883 → 6.9883 NEAR; contract balance 12.5024 → 10.5024 NEAR over both rejects |

**Which key signed.** Both rejects were meant to be signed with the reviewer's function-call key and were signed with the full-access key `ed25519:HAEGHEk2…`: the `public_key` of both transactions says so, and the nonce of the function-call key did not move. The commands had `--signer-public-key <function-call key>` after `sign-with-keychain`. near-cli-rs 0.31.1 does not use that option when it is online: it asks the network for the signer's keys and takes a full-access one (read in its source, `src/transaction_signature_options/sign_with_keychain/mod.rs`; the option is used only with `--offline`). Nothing was lost, but the claim "rejections are signed with the limited key" would have been false for these two. The commands in `docs/SWARM_JOBS.md` ("Reviewer key") are corrected.

## Job 4: escrow v3, paid after review, approve signed with the function-call key (2026-11-10 and 2026-11-11)

| # | Step | Signed by | Transaction | Checked after it |
|---|---|---|---|---|
| 1 | Demo posts job 4: reward 1 NEAR, `assigned_agent` alice, `scenario_sha256` `5b27a381…` (the escrow v1 → v3 upgrade-with-migrate scenario), deposit 1.1 NEAR | `bumm-guard-demo.testnet` | `4L8nD9pm2Miho5zLVWqEgyb1fPUCEZB5iMk2FfwJ3VpU` | block 277063646, 2026-11-10 11:50:53 UTC; returns `4`; job 4 `Open`, `reward_storage_fee` `4210000000000000000000`; 0.09579 NEAR excess refunded to demo; `job_count` 5 |
| 2 | Alice submits escrow v3 (`evidence/wasm/escrow_v3.wasm`) with its CI report (`evidence/escrow-v3-upgrade-with-migrate-report-ci.json`) | `bumm-guard-alice.testnet` | `EBddd3LHaCnS2HoG7Yvt7eCyzmixckESNX1euk7PPCym` | block 277063695; job 4 `Submitted`, `wasm_sha256` `b66a74b9…`, `report_sha256` `db7e3fe4…` |
| 3 | `review-job` for job 4, 2026-11-11 08:44:57 UTC | (read-only, reviewer's run) | — | `wasm sha256 … = submitted`, `report sha256 … = submitted`, `scenario_sha256 on the job: 5b27a381…; report scenarios with it: 1 of 1`; `gate: upgrade of bumm-guard-escrow2.testnet: ecd8a805… -> b66a74b9…`; `gate: proved after upgrade (25 step(s))`; `gate: access probe: migrate sent by another account to the new code on the not yet migrated state was refused (… only the contract itself may migrate); the same call from the contract account went through`; `REVIEW PASSED: job 4 may be paid`. Review report sha256 `fd307c873f49e2714e15c2b9b1d1c6ebdcf425a5f594c1824cfe0a8f1a2532e8`; the file is committed as [`review-job-4-2026-11-11.json`](review-job-4-2026-11-11.json) |
| 4 | `approve` job 4 (agent alice, wasm `b66a74b9…`, report `db7e3fe4…`, gate report `fd307c87…`) | `bumm-guard-bob.testnet`, **function-call key** `ed25519:BhBsPnJeC1KP1FuCJogm5gRnrN6nfGH5NgpBx1b4v3bP` | `EHNVGz6iWLX81C5gsu58JL5Wv1B3zpL6Fiqubd5e6vRy` | block 277192290, 08:45:32 UTC, 5.5 Tgas; returns `true`; job 4 `Paid`, `gate_report_sha256` = the hash of step 3; alice balance 10.4982 → 11.4982 NEAR (+1 NEAR), `jobs_paid` 2, `jobs_in_review` 0; contract balance 11.5066 → 10.5066 NEAR; the key's nonce is `276802634000001` (its first use), its allowance 0.25 → 0.2495 NEAR |

The approve was signed offline, the only way near-cli-rs 0.31.1 takes a chosen key from the keychain: `near --offline contract call-function as-transaction … sign-with-keychain --signer-public-key <key> --nonce <n> --block-hash <h> --block-height <b> save-to-file <file>`, then `near transaction send-signed-transaction file-with-base64-signed-transaction <file> network-config testnet send`. The `public_key` field of the transaction is the function-call key.

The job's target, `bumm-guard-escrow2.testnet`, was upgraded to the approved wasm three minutes later by its owner (`UPGRADE_TESTNET.md`, tx `27sDyuPT…`); the upgrade is not part of the job. The jobs contract now holds five jobs: 0 and 4 `Paid`, 1, 2 and 3 `Cancelled`. `scripts/judge-check.sh` reads job 4 back as check B6.
