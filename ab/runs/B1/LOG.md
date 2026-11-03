# LOG B1
- 09:02:25 start; read TASK_N10.md, src/lib.rs, README, docs/FORMATS.md, evaluate.yml (wasm path comes from --wasm, scenarios only need valid format)
- 09:03-09:05 wrote src/lib.rs: waitlist via ticket LookupMaps (slot->account, account->ticket) + head/tail counters; promotion on registered cancel; waitlisted cancel removes ticket (gap skipped). Existing messages kept ("already registered", "is not registered"); new "already on the waitlist".
- 09:03 first `cargo +stable test --offline`: compiled first try (2m22s), 13 tests pass (replaced s3 refusal test with waitlist test; added 6 waitlist tests). Local test iterations: 1.
- 09:05 wrote 3 scenarios in ab/runs/B1/scenarios (FIFO promotion, waitlisted leaves, duplicates/unknown); `simulate --validate-only` valid (wasm missing locally = expected).
- 09:06 local wasm build (rustc 1.85.0, offline) OK, 44.9s, 238481 bytes. Remote run b1-1 id 41608216660 triggered 09:05:43
- 09:14 remote b1-1 (41608216660): build+sim success, wait ~9.2 min (09:05:43->09:14:33); 3/3 scenarios PASS, 46/46 steps, simulate exit=0. Next: run old N06 02,03 (b1-2) as regression check (N06 01 expects refusal when full = replaced behaviour).
- 09:16 remote b1-2 (41609496895): N06 02,03 PASS (15/15 steps), wait 1.4 min (cache). 09:17 writing RESULT.md
