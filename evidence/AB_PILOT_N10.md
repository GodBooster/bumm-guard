# A/B pilot 1 on N10 (waitlist change) — 2026-11-03

Status: one run per arm. Calibration of the protocol, not a statistical result.

## Setup
- Identical task text: `TASK_N10.md`. Same model (Sonnet), same frozen starter (`bumm-guard/ab/n10_starter`), fresh context per arm, parallel start 09:02 UTC.
- A1: Rust + official NEAR docs; no BUMM. Time box 30 min.
- B1: the same + BUMM Guard (scenario format, `simulate`, remote near-sandbox via the `evaluate` workflow in CI). Time box 45 min (CI build is slow; the extra time is recorded as setup cost).
- Scoring: coordinator ran the hidden acceptance scenarios `scenarios/n10/*.json` (3 files, 35 steps) and the old `scenarios/n06/*.json` (3 files, 24 steps) against each arm's wasm in near-sandbox via `evaluate.yml`. Arms never saw `scenarios/n10`.

## Results [measured]

| | A1 (no BUMM) | B1 (with BUMM) |
|---|---|---|
| Wall clock | ~4.5 min | ~15 min (≈5 active, ≈10 waiting: 2.4 local test compile, 9.2 + 1.4 remote runs) |
| Build iterations to green | 2 (`store::TreeMap` needs `unstable`) | 1 |
| Unit tests | 12 pass, 1 iteration | 13 pass, 1 iteration |
| Own sandbox evidence | none | 3 own scenarios 46/46 steps; old N06 02+03 15/15 (2 CI runs) |
| Hidden N10 acceptance | **3/3, 35/35 steps** (run 41608409754) | **3/3, 35/35 steps** (run 41609795646) |
| Old N06 | 2/3; №1 fails only where the change request replaces behaviour (full → waitlist) | identical 2/3, same two steps |
| Migration of already-deployed state | **added `migrate()` (`#[init(ignore_state)]`)**, not executed | **not handled**; noted as "works only for a fresh `new`" |
| Self-reported unverified | real transactions, migrate on real state, gas for long queues | migration, gas for long queues, testnet/panel |

## What this says [hypothesis, from one pair]
1. On a small, well-specified state change, a plain agent with unit tests reaches the same independent acceptance result, faster. BUMM's sandbox loop added confidence but no measurable correctness and cost ~10 min of CI waiting.
2. The hidden acceptance suite itself has a blind spot both arms share: it deploys fresh. The realistic N10 path is an **upgrade** of a contract that already holds N06 state (our live `bumm-guard-demo.testnet` does). A1 wrote a migration; B1 did not — and nothing in our current checks would tell the difference. That is a NEAR-specific failure (Borsh state layout changed → every call panics after code-only redeploy) which unit tests and fresh-deploy scenarios cannot see.
3. Therefore the candidate value for BUMM is not "write/check the change" but **"prove the upgrade"**: deploy the old wasm, put real state in it with transactions, deploy the new wasm on the same account (with or without a migrate call), and run the old and new scenarios against the upgraded state. Then gate refuses an upgrade whose scenarios fail on migrated state.

## Next measurement (proposed)
Add an `upgrade` step to the scenario format (`{"deploy": "<new wasm>", "then_call": {"method": "migrate", "args": {}}?}`), write the hidden scenario "N06 state → upgrade → waitlist works, old registrations survive", and score A1 and B1 again without rerunning the agents. Prediction (to be falsified): A1 passes with its migrate, B1 fails (state deserialization panic). Then a pilot 2 where both arms are told the contract is live.

## Re-scoring on upgrade scenarios (2026-11-03, no agent re-runs) [measured]

Hidden scenarios `scenarios/upgrade/n06_to_n10_{with_migrate,code_only}.json`: N06 wasm deployed, alice+bob registered, carol refused (full), then the candidate wasm is deployed on the same account, then 12 checks on the migrated state. Runs: A1 41617679746, B1 41617688666.

| | A1 (no BUMM, wrote `migrate()`) | B1 (with BUMM, no migration) |
|---|---|---|
| Upgrade with `migrate()` (A1's own documented procedure) | **PASS 16/16** — registrations survive, waitlist works | **FAIL** — `MethodNotFound` on migrate; the batch reverts atomically, old N06 code stays, so later N10 steps fail (`Event is full`) |
| Code-only upgrade | FAIL 4/16 — every call after it: `Cannot deserialize the contract state.` | FAIL 4/16 — same |

Prediction from pilot 1 confirmed: on the realistic path (upgrade of a live contract), the arm that passed every fresh-deploy check (B1) has **no working upgrade path**, and neither unit tests nor fresh scenarios showed it. Also measured: a failed migrate in the same batch leaves the old code in place (NEAR batch atomicity) — a safe failure mode worth stating in the README.

Interpretation [hypothesis]: BUMM's measurable value is not in writing or unit-checking the change, but in proving the upgrade against real migrated state before deploy. This was found by the study, not assumed; B1 used BUMM but its scenarios were all fresh-deploy because the tool did not yet offer an upgrade step. Next: gate refuses to print an upgrade command for an account that already has code unless an upgrade scenario for (on-chain code hash → candidate wasm) passed; pilot 2 with both arms told the contract is live.
