// Ours, not upstream. The V0 era of near-sdk-rs examples/versioned/src/lib.rs (commit 8b91e264,
// tag near-sdk-v5.17.2; MIT OR Apache-2.0, see ../../NOTICE.md): the same contract before V1
// existed, so deposits are stored in the V0 variant (enum tag 0) exactly as the official code
// reads them. Lines that differ from the official file (its line numbers):
//   10       the `V1(Contract)` variant is removed (the enum has only `V0(ContractV0)`)
//   14-31    `contract_mut` returns `&mut ContractV0` from the only variant (no V0 -> V1 conversion)
//   36       the `V1` arm of `funders` is removed
//   43       `Default` returns `V0(ContractV0::default())` instead of `V1(Contract::default())`
//   58-68    `struct Contract` (with `nonce`) and its `Default` are removed
//   81       `contract.nonce += 1;` is removed
//   84-89    `get_nonce` is removed
//   96-166   the unit-test module is removed
//   4-6      this comment replaces the doc comment
// Why not only `Default` returning V0: the official `deposit` converts V0 state to V1 on its
// first write (`contract_mut`), so such a build never keeps a deposit in the V0 variant.
use near_sdk::store::IterableMap;
use near_sdk::{env, log, near, AccountId, NearToken};

#[near(contract_state)]
pub enum VersionedContract {
    V0(ContractV0),
}

impl VersionedContract {
    fn contract_mut(&mut self) -> &mut ContractV0 {
        match self {
            Self::V0(contract) => contract,
        }
    }

    fn funders(&self) -> &IterableMap<AccountId, NearToken> {
        match self {
            Self::V0(contract) => &contract.funders,
        }
    }
}

impl Default for VersionedContract {
    fn default() -> Self {
        VersionedContract::V0(ContractV0::default())
    }
}

#[near]
pub struct ContractV0 {
    funders: IterableMap<AccountId, NearToken>,
}

impl Default for ContractV0 {
    fn default() -> Self {
        Self { funders: IterableMap::new(b"f") }
    }
}

#[near]
impl VersionedContract {
    #[payable]
    pub fn deposit(&mut self) {
        let account_id = env::predecessor_account_id();
        let deposit = env::attached_deposit();
        log!("{} deposited {} yNEAR", account_id, deposit);
        let contract = self.contract_mut();
        let res = contract.funders.entry(account_id.clone()).or_default();
        let res = res.saturating_add(deposit);
        contract.funders.insert(account_id, res);
    }

    pub fn get_deposit(&self, account_id: &AccountId) -> Option<&NearToken> {
        self.funders().get(account_id)
    }
}
