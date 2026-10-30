use near_sdk::json_types::Base64VecU8;
use near_sdk::{env, near, AccountId, Gas, NearToken, Promise};

#[near(contract_state)]
#[derive(Default)]
pub struct Proxy {}

#[near]
impl Proxy {
    /// Plain relay: at the target, predecessor = this contract, signer = original signer.
    pub fn forward(&self, target: AccountId, method: String, args: Base64VecU8) -> Promise {
        Promise::new(target).function_call(
            method, args.into(), NearToken::from_yoctonear(0), Gas::from_tgas(50))
    }
    /// Callback spoof: a successful self-call, then `target.method` scheduled as its "callback".
    pub fn spoof_callback(&self, target: AccountId, method: String, args: Base64VecU8) -> Promise {
        Promise::new(env::current_account_id())
            .function_call("noop".into(), vec![], NearToken::from_yoctonear(0), Gas::from_tgas(5))
            .then(Promise::new(target).function_call(
                method, args.into(), NearToken::from_yoctonear(0), Gas::from_tgas(30)))
    }
    pub fn noop(&self) {}
}
