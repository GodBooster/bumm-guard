//! Escrow v1: a buyer locks a deposit for a seller; only the buyer can release it.
//!
//! Second fixture pair for the `upgrade` step (first pair: event_registration -> its waitlist
//! variant). Here the layout change of v2 sits *inside* a map entry (`Deal`), not in the root
//! struct, so a code-only upgrade to v2 still answers `deal_count` and only fails when a deal
//! is read. Acceptance scenarios: scenarios/escrow/; upgrade scenarios: scenarios/upgrade/escrow_*.
//! Build: `cargo build --release --target wasm32-unknown-unknown -p escrow_v1`
//! from tests/fixtures/near (rustc 1.85.0, pinned there).

use near_sdk::store::IterableMap;
use near_sdk::{env, near, require, AccountId, NearToken, PanicOnDefault, Promise};

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Escrow {
    owner: AccountId,
    deals: IterableMap<u64, Deal>,
    next_id: u64,
}

/// Stored record of one deal (Borsh, inside `deals`).
#[near(serializers = [borsh])]
pub struct Deal {
    buyer: AccountId,
    seller: AccountId,
    amount: NearToken,
    released: bool,
}

/// What `get_deal` returns (JSON; `amount` is a yoctoNEAR string).
#[near(serializers = [json])]
pub struct DealView {
    pub id: u64,
    pub buyer: AccountId,
    pub seller: AccountId,
    pub amount: NearToken,
    pub released: bool,
}

#[near]
impl Escrow {
    #[init]
    pub fn new() -> Self {
        Self {
            owner: env::predecessor_account_id(),
            deals: IterableMap::new(b"d"),
            next_id: 0,
        }
    }

    /// Opens a deal for `seller`, funded with the attached deposit (must be positive).
    /// Returns the deal id.
    #[payable]
    pub fn create_deal(&mut self, seller: AccountId) -> u64 {
        let amount = env::attached_deposit();
        require!(!amount.is_zero(), "Attach a positive deposit to fund the deal");
        let id = self.next_id;
        self.next_id += 1;
        self.deals.insert(
            id,
            Deal { buyer: env::predecessor_account_id(), seller, amount, released: false },
        );
        id
    }

    /// Pays the locked amount to the seller. Only the buyer may release, and only once.
    pub fn release(&mut self, id: u64) -> Promise {
        let caller = env::predecessor_account_id();
        let deal = self
            .deals
            .get_mut(&id)
            .unwrap_or_else(|| env::panic_str(&format!("Deal {} does not exist", id)));
        require!(deal.buyer == caller, format!("Only the buyer can release deal {}", id));
        require!(!deal.released, format!("Deal {} is already released", id));
        deal.released = true;
        Promise::new(deal.seller.clone()).transfer(deal.amount)
    }

    pub fn get_deal(&self, id: u64) -> Option<DealView> {
        self.deals.get(&id).map(|d| DealView {
            id,
            buyer: d.buyer.clone(),
            seller: d.seller.clone(),
            amount: d.amount,
            released: d.released,
        })
    }

    pub fn deal_count(&self) -> u64 {
        u64::from(self.deals.len())
    }

    pub fn get_owner(&self) -> AccountId {
        self.owner.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use near_sdk::test_utils::VMContextBuilder;
    use near_sdk::testing_env;

    fn as_account(name: &str, deposit: NearToken) {
        let mut b = VMContextBuilder::new();
        b.predecessor_account_id(name.parse().unwrap()).attached_deposit(deposit);
        testing_env!(b.build());
    }

    fn acc(name: &str) -> AccountId {
        name.parse().unwrap()
    }

    fn near(n: u128) -> NearToken {
        NearToken::from_near(n)
    }

    fn new_escrow() -> Escrow {
        as_account("owner.near", NearToken::from_yoctonear(0));
        Escrow::new()
    }

    #[test]
    fn owner_is_the_deployer_and_count_starts_at_zero() {
        let c = new_escrow();
        assert_eq!(c.get_owner(), acc("owner.near"));
        assert_eq!(c.deal_count(), 0);
        assert!(c.get_deal(0).is_none());
    }

    #[test]
    fn create_deal_records_buyer_seller_and_amount() {
        let mut c = new_escrow();
        as_account("alice.near", near(2));
        assert_eq!(c.create_deal(acc("bob.near")), 0);
        as_account("alice.near", near(3));
        assert_eq!(c.create_deal(acc("carol.near")), 1);
        assert_eq!(c.deal_count(), 2);
        let d = c.get_deal(1).unwrap();
        assert_eq!(d.buyer, acc("alice.near"));
        assert_eq!(d.seller, acc("carol.near"));
        assert_eq!(d.amount, near(3));
        assert!(!d.released);
    }

    #[test]
    fn buyer_releases_once() {
        let mut c = new_escrow();
        as_account("alice.near", near(2));
        c.create_deal(acc("bob.near"));
        as_account("alice.near", NearToken::from_yoctonear(0));
        c.release(0);
        assert!(c.get_deal(0).unwrap().released);
    }

    #[test]
    #[should_panic(expected = "positive deposit")]
    fn create_deal_without_deposit_refused() {
        let mut c = new_escrow();
        as_account("alice.near", NearToken::from_yoctonear(0));
        c.create_deal(acc("bob.near"));
    }

    #[test]
    #[should_panic(expected = "Only the buyer can release")]
    fn release_by_non_buyer_refused() {
        let mut c = new_escrow();
        as_account("alice.near", near(2));
        c.create_deal(acc("bob.near"));
        as_account("bob.near", NearToken::from_yoctonear(0));
        c.release(0);
    }

    #[test]
    #[should_panic(expected = "already released")]
    fn second_release_refused() {
        let mut c = new_escrow();
        as_account("alice.near", near(2));
        c.create_deal(acc("bob.near"));
        as_account("alice.near", NearToken::from_yoctonear(0));
        c.release(0);
        c.release(0);
    }

    #[test]
    #[should_panic(expected = "does not exist")]
    fn release_unknown_deal_refused() {
        let mut c = new_escrow();
        as_account("alice.near", NearToken::from_yoctonear(0));
        c.release(7);
    }
}
