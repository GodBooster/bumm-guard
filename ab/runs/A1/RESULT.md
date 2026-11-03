# RESULT A1
- Start 09:02:24 UTC, end 09:06:47 UTC (~5 min of the 30-min box).
- Build iterations to green: 2 (1st failed: store::TreeMap is gated behind the 'unstable' feature; replaced with LookupMap<u64,AccountId> queue slots + head/tail). Test iterations: 1 (green first run).
- Final test output (cargo +stable test --offline): "running 12 tests ... test result: ok. 12 passed; 0 failed". wasm: cargo build --release --target wasm32-unknown-unknown --offline with rustc 1.85.0 -> Finished OK.

## Change
- State: added waitlist (LookupMap slot->account), waitlist_tickets (LookupMap account->slot, membership), head/tail u64.
- register(): "already registered" if registered; "already on the waitlist" if waitlisted; seat free -> registered, else appended to waitlist (never fails when full).
- cancel(): registered -> remove and promote first live waitlist slot (skips vacated slots); waitlisted -> leave waitlist (order of others unchanged); else panic "Account X is not registered".
- get_waitlist() -> Vec<AccountId> in order. All existing methods/args/view results unchanged.
- Added #[private] #[init(ignore_state)] migrate() because the Borsh state layout changed (adding fields breaks deserialization of an already deployed contract).
- Test s3 (third registration refused) replaced by waitlist behaviour (this is the one replaced behaviour); 5 new tests: double waitlist join, promotion order, waitlist leave + rejoin + re-promotion, cancel with empty waitlist, cancel after leaving waitlist refused.

## Why I believe it is correct
Unit tests cover each requirement and the order invariants (middle leave, rejoin at end, successive promotions). Invariant: an account is in at most one of registered / waitlist (register checks both; promotion removes from waitlist before insert). Storage prefixes r/w/t are distinct. wasm contains exports get_waitlist and migrate and both panic messages.

## NOT verified
- No real deployment / transactions / sandbox (forbidden here): persistence across separate calls (collection flush to storage) is only covered by the SDK's well-known contract_state flush, not tested; unit tests run in a mock env in one process.
- migrate() never executed against real old state.
- Gas for very large waitlists/many tombstones (get_waitlist iterates head..tail; promotion skips holes, linear in vacated slots) not measured; no view-call limit analysis.
- Panic message for cancel changed from require! to env::panic_str but text is identical ("Account X is not registered").

## Human to-do before deploying
- If an instance is already deployed: deploy new wasm and call migrate (by the contract account) in the same batch; fresh deploys just call new(capacity).
- Run sandbox/testnet scenarios with several real accounts; consider a cap on waitlist length / pagination for get_waitlist; consider whether waitlisted/promoted accounts should get an event/log.
