# BG-NEAR-001 sandbox regression

Run from the repository root on Intel macOS:

```sh
scripts/linux-test.sh contracts test --workspace
```

On Linux with the wasm32 target installed:

```sh
cd contracts
cargo test --workspace --locked
```

The test builds the actual `tests/fixtures/near/{d1_vuln,d1_safe,proxy}` crates
using their existing lockfile. No accounts on public networks are used.
For each escrow it asserts initial unpaid state, executes Mallory's forged
callback through the proxy, and verifies the resulting paid state and unchanged
seller balance. The safe fixture must fail specifically with the private-method
error. A fresh deployment of each fixture then verifies legitimate buyer payment,
callback completion, and the seller's exact balance increase.

The same security invariant (`is_paid == false` after spoofing) is violated by
`d1_vuln` and upheld by `d1_safe`; the regression suite intentionally expects both
outcomes so CI stays green when the exploit is correctly reproduced.

References:
- `../../research/06_near_detectors_spec_2026-10-29.md`, BG-NEAR-001, executable test.
- `../scripts/linux-test.sh`, Linux container runner.
- `rust-toolchain.toml`, Rust 1.99.1 and wasm32 target.

Version discrepancy: existing fixtures pin near-sdk **5.17.2**, despite the
handoff mentioning 5.30.0. These tests preserve the fixtures unchanged and pin
near-workspaces 0.24.0. SDK migration is not part of this change.
