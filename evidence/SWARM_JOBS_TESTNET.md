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

Nothing was approved, rejected or deployed in the rehearsal. The approve of job 0, the upgrade of escrow2 and the payment are recorded in the video (`docs/SUBMISSION.md` §3, shots 6 and 7) and will be added below.
