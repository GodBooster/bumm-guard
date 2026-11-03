//! Event registration with a fixed capacity and a waitlist.
//!
//! Copied unchanged from research/bumm_value_study/pilot_A_N06/src/lib.rs (same public
//! methods and unit tests). Acceptance scenarios: scenarios/n06/.
//! Build: `cargo build --release --target wasm32-unknown-unknown -p event_registration`
//! from tests/fixtures/near (rustc 1.85.0, pinned there).

use near_sdk::borsh::{BorshDeserialize, BorshSerialize};
use near_sdk::store::{IterableSet, LookupMap};
use near_sdk::{env, near, require, AccountId, PanicOnDefault};

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct EventRegistration {
    capacity: u32,
    registered: IterableSet<AccountId>,
    /// Waitlist queue: slot number -> account. Live slots are in `head..tail`; a slot
    /// vacated by a leaving account is simply removed (order of the rest is unchanged).
    waitlist: LookupMap<u64, AccountId>,
    /// Reverse index: account -> its slot in `waitlist` (membership test).
    waitlist_tickets: LookupMap<AccountId, u64>,
    /// First slot that may still be occupied.
    head: u64,
    /// Next free slot.
    tail: u64,
}

/// Layout of the state before the waitlist was added (used by `migrate`).
#[derive(BorshDeserialize, BorshSerialize)]
#[borsh(crate = "near_sdk::borsh")]
struct OldEventRegistration {
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
            waitlist: LookupMap::new(b"w"),
            waitlist_tickets: LookupMap::new(b"t"),
            head: 0,
            tail: 0,
        }
    }

    /// Upgrade an already-deployed pre-waitlist contract in place (keeps registrations).
    /// Call once right after deploying the new code: `migrate` (owner/contract account only).
    #[private]
    #[init(ignore_state)]
    pub fn migrate() -> Self {
        let old: OldEventRegistration =
            env::state_read().unwrap_or_else(|| env::panic_str("No old state to migrate"));
        Self {
            capacity: old.capacity,
            registered: old.registered,
            waitlist: LookupMap::new(b"w"),
            waitlist_tickets: LookupMap::new(b"t"),
            head: 0,
            tail: 0,
        }
    }

    /// Registers the caller. If the event is full the caller joins the end of the
    /// waitlist instead. Panics if the caller is already registered or waitlisted.
    pub fn register(&mut self) {
        let account = env::predecessor_account_id();
        require!(
            !self.registered.contains(&account),
            format!("Account {} is already registered", account)
        );
        require!(
            !self.waitlist_tickets.contains_key(&account),
            format!("Account {} is already on the waitlist", account)
        );
        if self.registered.len() < self.capacity {
            self.registered.insert(account);
        } else {
            let ticket = self.tail;
            self.tail += 1;
            self.waitlist_tickets.insert(account.clone(), ticket);
            self.waitlist.insert(ticket, account);
        }
    }

    /// Cancels the caller's registration (the first waitlisted account, if any, takes
    /// the freed seat) or removes the caller from the waitlist.
    pub fn cancel(&mut self) {
        let account = env::predecessor_account_id();
        if self.registered.remove(&account) {
            // Skip slots vacated by accounts that left the waitlist.
            while self.head < self.tail {
                let slot = self.head;
                self.head += 1;
                if let Some(next) = self.waitlist.remove(&slot) {
                    self.waitlist_tickets.remove(&next);
                    self.registered.insert(next);
                    break;
                }
            }
        } else if let Some(ticket) = self.waitlist_tickets.remove(&account) {
            self.waitlist.remove(&ticket);
        } else {
            env::panic_str(&format!("Account {} is not registered", account));
        }
    }

    pub fn get_waitlist(&self) -> Vec<AccountId> {
        (self.head..self.tail)
            .filter_map(|slot| self.waitlist.get(&slot).cloned())
            .collect()
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
    fn s3_third_registration_goes_to_waitlist() {
        let mut c = new_event(2);
        for n in ["alice.near", "bob.near", "carol.near"] {
            as_account(n);
            c.register();
        }
        assert_eq!(c.get_registered_count(), 2);
        assert!(!c.is_registered(acc("carol.near")));
        assert_eq!(c.get_waitlist(), vec![acc("carol.near")]);
        assert_eq!(c.get_seats_left(), 0);
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

    #[test]
    #[should_panic(expected = "already on the waitlist")]
    fn waitlist_double_join_refused() {
        let mut c = new_event(1);
        as_account("alice.near");
        c.register();
        as_account("bob.near");
        c.register();
        c.register();
    }

    #[test]
    fn cancel_promotes_first_in_order() {
        let mut c = new_event(1);
        for n in ["a.near", "b.near", "c.near", "d.near"] {
            as_account(n);
            c.register();
        }
        assert_eq!(c.get_waitlist(), vec![acc("b.near"), acc("c.near"), acc("d.near")]);
        as_account("a.near");
        c.cancel();
        assert!(c.is_registered(acc("b.near")));
        assert_eq!(c.get_waitlist(), vec![acc("c.near"), acc("d.near")]);
        assert_eq!(c.get_registered_count(), 1);
        assert_eq!(c.get_seats_left(), 0);
    }

    #[test]
    fn waitlisted_cancel_leaves_waitlist_preserving_order() {
        let mut c = new_event(1);
        for n in ["a.near", "b.near", "c.near", "d.near"] {
            as_account(n);
            c.register();
        }
        as_account("c.near");
        c.cancel();
        assert_eq!(c.get_waitlist(), vec![acc("b.near"), acc("d.near")]);
        assert_eq!(c.get_registered(), vec![acc("a.near")]);
        // can rejoin at the end
        c.register();
        assert_eq!(c.get_waitlist(), vec![acc("b.near"), acc("d.near"), acc("c.near")]);
        // promoted account can cancel again, next one promoted
        as_account("a.near");
        c.cancel();
        as_account("b.near");
        c.cancel();
        assert_eq!(c.get_registered(), vec![acc("d.near")]);
        assert_eq!(c.get_waitlist(), vec![acc("c.near")]);
    }

    #[test]
    fn cancel_with_free_seat_and_empty_waitlist() {
        let mut c = new_event(2);
        as_account("a.near");
        c.register();
        c.cancel();
        assert_eq!(c.get_seats_left(), 2);
        assert!(c.get_waitlist().is_empty());
    }

    #[test]
    #[should_panic(expected = "not registered")]
    fn cancel_after_leaving_waitlist_refused() {
        let mut c = new_event(1);
        as_account("a.near");
        c.register();
        as_account("b.near");
        c.register();
        c.cancel();
        c.cancel();
    }
}
