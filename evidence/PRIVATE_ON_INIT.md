# `#[private]` on `#[init]` is not enforced by near-sdk 5.0.0 to 5.17.2

Found on 2026-11-10 by the access probe of `bumm-sim` (first seen in CI run 42790722006, commit `8246862`; the reports below are from run 42797836621, commit `18fe73c`, `bumm-sim 0.5.0`).

## The fact

near-sdk-macros parse `#[private]` on a method that is also `#[init]` or `#[init(ignore_state)]`, and up to
5.17.2 generate no caller check for it. Read in the near-sdk-rs source,
`near-sdk-macros/src/core_impl/code_generator/attr_sig_info.rs`, function `is_private()`:

- tags `near-sdk-v5.0.0`, `5.5.0`, `5.10.0`, `5.15.0`, `5.17.2`: `Init(_) => false`;
- tags `near-sdk-v5.18.0` to `5.28.0` (nine tags checked) and `master`: `Init(init_method) => init_method.is_private`.

4.x was not checked (the file is elsewhere there).

The pattern is the one of NEAR's official example `near-examples/update-migrate-rust`
(`basic-updates/update/src/migrate.rs`, linked from docs.near.org/smart-contracts/release/upgrade). That example
pins near-sdk 5.29.1, where the attribute is enforced; a contract that copied the pattern and builds with an
older 5.x is not protected by it.

So a migration written as

```rust
#[private]
#[init(ignore_state)]
pub fn migrate() -> Self { /* read the old state, return the new one */ }
```

in a crate built with near-sdk 5.17.2 can be called by any account. Nothing in the source shows it.

## What the probe recorded

The access probe replays an upgrade scenario up to its upgrade step in a second sandbox, deploys the new code
without the migration call, and sends that call from an account that is neither the contract nor a scenario
account. Code, state and arguments are those of the real upgrade; only the caller differs. When that call
fails, the contract account sends the same call in the same sandbox (the control): if the control goes through,
the first call failed because of who sent it.

All of this happens in near-sandbox. Nobody called `migrate` on the testnet accounts.

| Pair (old code → new code) | New code built with | `migrate` sent by another account | Report |
|---|---|---|---|
| N06 → N10 (`c3f50add…` → `8526195a…`) | near-sdk 5.17.2, `#[private] #[init(ignore_state)]` | **went through** | `upgrade-with-migrate-probe-report-ci.json` |
| escrow v1 → v2 (`ecd8a805…` → `13425a04…`) | same | **went through** | `escrow-upgrade-with-migrate-probe-report-ci.json` |
| swarm_jobs v1 → v2 (`bcace583…` → `5dbcf8cd…`) | same | **went through** | `swarm-jobs-upgrade-with-migrate-probe-report-ci.json` |
| escrow v1 → v3 (`ecd8a805…` → `b66a74b9…`) | near-sdk 5.17.2, plus `require!(env::predecessor_account_id() == env::current_account_id(), ..)` as the first line | refused: `only the contract itself may migrate`; the control call from the contract account went through | `escrow-v3-upgrade-with-migrate-report-ci.json` |

In every report: `scenarios[0].steps[<upgrade>].access_probe` (`status`, `by`, `error`, `state_sha256`, and `control_status` after a failure), and
`state_sha256` equals the step's `state_before.sha256`, so the probe ran on the same state as the real upgrade.
The new wasm hashes of the first three rows are the files that run on testnet (`evidence/wasm/SHA256SUMS`).

## What it means for the three live upgrades

- 2026-11-03, 04 and 07: deploy and `migrate` were one transaction (`evidence/UPGRADE_TESTNET.md`,
  `evidence/SWARM_JOBS_TESTNET.md`), so there was no moment with the new code and the old state on those accounts.
- A later call of `migrate` on those accounts is not refused by an access check. It runs the body, which reads the
  state as the old layout. Read from the code, not tested: it fails where the already migrated records do not
  parse as that layout, and on an account with no such records it would go through (for escrow v2 the root struct
  has the same shape in both versions, so only the deal records stand in the way). That is an accident of the
  layouts, not access control.
- The gate of that time printed `migration migrate: #[private] (file:line)` for these three. That line was
  wrong for near-sdk 5.17.2.

## What the gate does now

- With a report that has the probe: a probe that went through is not a proof. A probe counts as refused only
  when the other account's call failed, the probe sandbox held the same state as the scenario run, and the
  control call went through; then the migration is accepted even if the source check did not recognise its
  guard. It covers the arguments the scenario's migration call uses.
- Without such a probe: `#[private]` on an `#[init]` method of a crate that builds with near-sdk before 5.18.0
  (resolved in its `Cargo.lock`), or with a version the files do not tell, and with no recognised caller check
  in the body, blocks (`private-unenforced`).
- `python3 scripts/replay_gate_offline.py` shows both: the three earlier pairs exit 3, escrow v1 → v3 exits 0.
- Live, 2026-11-11: `bumm-guard-escrow2.testnet` was upgraded from escrow v1 to v3 with the command this gate
  printed (`UPGRADE_TESTNET.md`, last section). No call of `migrate` from another account was made on testnet;
  the refusal is shown in the sandbox only.

## Fix for a contract

Make the caller check the first line of the migration method (works on every near-sdk version), or build with
near-sdk 5.18.0 or later:

```rust
require!(env::predecessor_account_id() == env::current_account_id(), "only the contract itself may migrate");
```
