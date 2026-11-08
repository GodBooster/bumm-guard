// Ours, not upstream. A pre-versioning build of near-sdk-rs examples/versioned/src/lib.rs
// (commit 7476cbe4 = tag near-sdk-v5.17.2; MIT OR Apache-2.0, see ../../NOTICE.md): the contract
// state is a plain struct with the same field as the official `ContractV0` (no enum, so no tag
// byte in the stored state). Lines that differ from the official file (its line numbers):
//   7-45     the `VersionedContract` enum, its helpers and its `Default` are removed
//   47-50    `#[near] pub struct ContractV0` becomes `#[near(contract_state)] pub struct Contract`
//            with the same single field `funders: IterableMap<AccountId, NearToken>`
//   52-56    its `Default` is kept (same `IterableMap::new(b"f")`), for `Contract`
//   58-68    the official `struct Contract` (with `nonce`) and its `Default` are removed
//   71       `impl VersionedContract` becomes `impl Contract`
//   77-80    `deposit` writes to `self.funders` directly
//   81       `contract.nonce += 1;` is removed
//   84-89    `get_nonce` is removed
//   92       `get_deposit` reads `self.funders` directly
//   96-166   the unit-test module is removed
//   4-6      this comment replaces the doc comment
use near_sdk::store::IterableMap;
use near_sdk::{env, log, near, AccountId, NearToken};

#[near(contract_state)]
pub struct Contract {
    funders: IterableMap<AccountId, NearToken>,
}

impl Default for Contract {
    fn default() -> Self {
        Self { funders: IterableMap::new(b"f") }
    }
}

#[near]
impl Contract {
    #[payable]
    pub fn deposit(&mut self) {
        let account_id = env::predecessor_account_id();
        let deposit = env::attached_deposit();
        log!("{} deposited {} yNEAR", account_id, deposit);
        let res = self.funders.entry(account_id.clone()).or_default();
        let res = res.saturating_add(deposit);
        self.funders.insert(account_id, res);
    }

    pub fn get_deposit(&self, account_id: &AccountId) -> Option<&NearToken> {
        self.funders.get(account_id)
    }
}
