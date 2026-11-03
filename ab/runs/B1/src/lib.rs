//! Event registration with a fixed capacity and a FIFO waitlist.
//!
//! Waitlist storage: tickets. `wait_slots` maps a ticket number to the account,
//! `wait_ticket` maps an account to its ticket. `wait_head..wait_tail` is the range of
//! tickets that may still be present; leaving the waitlist removes the entries and leaves
//! a gap that promotion and `get_waitlist` skip. Order is the ticket order.

use near_sdk::store::{IterableSet, LookupMap};
use near_sdk::{env, near, require, AccountId, PanicOnDefault};

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct EventRegistration {
    capacity: u32,
    registered: IterableSet<AccountId>,
    wait_slots: LookupMap<u64, AccountId>,
    wait_ticket: LookupMap<AccountId, u64>,
    wait_head: u64,
    wait_tail: u64,
}

#[near]
impl EventRegistration {
    #[init]
    pub fn new(capacity: u32) -> Self {
        require!(capacity > 0, "Capacity must be greater than zero");
        Self {
            capacity,
            registered: IterableSet::new(b"r"),
            wait_slots: LookupMap::new(b"s"),
            wait_ticket: LookupMap::new(b"t"),
            wait_head: 0,
            wait_tail: 0,
        }
    }

    /// Registers the caller; if the event is full the caller joins the end of the
    /// waitlist. Panics if the caller is already registered or already waitlisted.
    pub fn register(&mut self) {
        let account = env::predecessor_account_id();
        require!(
            !self.registered.contains(&account),
            format!("Account {} is already registered", account)
        );
        require!(
            !self.wait_ticket.contains_key(&account),
            format!("Account {} is already on the waitlist", account)
        );
        if self.registered.len() < self.capacity {
            self.registered.insert(account);
        } else {
            let ticket = self.wait_tail;
            self.wait_tail += 1;
            self.wait_slots.insert(ticket, account.clone());
            self.wait_ticket.insert(account, ticket);
        }
    }

    /// Cancels the caller's registration (the first waitlisted account is promoted)
    /// or removes the caller from the waitlist.
    pub fn cancel(&mut self) {
        let account = env::predecessor_account_id();
        if self.registered.remove(&account) {
            self.promote_waitlisted();
        } else if let Some(ticket) = self.wait_ticket.remove(&account) {
            self.wait_slots.remove(&ticket);
        } else {
            env::panic_str(&format!("Account {} is not registered", account));
        }
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

    /// The waitlist in order, first in line first.
    pub fn get_waitlist(&self) -> Vec<AccountId> {
        (self.wait_head..self.wait_tail)
            .filter_map(|t| self.wait_slots.get(&t).cloned())
            .collect()
    }
}

impl EventRegistration {
    /// Moves waitlisted accounts into free seats, oldest ticket first.
    fn promote_waitlisted(&mut self) {
        while self.registered.len() < self.capacity && self.wait_head < self.wait_tail {
            let ticket = self.wait_head;
            self.wait_head += 1;
            if let Some(account) = self.wait_slots.remove(&ticket) {
                self.wait_ticket.remove(&account);
                self.registered.insert(account);
            }
        }
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
    fn s3_third_registration_is_waitlisted() {
        let mut c = new_event(2);
        for n in ["alice.near", "bob.near", "carol.near"] {
            as_account(n);
            c.register();
        }
        assert_eq!(c.get_registered_count(), 2);
        assert!(!c.is_registered(acc("carol.near")));
        assert_eq!(c.get_waitlist(), vec![acc("carol.near")]);
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
        assert!(c.get_waitlist().is_empty());
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

    fn full_event_with_waitlist() -> EventRegistration {
        let mut c = new_event(2);
        for n in ["alice.near", "bob.near", "carol.near", "dave.near", "erin.near"] {
            as_account(n);
            c.register();
        }
        c
    }

    #[test]
    fn waitlist_order_and_promotion_fifo() {
        let mut c = full_event_with_waitlist();
        assert_eq!(c.get_waitlist(), vec![acc("carol.near"), acc("dave.near"), acc("erin.near")]);
        as_account("alice.near");
        c.cancel();
        assert!(c.is_registered(acc("carol.near")));
        assert_eq!(c.get_waitlist(), vec![acc("dave.near"), acc("erin.near")]);
        assert_eq!(c.get_registered_count(), 2);
        as_account("bob.near");
        c.cancel();
        assert!(c.is_registered(acc("dave.near")));
        assert_eq!(c.get_waitlist(), vec![acc("erin.near")]);
        assert_eq!(c.get_seats_left(), 0);
    }

    #[test]
    fn waitlisted_can_leave_and_order_is_kept() {
        let mut c = full_event_with_waitlist();
        as_account("dave.near");
        c.cancel();
        assert_eq!(c.get_waitlist(), vec![acc("carol.near"), acc("erin.near")]);
        as_account("carol.near");
        c.cancel();
        as_account("alice.near");
        c.cancel();
        assert!(c.is_registered(acc("erin.near")));
        assert!(c.get_waitlist().is_empty());
        // carol left, can join again at the end
        as_account("carol.near");
        c.register();
        assert_eq!(c.get_waitlist(), vec![acc("carol.near")]);
    }

    #[test]
    fn cancel_with_empty_waitlist_frees_seat() {
        let mut c = new_event(2);
        as_account("alice.near");
        c.register();
        c.cancel();
        assert_eq!(c.get_seats_left(), 2);
        assert!(c.get_waitlist().is_empty());
    }

    #[test]
    #[should_panic(expected = "already on the waitlist")]
    fn double_waitlist_refused() {
        let mut c = full_event_with_waitlist();
        as_account("carol.near");
        c.register();
    }

    #[test]
    #[should_panic(expected = "already registered")]
    fn registered_cannot_register_when_full() {
        let mut c = full_event_with_waitlist();
        as_account("alice.near");
        c.register();
    }

    #[test]
    #[should_panic(expected = "not registered")]
    fn cancel_twice_refused() {
        let mut c = full_event_with_waitlist();
        as_account("dave.near");
        c.cancel();
        c.cancel();
    }
}
