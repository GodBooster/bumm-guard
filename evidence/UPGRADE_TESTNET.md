# Live upgrade N06 → N10 on NEAR testnet (2026-11-03)

The same account `bumm-guard-demo.testnet` that ran N06 since 2026-11-02 was upgraded to N10
(waitlist) with a migration, and only after the gate saw a passing upgrade proof for exactly
that pair of code hashes.

| Step | What | Evidence |
|---|---|---|
| 1 | CI builds N06 and N10 with rustc 1.85.0; the N06 build is byte-identical to the code already on the account | N06 sha256 `c3f50addfb067db8b9fcfc4b8dc6d1a6a197e66d392648bda372f7a7b3a3f617` (= on-chain code), N10 sha256 `8526195a51a5f5d94f15fab7df1c685804eb419a01dc1a734ac1c76d6220e079` |
| 2 | Upgrade proof in near-sandbox (CI, job `scenario runner`) | with `migrate()`: 16/16 steps (registrations survive, waitlist works on migrated state) → `upgrade-with-migrate-report-ci.json`; code-only: every step after the upgrade fails with `Cannot deserialize the contract state.` |
| 3 | Gate against the live account, no proof | `DEPLOY BLOCKED: account bumm-guard-demo.testnet already runs code c3f50add…; this is an upgrade and no passing upgrade scenario proves c3f50add… -> 8526195a…` (exit 3) |
| 4 | Gate with the failing code-only report | `DEPLOY BLOCKED` … `upgrade scenario '… (code-only upgrade) …' for this pair did not pass` (exit 3) |
| 5 | Gate with the passing migrate proof | passed; printed one batch: `near contract deploy bumm-guard-demo.testnet use-file …event_registration_waitlist.wasm with-init-call migrate json-args '{}' prepaid-gas '100 Tgas' …`; storage lock estimate ≈2.07 NEAR, balance 9.9985 NEAR |
| 6 | Owner put state into the old contract: `register` as `bumm-guard-demo.testnet` | tx [`6BbnmQ1sRnKc6LgGKHomtSRMHBmHRwNRihFPQ8sqiKqq`](https://explorer.testnet.near.org/transactions/6BbnmQ1sRnKc6LgGKHomtSRMHBmHRwNRihFPQ8sqiKqq), 0.309 Tgas |
| 7 | Owner ran the printed command with their own key (deploy + `migrate` in one transaction) | tx [`8kvdZu8787miimR9rJHkiB1oNBXu58xJrJk4mxZcofrd`](https://explorer.testnet.near.org/transactions/8kvdZu8787miimR9rJHkiB1oNBXu58xJrJk4mxZcofrd), 2.0 Tgas, fee 0.00019 NEAR |
| 8 | `bumm-guard gate --verify-deployed` | on-chain code sha256 `8526195a…` = proven N10 (**MATCH**); `get_capacity` 2, `get_registered_count` 1, `get_registered` ["bumm-guard-demo.testnet"] (**the registration made before the upgrade survived**), `get_waitlist` [] (new method answers), `get_seats_left` 1, `is_registered` true → `testnet-upgrade-gate-report-2026-11-03.json` |

Not shown: a waitlist entry on testnet (needs a second and third account), the panel's wallet path.
