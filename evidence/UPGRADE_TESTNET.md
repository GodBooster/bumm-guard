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

## Waitlist on the migrated contract (2026-11-04)

Two more accounts (`bumm-guard-alice.testnet` tx [`7KueMRAFyRE4bPtbBQKqWmCYp53fPc4DCdMJtJ9QgvJN`](https://explorer.testnet.near.org/transactions/7KueMRAFyRE4bPtbBQKqWmCYp53fPc4DCdMJtJ9QgvJN), `bumm-guard-bob.testnet` tx [`BkFxjcB9XPs4zRAFo5M7edcM3kHJgwGVq6hF2UhdX3D2`](https://explorer.testnet.near.org/transactions/BkFxjcB9XPs4zRAFo5M7edcM3kHJgwGVq6hF2UhdX3D2)) exercised the feature N10 added on top of the state N06 left behind. State after each receipt read from the archival RPC at that receipt's block.

| Step | Tx | State after (`get_registered` / `get_waitlist`) |
|---|---|---|
| `register` as alice (last free seat) | [`22YW2teteUaYZuMMoyReS8MgkpYfZwwpzse8o3HyRaas`](https://explorer.testnet.near.org/transactions/22YW2teteUaYZuMMoyReS8MgkpYfZwwpzse8o3HyRaas), block 276189585 | `["bumm-guard-demo.testnet","bumm-guard-alice.testnet"]` / `[]` |
| `register` as bob (event full) | [`BXj6MnjF7Dd2KkyTwWUXxFhpHTWTCWPBfjCzZEMx3bVY`](https://explorer.testnet.near.org/transactions/BXj6MnjF7Dd2KkyTwWUXxFhpHTWTCWPBfjCzZEMx3bVY), block 276189596 | `["bumm-guard-demo.testnet","bumm-guard-alice.testnet"]` / **`["bumm-guard-bob.testnet"]`** |
| `cancel` as alice (bob takes the seat) | [`7hA8pkCA6NHTfjSmC2jpJMzRwZbrVATouxXhkRRzuUnC`](https://explorer.testnet.near.org/transactions/7hA8pkCA6NHTfjSmC2jpJMzRwZbrVATouxXhkRRzuUnC), block 276189606 | `["bumm-guard-demo.testnet","bumm-guard-bob.testnet"]` / `[]`, `get_seats_left` 0 |

Not shown: the panel's wallet path.

# Live upgrade escrow v1 → v2 on NEAR testnet (2026-11-04)

A second, unrelated contract pair on a fresh account `bumm-guard-escrow.testnet`. The v1→v2 change
alters the format *inside* a map record (`IterableMap<u64, Deal>`), so a code-only upgrade keeps
`deal_count` working while `get_deal` fails with `Cannot deserialize element` — the case fresh-deploy
tests cannot see. The owner ran every signing command with their own key; the gate only printed them.

| Step | What | Evidence |
|---|---|---|
| 1 | Account created via faucet | tx [`445fJfaBSfMtbJxxexBu9vqD4cdoThoVfYJpUy5xfuL5`](https://explorer.testnet.near.org/transactions/445fJfaBSfMtbJxxexBu9vqD4cdoThoVfYJpUy5xfuL5), 10 NEAR, no code |
| 2 | Gate for the fresh deploy (`--estimate-storage`) | `has no contract code: fresh deploy`; wasm 192319 bytes locks ≈1.92 NEAR; printed `near contract deploy … without-init-call …` |
| 3 | Owner deployed escrow v1 (CI build, sha256 `ecd8a805ea3252f1ffa2a285964d3e035d6e3c6be16e6974c33c479c3e034443`) | tx [`9xCvJr6FLY2R6uQduWQQSe4qeT4cVagqaSfr614p28cj`](https://explorer.testnet.near.org/transactions/9xCvJr6FLY2R6uQduWQQSe4qeT4cVagqaSfr614p28cj), 1.7 Tgas |
| 4 | `new`, then one deal: buyer `bumm-guard-demo.testnet`, seller `bumm-guard-escrow.testnet`, 1 NEAR | tx [`GQhoMipDXparkuJJ1BEw3bVXfCKAv6aWz29bmSMngxFQ`](https://explorer.testnet.near.org/transactions/GQhoMipDXparkuJJ1BEw3bVXfCKAv6aWz29bmSMngxFQ), tx [`F3XBjpq73hZHuCZFZEFFgweNYGSVfk3sHArerXt9XVMW`](https://explorer.testnet.near.org/transactions/F3XBjpq73hZHuCZFZEFFgweNYGSVfk3sHArerXt9XVMW) (returns deal id `0`) |
| 5 | `--verify-deployed` on v1 before the upgrade | code `ecd8a805…` **MATCH**; `deal_count` 1; `get_deal(0)` = `{buyer: bumm-guard-demo.testnet, seller: bumm-guard-escrow.testnet, amount: 1 NEAR, released: false}` (v1 record format) |
| 6 | Gate for v2, no proof | `DEPLOY BLOCKED: account bumm-guard-escrow.testnet already runs code ecd8a805…; this is an upgrade and no passing upgrade scenario proves ecd8a805… -> 13425a04…` (exit 3) |
| 7 | Gate with the failing code-only CI report (`escrow-upgrade-code-only-report-ci.json`) | `DEPLOY BLOCKED` … `upgrade scenario 'Upgrade escrow v1 → v2 (code-only upgrade) …' for this pair did not pass` (exit 3) |
| 8 | Gate with the passing migrate proof (`escrow-upgrade-with-migrate-report-ci.json`) | passed: `upgrade … ecd8a805… -> 13425a04…, proved by passing scenario '… (upgrade with migrate()) …'; migration migrate({}) in the same transaction`; `migration migrate: #[private] (src/lib.rs:118)`; printed `near contract deploy bumm-guard-escrow.testnet use-file e2e/escrow/v2/escrow_v2.wasm with-init-call migrate json-args '{}' prepaid-gas '100 Tgas' …` |
| 9 | Owner ran the printed command (deploy + `migrate` in one transaction) | tx [`DPgBviLp8DCPF2FiVG9jXigHJaCgNviqni17odxiQ4Xm`](https://explorer.testnet.near.org/transactions/DPgBviLp8DCPF2FiVG9jXigHJaCgNviqni17odxiQ4Xm), 2.0 Tgas, fee 0.00019 NEAR |
| 10 | `--verify-deployed` on v2 | code `13425a042dec55eec364240e890a1645c7332f2898149353ebcb5830a5886013` = proven v2 (**MATCH**); `deal_count` 1; `get_deal(0)` = `{…, status: "Funded", created_at_ms: 0}` — **the deal made on v1 is readable in the v2 format**, with the migration defaults → `testnet-escrow-upgrade-gate-report-2026-11-04.json` |

Not shown: `release`/`refund` on testnet after the upgrade (the sandbox scenario covers them).

# Live upgrade escrow v1 → v3 on NEAR testnet (2026-11-11): the pair whose `migrate` checks its caller

The fourth live pair, and the first one upgraded after the finding of 2026-11-10 (`PRIVATE_ON_INIT.md`): escrow v3 is escrow v2 with a caller check in the body of `migrate`. Account `bumm-guard-escrow2.testnet` had run escrow v1 with one 1 NEAR deal since 2026-11-04 (create tx `4RhzPTdksaBccof7Gjwv1G2iz5qMkhYSVtUsqUrtmy61`, v1 deploy `yihTWZt7bYBcsZFSbrJVA2MoKJquJPzxdUqKj1EENc9`, `new` `Cv2f7M7W8n2WW22nmTPzNCwq8yAxMXjma61ARxowVqNi`, the deal `BhGjfR7Nf3xVpFv2bkshysS3N7zaeHQNCcqYwJ5imF1K`). The upgrade was recorded for the demo video. The owner ran the signing command with the account's own key; the gate only printed it. Transactions and state were read back by the author from the archival RPC on 2026-11-11.

| Step | What | Evidence |
|---|---|---|
| 1 | `--verify-deployed` on v1 before the upgrade | code `ecd8a805…` **MATCH**; `deal_count` 1; `get_deal(0)` = `{buyer: bumm-guard-demo.testnet, seller: bumm-guard-escrow2.testnet, amount: 1 NEAR, released: false}` (v1 record format; the same answer from the archival RPC at block 277192554, the block before the upgrade) |
| 2 | Gate for escrow v2 with its passing migrate report (`escrow-upgrade-with-migrate-report-ci.json`), asked by a coding agent through the MCP tool `gate_deploy` | `blocked: true`, no deploy command: `DEPLOY BLOCKED: migration method migrate in src/lib.rs:118 has #[private] on an #[init] method, but this crate builds with near-sdk 5.17.2: before 5.18.0 near-sdk generates no caller check for init methods, so any account can call it; the report has no access probe that says otherwise` (the gate's message for these inputs, as `review-job` printed it on 2026-11-10 and as it is stored on chain in the reject reason of job 2, `SWARM_JOBS_TESTNET.md`; in the video the agent reports it in its own words) |
| 3 | Gate for escrow v3 with its migrate report (`escrow-v3-upgrade-with-migrate-report-ci.json`, scenario pin `5b27a381…`) | passed: `upgrade of bumm-guard-escrow2.testnet: ecd8a805… -> b66a74b9…, proved by passing scenario 'Upgrade escrow v1 → v3 (upgrade with migrate()) …'; migration migrate({}) in the same transaction`; `proved after upgrade (25 step(s))`; `scenario pin: 5b27a381… (matches)`; `access probe: migrate sent by another account to the new code on the not yet migrated state was refused (… only the contract itself may migrate); the same call from the contract account went through`; `migration migrate: owner check at src/lib.rs:129`; printed `near contract deploy bumm-guard-escrow2.testnet use-file evidence/wasm/escrow_v3.wasm with-init-call migrate json-args '{}' prepaid-gas '100 Tgas' …`. The same gate run is inside the committed review report of job 4, [`review-job-4-2026-11-11.json`](review-job-4-2026-11-11.json) |
| 4 | Owner ran the printed command (deploy + `migrate` in one transaction) | tx [`27sDyuPTAdPYQJreDn6DVsBr5Gp1zUKC7186bgDM9oAo`](https://explorer.testnet.near.org/transactions/27sDyuPTAdPYQJreDn6DVsBr5Gp1zUKC7186bgDM9oAo), block 277192555, 2026-11-11 08:48:11 UTC, 18.0 Tgas, fee 0.0018 NEAR |
| 5 | `--verify-deployed` on v3 | code `b66a74b929fdc843f73f924e2435ad9b61e41a8d1f8a1a7aec6f9fe0cd4a5527` = proven v3 (**MATCH**); `deal_count: ok: 1 (as expected)`; `get_deal(0)` = `{…, status: "Funded", created_at_ms: 0} (as expected)`: **the deal made on v1 is readable in the v3 format**. `scripts/judge-check.sh` repeats this as check B5 |
| 6 | The same command was sent a second time 22 seconds later, by mistake during the recording | tx [`DpdRuA9uVrvqrSAwa1cQheowQqZnvnLz1dnqm82FfKr1`](https://explorer.testnet.near.org/transactions/DpdRuA9uVrvqrSAwa1cQheowQqZnvnLz1dnqm82FfKr1), block 277192592: **failed**, `Smart contract panicked: Cannot deserialize element` in `migrate` (the deals were already in the new format, and `migrate` reads them in the old one). Deploy and `migrate` are one batch, so nothing was applied: at block 277192600 the code is still `b66a74b9…` and `get_deal(0)` is unchanged. Fee 0.0017 NEAR |

Step 6 was not planned. It shows two things about this contract: its `migrate` cannot be applied twice, and a failed upgrade batch leaves code and state as they were. The caller was the contract account itself, so this says nothing about the caller check.

Not shown on testnet: a call of `migrate` from another account (the access probe does that in the sandbox, on every push); `release`/`refund` after the upgrade (the sandbox scenario covers them). The gate calls in steps 2 and 3 cannot be repeated live, because the account no longer runs v1: `scripts/replay_gate_offline.py` (check A7) repeats them with the recorded v1 hash, and the live gate now answers `NO CHANGE` for v3 (check B4).
