# Fixtures

`event_registration_abi.json` is **hand-written**, not generated. It follows the
near-abi schema (`schema_version`, `metadata`, `body.functions[]`, `body.root_schema`)
and describes the N06 contract (`tests/fixtures/near/event_registration/src/lib.rs`):
8 functions, `new` (init), `register`, `cancel` (change calls), and `get_capacity`,
`get_registered_count`, `get_registered`, `is_registered`, `get_seats_left` (views).

It must be replaced by the output of `cargo near abi` as soon as that output is
available (`target/near/event_registration_abi.json`, see `docs/FORMATS.md` section 1).
Until then the panel is verified against this file only, not against real
`cargo near abi` output.
