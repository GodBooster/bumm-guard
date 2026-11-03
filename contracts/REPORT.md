# contracts/: sandbox tests and the scenario runner

A Rust workspace on near-workspaces 0.24.0 (`Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml`:
rustc 1.99.1 with the `wasm32-unknown-unknown` target). Two crates:

- `sandbox/` — `tests/bg_near_001.rs` builds the real fixtures `d1_vuln`, `d1_safe` and `proxy`
  from `tests/fixtures/near/` as a cargo subprocess (so the fixtures' own pinned rustc 1.85.0 is
  used) and checks on a local near-sandbox that a third account calling the callback through the
  proxy is accepted on `d1_vuln` (state flips to paid) and rejected on `d1_safe` with
  `Method on_paid is private`, that the seller's balance is untouched by the spoof, and that the
  legitimate `pay -> on_paid` path works on both, including the seller's balance increase.
- `sim-runner/` — crate `bumm-sim`, the runner behind `bumm-guard simulate` (formats:
  `docs/FORMATS.md` §2, §2a, §3). 17 lib tests, plus `tests/upgrade_sandbox.rs`, which runs the two
  upgrade scenarios in `scenarios/upgrade/` on a real sandbox and checks the report (with `migrate`
  all steps pass; code-only, every step after the upgrade fails). Design notes and limits:
  `SIMULATE_REPORT.md` and `UPGRADE_REPORT.md` at the repository root.

Run everything (Linux; macOS x86_64 has no near-sandbox binary, hence the Docker wrapper):

```sh
scripts/linux-test.sh contracts test --workspace --locked
```

CI runs the same workspace in the job `sandbox tests (near-workspaces, linux only)` and builds
`bumm-sim` for the job `scenario runner` (`.github/workflows/ci.yml`); both were green on run
41627653109 (2026-11-03, commit `17e9bb3`). The fixtures pin near-sdk 5.17.2 and rustc 1.85.0
(`tests/fixtures/near/rust-toolchain.toml`); the detector that `bg_near_001.rs` exercises is
described in `RULEPACK_REPORT.md`. Accounts in these tests exist only in the local sandbox;
nothing in `contracts/` touches testnet or holds a key.

History: `sandbox/` was handed over on 2026-10-30 with the sandbox run still unverified; it was
first verified green in CI on 2026-11-02 (run 41422672919). `sim-runner/` and the `upgrade` step
followed on 2026-11-02 and 2026-11-03.
