use near_sdk::json_types::U128;
use near_sdk::store::LookupMap;
use near_sdk::{env, near, require, AccountId, NearToken, PanicOnDefault, Promise, PromiseError};

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Vault { balances: LookupMap<AccountId, u128> }

#[near]
impl Vault {
    #[init]
    pub fn new() -> Self { Self { balances: LookupMap::new(b"b") } }

    #[payable]
    pub fn deposit(&mut self) {
        let u = env::predecessor_account_id();
        let cur = self.balances.get(&u).copied().unwrap_or(0);
        self.balances.insert(u, cur + env::attached_deposit().as_yoctonear());
    }

    pub fn withdraw(&mut self, to: AccountId, amount: U128) -> Promise {
        let user = env::predecessor_account_id();
        let bal = self.balances.get(&user).copied().unwrap_or(0);
        require!(bal >= amount.0, "insufficient");
        self.balances.insert(user.clone(), bal - amount.0); // debit first (good)
        Promise::new(to)
            .transfer(NearToken::from_yoctonear(amount.0))
            .then(Self::ext(env::current_account_id()).on_withdraw(user, amount))
    }

    // SAFE: failure branch restores the debit
    #[private]
    pub fn on_withdraw(&mut self, user: AccountId, amount: U128, #[callback_result] r: Result<(), PromiseError>) {
        if r.is_err() {
            let cur = self.balances.get(&user).copied().unwrap_or(0);
            self.balances.insert(user, cur + amount.0);
        }
    }

    pub fn balance_of(&self, a: AccountId) -> U128 { U128(self.balances.get(&a).copied().unwrap_or(0)) }
}
