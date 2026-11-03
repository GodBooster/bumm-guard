# LOG B1
- 09:02:25 start; read TASK_N10.md, src/lib.rs, README, docs/FORMATS.md, evaluate.yml (wasm path comes from --wasm, scenarios only need valid format)
- 09:03-09:05 wrote src/lib.rs: waitlist via ticket LookupMaps (slot->account, account->ticket) + head/tail counters; promotion on registered cancel; waitlisted cancel removes ticket (gap skipped). Existing messages kept ("already registered", "is not registered"); new "already on the waitlist".
- 09:03 first `cargo +stable test --offline`: compiled first try (2m22s), 13 tests pass (replaced s3 refusal test with waitlist test; added 6 waitlist tests). Local test iterations: 1.
- 09:05 wrote 3 scenarios in ab/runs/B1/scenarios (FIFO promotion, waitlisted leaves, duplicates/unknown); `simulate --validate-only` valid (wasm missing locally = expected).
