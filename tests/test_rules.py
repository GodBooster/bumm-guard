"""Rule tests: fixtures plus synthetic edge cases. Run: python3 -m unittest discover -s tests"""

import tempfile
import unittest
from pathlib import Path

from bumm_guard.scanner import scan

FIXTURES = Path(__file__).parent / "fixtures" / "near"

EXPECTED = {
    "d1_vuln": "BG-NEAR-001",
    "d2_vuln": "BG-NEAR-002",
    "d3_vuln": "BG-NEAR-003",
}
CLEAN = ["d1_safe", "d2_safe", "d3_safe", "proxy"]


def scan_source(source: str, name: str = "lib.rs"):
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / name).write_text(source)
        return scan([d]).findings


def contract(methods: str, extra: str = "") -> str:
    return (
        "use near_sdk::{env, near, require, AccountId, Promise, PromiseError};\n"
        "#[near(contract_state)]\n#[derive(Default)]\npub struct C { balance: u128, nonce: u64 }\n"
        + extra
        + "#[near]\nimpl C {\n" + methods + "\n}\n"
    )


def rules_of(findings):
    return sorted((f.rule_id, f.severity) for f in findings)


class FixtureTests(unittest.TestCase):
    def test_vuln_fixtures_yield_exactly_their_finding(self):
        for name, rule in EXPECTED.items():
            with self.subTest(name):
                fs = scan([str(FIXTURES / name)]).findings
                self.assertEqual([f.rule_id for f in fs], [rule])
                self.assertEqual(fs[0].severity, "high")
                self.assertTrue(fs[0].file.endswith("src/lib.rs"))
                self.assertGreater(fs[0].line, 0)

    def test_vuln_fixture_lines(self):
        lines = {n: scan([str(FIXTURES / n)]).findings[0].line for n in EXPECTED}
        self.assertEqual(lines, {"d1_vuln": 23, "d2_vuln": 33, "d3_vuln": 18})

    def test_safe_fixtures_and_proxy_are_clean(self):
        for name in CLEAN:
            with self.subTest(name):
                self.assertEqual(scan([str(FIXTURES / name)]).findings, [])

    def test_whole_fixture_dir_has_three_findings(self):
        res = scan([str(FIXTURES)])
        self.assertEqual(sorted(f.rule_id for f in res.findings), sorted(EXPECTED.values()))
        self.assertEqual(res.files_scanned, 13)  # 7 detector fixtures + event_registration, event_registration_waitlist, escrow_v1, escrow_v2, swarm_jobs, swarm_jobs_v2 (clean)


class NonPrivateCallbackTests(unittest.TestCase):
    CB = (
        "pub fn go(&mut self) -> Promise {\n"
        "  Promise::new(env::current_account_id()).then(Self::ext(env::current_account_id()).with_static_gas(near_sdk::Gas::from_tgas(5)).done());\n}\n"
    )

    def test_flags_public_callback(self):
        fs = scan_source(contract(self.CB + "pub fn done(&mut self) { self.balance += 1; }"))
        self.assertEqual(rules_of(fs), [("BG-NEAR-001", "high")])

    def test_private_attribute_suppresses(self):
        fs = scan_source(contract(self.CB + "#[private]\npub fn done(&mut self) { self.balance += 1; }"))
        self.assertEqual(fs, [])

    def test_path_qualified_private_suppresses(self):
        fs = scan_source(contract(self.CB + "#[near_sdk::private]\npub fn done(&mut self) { self.balance += 1; }"))
        self.assertEqual(fs, [])

    def test_explicit_predecessor_check_suppresses(self):
        body = "pub fn done(&mut self) { require!(env::current_account_id() == env::predecessor_account_id(), \"x\"); self.balance += 1; }"
        self.assertEqual(scan_source(contract(self.CB + body)), [])
        body = "pub fn done(&mut self) { assert_eq!(env::predecessor_account_id(), env::current_account_id()); self.balance += 1; }"
        self.assertEqual(scan_source(contract(self.CB + body)), [])
        body = "pub fn done(&mut self) { near_sdk::assert_self(); self.balance += 1; }"
        self.assertEqual(scan_source(contract(self.CB + body)), [])

    def test_read_only_callback_is_info(self):
        m = "pub fn read(&self, #[callback_unwrap] v: u32) -> u32 { v }"
        self.assertEqual(rules_of(scan_source(contract(m))), [("BG-NEAR-001", "info")])

    def test_promise_result_read_marks_callback(self):
        m = "pub fn x(&mut self) { match env::promise_result_checked(0, 100) { Ok(_) => self.balance = 1, Err(_) => {} } }"
        self.assertEqual(rules_of(scan_source(contract(m))), [("BG-NEAR-001", "high")])

    def test_no_receiver_and_init_ignored(self):
        m = "pub fn s(#[callback_unwrap] v: u32) {}\n#[init]\npub fn new() -> Self { Self::default() }"
        self.assertEqual(scan_source(contract(m)), [])

    def test_non_pub_and_non_exported_ignored(self):
        m = "fn done(&mut self, #[callback_unwrap] v: u32) {}\npub(crate) fn d2(&mut self, #[callback_unwrap] v: u32) {}"
        self.assertEqual(scan_source(contract(m)), [])
        plain = (
            "pub struct S; impl S { pub fn done(&mut self, #[callback_unwrap] v: u32) {} }"
        )
        self.assertEqual(scan_source(plain), [])

    def test_cfg_test_ignored(self):
        src = contract("") + "#[cfg(test)]\nmod tests {\n#[near]\nimpl super::C { pub fn done(&mut self, #[callback_unwrap] v: u32) {} }\n}\n"
        self.assertEqual(scan_source(src), [])

    def test_foreign_ext_does_not_mark_local_method(self):
        src = (
            "use near_sdk::*;\n#[ext_contract(ext_ft)] pub trait Ft { fn ft_transfer(&self); }\n"
            "#[near(contract_state)] #[derive(Default)] pub struct C {}\n"
            "#[near] impl C {\n"
            "  pub fn go(&mut self) -> Promise { Promise::new(env::current_account_id()).then(ext_ft::ext(env::current_account_id()).ft_transfer()) }\n"
            "  pub fn ft_transfer(&mut self) { }\n}\n"
        )
        self.assertEqual(scan_source(src), [])

    def test_braces_in_strings_and_comments_do_not_break_parsing(self):
        m = (
            'pub fn log_it(&self) { env::log_str("}}} { fn done(&mut self)"); /* } */ }\n'
            "// pub fn fake(&mut self, #[callback_unwrap] v: u32) {}\n"
            "pub fn done(&mut self, #[callback_unwrap] v: u32) { self.balance = v as u128; }"
        )
        fs = scan_source(contract(m))
        self.assertEqual([(f.rule_id, f.line) for f in fs], [("BG-NEAR-001", 9)])

    def test_trait_impl_methods_are_public(self):
        src = (
            "use near_sdk::*;\npub trait T { fn done(&mut self, v: u32); }\n"
            "#[near(contract_state)] #[derive(Default)] pub struct C {}\n"
            "#[near] impl T for C { fn done(&mut self, #[callback_unwrap] v: u32) {} }\n"
        )
        self.assertEqual(rules_of(scan_source(src)), [("BG-NEAR-001", "high")])


class CallbackIgnoresFailureTests(unittest.TestCase):
    def go(self, pre: str, call_args: str = "amt") -> str:
        return (
            "pub fn go(&mut self, amt: u128) -> Promise {\n"
            f"  {pre}\n"
            f"  Promise::new(env::predecessor_account_id()).then(Self::ext(env::current_account_id()).cb({call_args}))\n}}\n"
        )

    def test_unused_result_binding(self):
        cb = "#[private]\npub fn cb(&mut self, amt: u128, #[callback_result] _r: Result<(), PromiseError>) {}"
        fs = scan_source(contract(self.go("self.balance -= amt;") + cb))
        self.assertEqual(rules_of(fs), [("BG-NEAR-002", "high")])

    def test_handled_result_is_clean(self):
        cb = "#[private]\npub fn cb(&mut self, amt: u128, #[callback_result] r: Result<(), PromiseError>) { if r.is_err() { self.balance += amt; } }"
        self.assertEqual(scan_source(contract(self.go("self.balance -= amt;") + cb)), [])

    def test_promise_result_read_counts_as_handling(self):
        cb = "#[private]\npub fn cb(&mut self, amt: u128) { if !near_sdk::is_promise_success() { self.balance += amt; } }"
        self.assertEqual(scan_source(contract(self.go("self.balance -= amt;") + cb)), [])

    def test_callback_unwrap_is_medium(self):
        cb = "#[private]\npub fn cb(&mut self, amt: u128, #[callback_unwrap] v: u32) {}"
        fs = scan_source(contract(self.go("self.balance -= amt;") + cb))
        self.assertEqual(rules_of(fs), [("BG-NEAR-002", "medium")])

    def test_pending_marker_is_info(self):
        cb = "#[private]\npub fn cb(&mut self, amt: u128) {}"
        fs = scan_source(contract(self.go("self.nonce += 1;") + cb))
        self.assertEqual(rules_of(fs), [("BG-NEAR-002", "info")])

    def test_custom_rollback_helper_is_excluded(self):
        cb = "#[private]\npub fn cb(&mut self, amt: u128) { self.refund(amt); }\nfn refund(&mut self, a: u128) { self.balance += a; }"
        self.assertEqual(scan_source(contract(self.go("self.balance -= amt;") + cb)), [])

    def test_no_state_change_before_then(self):
        cb = "#[private]\npub fn cb(&mut self, amt: u128) {}"
        self.assertEqual(scan_source(contract(self.go("let _x = amt;") + cb)), [])

    def test_state_change_after_then_not_counted(self):
        m = (
            "pub fn go(&mut self, amt: u128) -> Promise {\n"
            "  let p = Promise::new(env::predecessor_account_id()).then(Self::ext(env::current_account_id()).cb(amt));\n"
            "  self.balance -= amt;\n  p\n}\n"
            "#[private]\npub fn cb(&mut self, amt: u128) {}"
        )
        self.assertEqual(scan_source(contract(m)), [])

    def test_ref_self_initiator_excluded(self):
        m = (
            "pub fn go(&self, amt: u128) -> Promise {\n  self.balance = 1;\n"
            "  Promise::new(env::predecessor_account_id()).then(Self::ext(env::current_account_id()).cb(amt))\n}\n"
            "#[private]\npub fn cb(&mut self, amt: u128) {}"
        )
        self.assertEqual(scan_source(contract(m)), [])


class SignerAuthTests(unittest.TestCase):
    def one(self, body: str):
        return scan_source(contract(f"pub fn m(&mut self) {{ {body} }}"))

    def test_guard_forms_are_high(self):
        for body in (
            'require!(env::signer_account_id() == env::current_account_id(), "x");',
            'assert_eq!(near_sdk::env::signer_account_id(), env::current_account_id());',
            'if env::signer_account_id() != env::current_account_id() { env::panic_str("no"); }',
            'let ok = env::current_account_id() == env::signer_account_id(); require!(ok);',
        ):
            with self.subTest(body):
                self.assertEqual(rules_of(self.one(body)), [("BG-NEAR-003", "high")])

    def test_identity_use_is_review(self):
        fs = self.one("let a = env::signer_account_id(); self.balance = a.as_str().len() as u128;")
        self.assertEqual(rules_of(fs), [("BG-NEAR-003", "low")])
        self.assertTrue(fs[0].message.startswith("REVIEW"))

    def test_logging_only_is_ignored(self):
        self.assertEqual(self.one('env::log_str(&format!("by {}", env::signer_account_id()));'), [])
        self.assertEqual(self.one('near_sdk::log!("by {}", env::signer_account_id());'), [])

    def test_builder_method_form_is_ignored(self):
        self.assertEqual(self.one("let _b = Builder::new().signer_account_id(acc);"), [])

    def test_signer_ok_comment_opt_out(self):
        src = contract("// signer-ok: intended\npub fn m(&mut self) { require!(env::signer_account_id() == env::current_account_id()); }")
        self.assertEqual(scan_source(src), [])

    def test_private_fn_and_predecessor_are_clean(self):
        src = contract("fn m(&mut self) { require!(env::signer_account_id() == env::current_account_id()); }")
        self.assertEqual(scan_source(src), [])
        self.assertEqual(self.one("require!(env::predecessor_account_id() == env::current_account_id());"), [])


if __name__ == "__main__":
    unittest.main()
