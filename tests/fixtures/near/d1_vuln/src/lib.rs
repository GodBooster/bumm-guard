use near_sdk::{env, near, require, AccountId, PanicOnDefault, Promise, PromiseError, Gas};

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Escrow { buyer: AccountId, seller: AccountId, paid: bool }

#[near]
impl Escrow {
    #[init]
    pub fn new(buyer: AccountId, seller: AccountId) -> Self { Self { buyer, seller, paid: false } }

    #[payable]
    pub fn pay(&mut self) -> Promise {
        require!(env::predecessor_account_id() == self.buyer, "buyer only");
        Promise::new(self.seller.clone()).transfer(env::attached_deposit()).then(
            Self::ext(env::current_account_id())
                .with_static_gas(Gas::from_tgas(5))
                .on_paid(),
        )
    }

    // VULNERABLE: no #[private] -> anyone can call and set paid = true
    pub fn on_paid(&mut self, #[callback_result] r: Result<(), PromiseError>) {
        if r.is_ok() { self.paid = true; }
    }
    pub fn is_paid(&self) -> bool { self.paid }
}
