# Contract wasm files the proofs are bound to

The deploy gate binds every proof to a wasm file by its sha256. These are the exact files: CI builds, byte-identical across the CI runs cited (the first four; `swarm_jobs.wasm` was added on 2026-11-06 and `swarm_jobs_v2.wasm` on 2026-11-07, same build) of the fixtures in `tests/fixtures/near/` (rustc 1.85.0, near-sdk 5.17.2), downloaded from CI run 42192505774 (commit `85c4dfb`, 2026-11-06). The same hashes were produced by every earlier CI run cited in `evidence/` and are the code that runs on the testnet accounts in `evidence/UPGRADE_TESTNET.md`.

| File | Contract | sha256 |
|---|---|---|
| `event_registration.wasm` | N06, event registration | `c3f50addfb067db8b9fcfc4b8dc6d1a6a197e66d392648bda372f7a7b3a3f617` |
| `event_registration_waitlist.wasm` | N10, N06 plus a waitlist and `migrate()` | `8526195a51a5f5d94f15fab7df1c685804eb419a01dc1a734ac1c76d6220e079` |
| `escrow_v1.wasm` | escrow v1 | `ecd8a805ea3252f1ffa2a285964d3e035d6e3c6be16e6974c33c479c3e034443` |
| `escrow_v2.wasm` | escrow v2 (new deal layout and `migrate()`) | `13425a042dec55eec364240e890a1645c7332f2898149353ebcb5830a5886013` |
| `swarm_jobs.wasm` | guarded jobs for agent swarms (`docs/SWARM_JOBS.md`); from CI run 42272132654 on `adb8b70`, scenario report `evidence/swarm-jobs-report-ci.json` | `bcace58397f2ee597dd5687187ea9e178e3cd5405dfdb810553214e416a9a92b` |
| `swarm_jobs_v2.wasm` | guarded jobs v2 (storage fee, per-job floor, approve bindings, pinned scenario, `migrate()` from v1); from CI run 42390541743 on `00bd558`, reports `evidence/swarm-jobs-v2-report-ci.json`, `evidence/swarm-jobs-upgrade-with-migrate-report-ci.json` (v1 -> v2 with migrate, 49/49), `evidence/swarm-jobs-upgrade-code-only-report-ci.json` (expected failure) | `5dbcf8cd767c096c3e207e8c804e5f950bc5460de8be0e6e4132426009d33ba5` |

Check: `cd evidence/wasm && shasum -a 256 -c SHA256SUMS` (or `sha256sum -c SHA256SUMS` on Linux).

Use them with the gate, for example: `python3 -m bumm_guard gate tests/fixtures/near/escrow_v2 --wasm evidence/wasm/escrow_v2.wasm --account <account> --network testnet --simulate-report evidence/escrow-upgrade-with-migrate-report-ci.json --dry-run`. A local build on another machine may give other bytes and therefore another hash; the shipped reports would then not match it, which is the gate working as designed.
