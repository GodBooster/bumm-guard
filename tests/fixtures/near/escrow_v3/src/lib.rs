//! Escrow v3: escrow v2 with one change, a caller check inside `migrate`.
//!
//! escrow_v2 marks `migrate` `#[private] #[init(ignore_state)]`. With near-sdk 5.17.2 (pinned
//! here) that attribute does nothing on an init method: near-sdk-macros before 5.18.0 parse
//! `#[private]` and generate no caller check for `#[init]` functions, so any account can call
//! `migrate` of escrow_v2. The access probe of `bumm-sim` shows it (the call of another account
//! goes through on the not yet migrated state) and the deploy gate refuses that upgrade. Here
//! the first line of `migrate` compares the caller with the contract account itself, which
//! works on every near-sdk version; the attribute stays for readers and for near-sdk 5.18.0+.
//!
//! As in v2: every deal carries a `status` (Funded / Released / Refunded) instead of the v1
//! `released` flag, plus `created_at_ms`; the seller can refund a funded deal.
//!
//! The root struct keeps the v1 shape (`owner`, `deals`, `next_id`); only the record inside
//! `deals` changed. A code-only upgrade from escrow_v1 therefore still answers `deal_count`
//! (the map's length lives in the root struct) and panics with the map's own message
//! (`Cannot deserialize element`) as soon as a record is read. `migrate` rewrites the records.
//! Acceptance scenario: scenarios/upgrade/escrow_v1_to_v3_with_migrate.json.
//! Build: `cargo build --release --target wasm32-unknown-unknown -p escrow_v3`
//! from tests/fixtures/near (rustc 1.85.0, pinned there).

use near_sdk::borsh::{BorshDeserialize, BorshSerialize};
use near_sdk::store::IterableMap;
use near_sdk::{env, near, require, AccountId, NearToken, PanicOnDefault, Promise};

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct Escrow {
    owner: AccountId,
    deals: IterableMap<u64, Deal>,
    next_id: u64,
}

#[near(serializers = [borsh])]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum DealStatus {
    Funded,
    Released,
    Refunded,
}

impl DealStatus {
    fn as_str(self) -> &'static str {
        match self {
            DealStatus::Funded => "Funded",
            DealStatus::Released => "Released",
            DealStatus::Refunded => "Refunded",
        }
    }
}

/// Stored record of one deal (Borsh, inside `deals`).
#[near(serializers = [borsh])]
pub struct Deal {
    buyer: AccountId,
    seller: AccountId,
    amount: NearToken,
    status: DealStatus,
    /// Block timestamp of `create_deal` in ms; 0 for deals migrated from v1 (v1 did not
    /// record it, so the value is unknown rather than guessed).
    created_at_ms: u64,
}

/// What `get_deal` returns (JSON; `amount` is a yoctoNEAR string, `status` one of
/// "Funded", "Released", "Refunded").
#[near(serializers = [json])]
pub struct DealView {
    pub id: u64,
    pub buyer: AccountId,
    pub seller: AccountId,
    pub amount: NearToken,
    pub status: String,
    pub created_at_ms: u64,
}

/// Layout of the v1 state (used by `migrate`). Same root fields; the old record type.
#[derive(BorshDeserialize, BorshSerialize)]
#[borsh(crate = "near_sdk::borsh")]
struct EscrowV1 {
    owner: AccountId,
    deals: IterableMap<u64, DealV1>,
    next_id: u64,
}

#[derive(BorshDeserialize, BorshSerialize)]
#[borsh(crate = "near_sdk::borsh")]
struct DealV1 {
    buyer: AccountId,
    seller: AccountId,
    amount: NearToken,
    released: bool,
}

/// Storage prefix of the v1 map (`escrow_v1` uses `b"d"`); the old state read by `migrate`
/// carries it in its own serialized `IterableMap`, so the code needs it only in the tests.
#[cfg(test)]
const DEALS_PREFIX_V1: &[u8] = b"d";
/// Storage prefix of the v2 map. Different from v1 on purpose, see `migrate`.
const DEALS_PREFIX: &[u8] = b"e";

#[near]
impl Escrow {
    #[init]
    pub fn new() -> Self {
        Self {
            owner: env::predecessor_account_id(),
            deals: IterableMap::new(DEALS_PREFIX),
            next_id: 0,
        }
    }

    /// Upgrade an already-deployed escrow_v1 contract in place (keeps every deal).
    /// Call once, in the same batch as the deploy: `migrate` (contract account only; the check
    /// is the first line of the body, see the module comment for why the attribute is not enough).
    ///
    /// Why a new prefix: an `IterableMap` keeps three things under its prefix, the key
    /// vector (`<prefix>v…`, with its `len` serialized inside the root struct), the values
    /// (`<prefix>m` + key, each stored together with its index into the key vector) and
    /// a write-back cache. Re-inserting the new records into a map that uses the old prefix
    /// would overwrite the value entries, but the v1 map object still holds the old key
    /// vector and its own cache, and whichever map is dropped last flushes last, so an
    /// interleaving mistake silently loses or duplicates records. Moving the records to a
    /// fresh prefix keeps the two maps independent: the new one is built from scratch, then
    /// the old one is cleared (`clear` drops the keys and removes the values without
    /// deserializing them, which the new code could not do), so no v1 bytes stay behind and
    /// `deal_count` and every `get_deal` are correct afterwards.
    #[private]
    #[init(ignore_state)]
    pub fn migrate() -> Self {
        require!(
            env::predecessor_account_id() == env::current_account_id(),
            "only the contract itself may migrate"
        );
        let mut old: EscrowV1 =
            env::state_read().unwrap_or_else(|| env::panic_str("No old state to migrate"));
        let mut deals = IterableMap::new(DEALS_PREFIX);
        for (id, d) in old.deals.iter() {
            deals.insert(
                *id,
                Deal {
                    buyer: d.buyer.clone(),
                    seller: d.seller.clone(),
                    amount: d.amount,
                    status: if d.released { DealStatus::Released } else { DealStatus::Funded },
                    created_at_ms: 0,
                },
            );
        }
        old.deals.clear();
        Self { owner: old.owner, deals, next_id: old.next_id }
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
            Deal {
                buyer: env::predecessor_account_id(),
                seller,
                amount,
                status: DealStatus::Funded,
                created_at_ms: env::block_timestamp_ms(),
            },
        );
        id
    }

    /// Pays the locked amount to the seller. Only the buyer may release, and only a funded deal.
    pub fn release(&mut self, id: u64) -> Promise {
        let caller = env::predecessor_account_id();
        let deal = self.deal_mut(id);
        require!(deal.buyer == caller, format!("Only the buyer can release deal {}", id));
        require!(
            deal.status == DealStatus::Funded,
            format!("Deal {} is already {}", id, deal.status.as_str())
        );
        deal.status = DealStatus::Released;
        Promise::new(deal.seller.clone()).transfer(deal.amount)
    }

    /// Returns the locked amount to the buyer. Only the seller may refund, and only a funded deal.
    pub fn refund(&mut self, id: u64) -> Promise {
        let caller = env::predecessor_account_id();
        let deal = self.deal_mut(id);
        require!(deal.seller == caller, format!("Only the seller can refund deal {}", id));
        require!(
            deal.status == DealStatus::Funded,
            format!("Deal {} is already {}", id, deal.status.as_str())
        );
        deal.status = DealStatus::Refunded;
        Promise::new(deal.buyer.clone()).transfer(deal.amount)
    }

    pub fn get_deal(&self, id: u64) -> Option<DealView> {
        self.deals.get(&id).map(|d| DealView {
            id,
            buyer: d.buyer.clone(),
            seller: d.seller.clone(),
            amount: d.amount,
            status: d.status.as_str().to_string(),
            created_at_ms: d.created_at_ms,
        })
    }

    pub fn deal_count(&self) -> u64 {
        u64::from(self.deals.len())
    }

    pub fn get_owner(&self) -> AccountId {
        self.owner.clone()
    }

    fn deal_mut(&mut self, id: u64) -> &mut Deal {
        self.deals
            .get_mut(&id)
            .unwrap_or_else(|| env::panic_str(&format!("Deal {} does not exist", id)))
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

    /// The contract calling itself, as in the deploy + migrate batch.
    fn as_contract() {
        let mut b = VMContextBuilder::new();
        b.current_account_id(acc("escrow.near")).predecessor_account_id(acc("escrow.near"));
        testing_env!(b.build());
    }

    /// Another account calling `migrate`: refused before any state is read.
    #[test]
    #[should_panic(expected = "only the contract itself may migrate")]
    fn migrate_refuses_another_caller() {
        let mut b = VMContextBuilder::new();
        b.current_account_id(acc("escrow.near")).predecessor_account_id(acc("mallory.near"));
        testing_env!(b.build());
        Escrow::migrate();
    }

    fn near(n: u128) -> NearToken {
        NearToken::from_near(n)
    }

    fn zero() -> NearToken {
        NearToken::from_yoctonear(0)
    }

    fn new_escrow() -> Escrow {
        as_account("owner.near", zero());
        Escrow::new()
    }

    #[test]
    fn create_deal_is_funded() {
        let mut c = new_escrow();
        as_account("alice.near", near(2));
        assert_eq!(c.create_deal(acc("bob.near")), 0);
        assert_eq!(c.deal_count(), 1);
        let d = c.get_deal(0).unwrap();
        assert_eq!(d.buyer, acc("alice.near"));
        assert_eq!(d.seller, acc("bob.near"));
        assert_eq!(d.amount, near(2));
        assert_eq!(d.status, "Funded");
    }

    #[test]
    fn buyer_releases_then_seller_cannot_refund() {
        let mut c = new_escrow();
        as_account("alice.near", near(2));
        c.create_deal(acc("bob.near"));
        as_account("alice.near", zero());
        c.release(0);
        assert_eq!(c.get_deal(0).unwrap().status, "Released");
    }

    #[test]
    fn seller_refunds_a_funded_deal() {
        let mut c = new_escrow();
        as_account("alice.near", near(2));
        c.create_deal(acc("bob.near"));
        as_account("bob.near", zero());
        c.refund(0);
        assert_eq!(c.get_deal(0).unwrap().status, "Refunded");
    }

    #[test]
    #[should_panic(expected = "Only the buyer can release")]
    fn release_by_non_buyer_refused() {
        let mut c = new_escrow();
        as_account("alice.near", near(2));
        c.create_deal(acc("bob.near"));
        as_account("bob.near", zero());
        c.release(0);
    }

    #[test]
    #[should_panic(expected = "Only the seller can refund")]
    fn refund_by_non_seller_refused() {
        let mut c = new_escrow();
        as_account("alice.near", near(2));
        c.create_deal(acc("bob.near"));
        as_account("alice.near", zero());
        c.refund(0);
    }

    #[test]
    #[should_panic(expected = "already Released")]
    fn refund_after_release_refused() {
        let mut c = new_escrow();
        as_account("alice.near", near(2));
        c.create_deal(acc("bob.near"));
        as_account("alice.near", zero());
        c.release(0);
        as_account("bob.near", zero());
        c.refund(0);
    }

    #[test]
    #[should_panic(expected = "already Refunded")]
    fn second_refund_refused() {
        let mut c = new_escrow();
        as_account("alice.near", near(2));
        c.create_deal(acc("bob.near"));
        as_account("bob.near", zero());
        c.refund(0);
        c.refund(0);
    }

    /// Writes v1 state through the v1 layout, then migrates: counts, fields and statuses
    /// survive, `created_at_ms` is 0, and no v1 record stays under the old prefix.
    #[test]
    fn migrate_rewrites_v1_records() {
        as_account("owner.near", zero());
        {
            let mut v1 = EscrowV1 {
                owner: acc("owner.near"),
                deals: IterableMap::new(DEALS_PREFIX_V1),
                next_id: 0,
            };
            for (buyer, seller, amount, released) in [
                ("alice.near", "bob.near", 2u128, false),
                ("alice.near", "carol.near", 3, true),
                ("bob.near", "carol.near", 1, false),
            ] {
                let id = v1.next_id;
                v1.next_id += 1;
                v1.deals.insert(
                    id,
                    DealV1 { buyer: acc(buyer), seller: acc(seller), amount: near(amount), released },
                );
            }
            env::state_write(&v1);
        }
        as_contract();
        let c = Escrow::migrate();
        assert_eq!(c.deal_count(), 3);
        assert_eq!(c.get_owner(), acc("owner.near"));
        let d1 = c.get_deal(1).unwrap();
        assert_eq!((d1.buyer, d1.seller, d1.amount), (acc("alice.near"), acc("carol.near"), near(3)));
        assert_eq!(d1.status, "Released");
        assert_eq!(d1.created_at_ms, 0);
        assert_eq!(c.get_deal(0).unwrap().status, "Funded");
        assert_eq!(c.get_deal(2).unwrap().status, "Funded");
        assert!(c.get_deal(3).is_none());
        // The v1 map was cleared: nothing under its prefix answers any more.
        let leftover: IterableMap<u64, DealV1> = IterableMap::new(DEALS_PREFIX_V1);
        assert!(leftover.get(&0).is_none());
        // And the migrated contract keeps working: the next id continues from v1.
        let mut c = c;
        as_account("bob.near", near(5));
        assert_eq!(c.create_deal(acc("alice.near")), 3);
        assert_eq!(c.deal_count(), 4);
    }
}
