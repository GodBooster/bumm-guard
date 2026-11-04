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
