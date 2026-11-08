# Third-party code: near-sdk-rs `examples/versioned`

- Source: https://github.com/near/near-sdk-rs/tree/7476cbe4328571322094f9e463f1dc9602933cb3/examples/versioned
- Commit: `7476cbe4328571322094f9e463f1dc9602933cb3` (the commit the annotated tag `near-sdk-v5.17.2` points to; that release is the near-sdk version the other fixtures pin); also in `UPSTREAM_COMMIT`
- Copyright (c) 2022 NEAR. License: MIT OR Apache-2.0, at the user's option (`LICENSE-MIT`, `LICENSE-APACHE`, copied unmodified from the same commit)

| path here | what it is |
|---|---|
| `versioned/` | `examples/versioned/` at that commit, unmodified (`Cargo.toml`, `README.md`, `.cargo/config.toml`, `src/lib.rs`; git blob ids `e0f8f607`, `8219ffd6`, `dadf1d35`, `7b3d55e1`). The build script fails if it differs from the fetched commit. |
| `versioned_v0/` | ours: the same contract in its V0 era (the enum has only `V0`), derived from the official file; the header comment lists every changed line; `Cargo.toml` differs only in the package name, `.cargo/config.toml` is the official one |
| `versioned_plain/` | ours: a pre-versioning build whose state is a plain struct with the official `ContractV0` field, derived from the official file; the header comment lists every changed line; `Cargo.toml` differs only in the package name, `.cargo/config.toml` is the official one |
| `locks/` | ours: a `Cargo.lock` for each of the three builds (the upstream example ships none), so CI builds with `--locked` |

How the wasm in `evidence/wasm/` is built (`scripts/build_official_example.sh`, run in CI): fetch the pinned commit, check that `versioned/` and the two licence files equal it byte for byte, copy `versioned_v0/` and `versioned_plain/` into its `examples/` next to the official crate (so all three use the same `near-sdk` path dependency, the same release profile and the example's `.cargo/config.toml`, which strips the wasm with `-C link-arg=-s`), and run `cargo +1.85.0 build --release --locked --target wasm32-unknown-unknown` in each with the lock from `locks/`. Upstream builds its examples with `cargo near build non-reproducible-wasm` (`examples/build_all.sh`); here they are built with plain cargo, like the other fixtures, so the bytes differ from a `cargo near` build, but the source is the official one.
