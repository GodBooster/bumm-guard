use near_sdk::json_types::U128;
use near_sdk::{env, near, require, AccountId, NearToken, PanicOnDefault, Promise};

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Treasury { owner: AccountId }

#[near]
impl Treasury {
    #[init]
    pub fn new(owner: AccountId) -> Self { Self { owner } }

    #[payable]
    pub fn fund(&mut self) {}

    // SAFE: authorizes the direct caller
    pub fn sweep(&mut self, to: AccountId, amount: U128) -> Promise {
        require!(env::predecessor_account_id() == self.owner, "owner only");
        Promise::new(to).transfer(NearToken::from_yoctonear(amount.0))
    }
}
