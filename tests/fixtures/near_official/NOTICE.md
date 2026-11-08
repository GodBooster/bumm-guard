# Third-party code: near-sdk-rs `examples/versioned`

- Source: https://github.com/near/near-sdk-rs/tree/8b91e264399d285e79a180c3e35d0018d310a6ea/examples/versioned
- Commit: `8b91e264399d285e79a180c3e35d0018d310a6ea` (tag `near-sdk-v5.17.2`, the near-sdk release the other fixtures pin); also in `UPSTREAM_COMMIT`
- Copyright (c) 2022 NEAR. License: MIT OR Apache-2.0, at the user's option (`LICENSE-MIT`, `LICENSE-APACHE`, copied unmodified from the same commit)

| path here | what it is |
|---|---|
| `versioned/` | `examples/versioned/` at that commit, unmodified (`Cargo.toml`, `README.md`, `src/lib.rs`; git blob ids `e0f8f607`, `8219ffd6`, `7b3d55e1`). The build script fails if it differs from the fetched commit. |
| `versioned_v0/` | ours: the same contract in its V0 era (the enum has only `V0`), derived from the official file; the header comment lists every changed line |
| `versioned_plain/` | ours: a pre-versioning build whose state is a plain struct with the official `ContractV0` field, derived from the official file; the header comment lists every changed line |
| `locks/` | ours: a `Cargo.lock` for each of the three builds (the upstream example ships none), so CI builds with `--locked` |

How the wasm in `evidence/wasm/` is built (`scripts/build_official_example.sh`, run in CI): fetch the pinned commit, check that `versioned/` and the two licence files equal it byte for byte, copy `versioned_v0/` and `versioned_plain/` into its `examples/` next to the official crate (so all three use the same `near-sdk` path dependency and the same release profile), and run `cargo +1.85.0 build --release --locked --target wasm32-unknown-unknown` in each with the lock from `locks/`. The official crate is built as plain cargo, not `cargo near build`, like the other fixtures.
