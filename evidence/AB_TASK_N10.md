# Task N10 (identical for every study arm)

You maintain a NEAR smart contract (Rust, near-sdk 5.17.2) for event registration with a
fixed capacity. Its current source is in your run directory (`src/lib.rs`): `new(capacity)`,
`register()`, `cancel()`, and the views `get_capacity`, `get_registered_count`,
`get_registered`, `is_registered(account_id)`, `get_seats_left`.

Change request from the event organiser:

> Add a waitlist. When the event is full, `register()` must not fail any more: the caller
> joins the end of a waitlist. When a registered participant cancels, the first account
> on the waitlist becomes registered automatically, and the waitlist order is preserved.
> A waitlisted account can call `cancel()` to leave the waitlist. Registering twice (while
> registered or while on the waitlist) must be refused with a clear message containing
> "already registered" or "already on the waitlist" respectively; cancelling when neither
> registered nor waitlisted must be refused with a message containing "not registered".
> Add a view `get_waitlist() -> Vec<AccountId>` returning the waitlist in order. Keep all
> existing method names, arguments and view results unchanged.

Definition of done (the same for every arm): the contract builds to wasm with rustc 1.85.0
(`cargo build --release --target wasm32-unknown-unknown` in the run directory; the
`rust-toolchain.toml` there pins 1.85.0), the behaviour above holds when the contract is
deployed and called with real transactions by several different accounts, and nothing that
worked before is broken except the one behaviour the change request replaces (a full event
no longer refuses `register`). You decide how to convince yourself that this is true.
Your work is scored afterwards by independent acceptance scenarios that you do not see.
