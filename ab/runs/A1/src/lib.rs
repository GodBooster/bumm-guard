//! N06 study contract: event registration with a fixed capacity.
//!
//! Copied unchanged from research/bumm_value_study/pilot_A_N06/src/lib.rs (same public
//! methods and unit tests). Acceptance scenarios: scenarios/n06/.
//! Build: `cargo build --release --target wasm32-unknown-unknown -p event_registration`
//! from tests/fixtures/near (rustc 1.85.0, pinned there).

use near_sdk::store::IterableSet;
use near_sdk::{env, near, require, AccountId, PanicOnDefault};

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct EventRegistration {
    capacity: u32,
    registered: IterableSet<AccountId>,
}

#[near]
impl EventRegistration {
    #[init]
    pub fn new(capacity: u32) -> Self {
        require!(capacity > 0, "Capacity must be greater than zero");
        Self {
            capacity,
            registered: IterableSet::new(b"r"),
        }
    }

    /// Registers the caller. Panics with an explicit message if the event is full
    /// or the caller is already registered.
    pub fn register(&mut self) {
        let account = env::predecessor_account_id();
        require!(
            !self.registered.contains(&account),
            format!("Account {} is already registered", account)
        );
        require!(
            self.registered.len() < self.capacity,
            format!("Event is full: capacity {} reached, registration refused", self.capacity)
        );
        self.registered.insert(account);
    }

    /// Cancels the caller's registration and frees a seat.
    pub fn cancel(&mut self) {
        let account = env::predecessor_account_id();
        require!(
            self.registered.remove(&account),
            format!("Account {} is not registered", account)
        );
    }

    pub fn get_capacity(&self) -> u32 {
        self.capacity
    }

    pub fn get_registered_count(&self) -> u32 {
        self.registered.len()
    }

    pub fn get_registered(&self) -> Vec<AccountId> {
        self.registered.iter().cloned().collect()
    }

    pub fn is_registered(&self, account_id: AccountId) -> bool {
        self.registered.contains(&account_id)
    }

    pub fn get_seats_left(&self) -> u32 {
        self.capacity - self.registered.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use near_sdk::test_utils::VMContextBuilder;
    use near_sdk::testing_env;

    fn as_account(name: &str) {
        let mut b = VMContextBuilder::new();
        b.predecessor_account_id(name.parse().unwrap());
        testing_env!(b.build());
    }

    fn acc(name: &str) -> AccountId {
        name.parse().unwrap()
    }

    fn new_event(cap: u32) -> EventRegistration {
        as_account("organizer.near");
        EventRegistration::new(cap)
    }

    #[test]
    fn s1_capacity_is_two() {
        let c = new_event(2);
        assert_eq!(c.get_capacity(), 2);
        assert_eq!(c.get_registered_count(), 0);
    }

    #[test]
    fn s2_two_different_accounts_register() {
        let mut c = new_event(2);
        as_account("alice.near");
        c.register();
        as_account("bob.near");
        c.register();
        assert_eq!(c.get_registered_count(), 2);
        assert!(c.is_registered(acc("alice.near")));
        assert!(c.is_registered(acc("bob.near")));
        assert_eq!(c.get_seats_left(), 0);
    }

    #[test]
    #[should_panic(expected = "Event is full: capacity 2 reached, registration refused")]
    fn s3_third_registration_refused() {
        let mut c = new_event(2);
        as_account("alice.near");
        c.register();
        as_account("bob.near");
        c.register();
        as_account("carol.near");
        c.register();
    }

    #[test]
    fn s4_cancel_frees_seat_then_new_registration_succeeds() {
        let mut c = new_event(2);
        as_account("alice.near");
        c.register();
        as_account("bob.near");
        c.register();
        as_account("alice.near");
        c.cancel();
        assert_eq!(c.get_registered_count(), 1);
        assert!(!c.is_registered(acc("alice.near")));
        as_account("carol.near");
        c.register();
        assert_eq!(c.get_registered_count(), 2);
        assert!(c.is_registered(acc("carol.near")));
    }

    #[test]
    fn s5_state_is_queryable() {
        let mut c = new_event(2);
        as_account("alice.near");
        c.register();
        as_account("bob.near");
        c.register();
        let mut list = c.get_registered();
        list.sort();
        assert_eq!(list, vec![acc("alice.near"), acc("bob.near")]);
        assert_eq!(c.get_registered_count(), 2);
        assert_eq!(c.get_capacity(), 2);
    }

    #[test]
    #[should_panic(expected = "already registered")]
    fn extra_double_registration_refused() {
        let mut c = new_event(2);
        as_account("alice.near");
        c.register();
        c.register();
    }

    #[test]
    #[should_panic(expected = "is not registered")]
    fn extra_cancel_unregistered_refused() {
        let mut c = new_event(2);
        as_account("alice.near");
        c.cancel();
    }
}
