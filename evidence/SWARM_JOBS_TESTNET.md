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
| 1 | `review-job` for job 0 (migrate report) | (read-only, reviewer's run) | — | `REVIEW PASSED`, review report sha256 `a27d07db3b4429afcbff1c523739c73af14704a84fa019bf80f51a9a539bfea6`, five-argument `approve` printed |
| 2 | `review-job` for job 1 (code-only report) | (read-only) | — | `REVIEW BLOCKED: … no passing upgrade scenario proves ecd8a805… -> 13425a04…`, `reject` printed in both variants (`close: false` / `close: true`) |
| 3 | `approve` job 0 (agent alice, wasm `13425a04…`, report `f305461f…`, gate report `a27d07db…`) | `bumm-guard-bob.testnet` | `na3HbkkajdpGxbRrCWTu6ecrsEr5PkFn1qpnCFMcmuF` | returns `true`; job 0 `Paid`, `gate_report_sha256` recorded; alice `jobs_paid` 1, `jobs_in_review` 1 → 0 after step 4; alice balance 9.4989 → 10.4989 NEAR (+1 NEAR) |
| 4 | `reject` job 1 with `close: true` | `bumm-guard-bob.testnet` | `ChUyAwT1qm8tjioA69MN96TU3ecCK2DehthFEKkGexZC` | job 1 `Cancelled` with the gate's reason; demo balance 5.9958 → 6.9958 NEAR (+1 NEAR refund); contract balance 12.4955 → 10.4955 NEAR |

Both reviews read `bumm-guard-escrow2.testnet` only; it still runs escrow v1 (`ecd8a805…`) and is upgraded in the video. Balances were read with `view_account` right after each transaction; the small differences from round numbers are gas paid by the signers.


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

Nothing was approved, rejected or deployed after these steps; the approve of job 2 and the escrow2 upgrade are recorded once, in the video.
