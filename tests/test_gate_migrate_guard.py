"""An upgrade proof with a migration call also needs the migration method to be protected
(``#[private]`` or a ``predecessor_account_id()`` check) in the candidate crate's sources.

Same stubs as ``test_gate_upgrade.py``: no test touches the network.
"""

import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from bumm_guard.gate import REPORT_DIR, REPORT_NAME, migration_guard

try:  # `unittest discover -s tests` imports the sibling as a top-level module; `tests.x` needs the package form
    from test_gate_upgrade import FIXTURES, NEW, OLD, Node, report, run_cli, upgrade_scenario
except ModuleNotFoundError:
    from tests.test_gate_upgrade import FIXTURES, NEW, OLD, Node, report, run_cli, upgrade_scenario

CARGO = """[package]
name = "cand"
version = "0.1.0"
edition = "2021"
[lib]
crate-type = ["cdylib","rlib"]
[dependencies]
near-sdk = "=5.17.2"
"""

HEAD = """use near_sdk::{env, near, require, AccountId};

#[near(contract_state)]
pub struct C { owner: AccountId, n: u32 }

impl Default for C { fn default() -> Self { Self { owner: env::current_account_id(), n: 0 } } }

#[near]
impl C {
    pub fn get_n(&self) -> u32 { self.n }
"""

PRIVATE = """    #[private]
    #[init(ignore_state)]
    pub fn migrate() -> Self {
        let old: C = env::state_read().unwrap_or_else(|| env::panic_str("no state"));
        Self { owner: old.owner, n: old.n + 1 }
    }
"""

PRIVATE_AFTER_INIT = PRIVATE.replace("    #[private]\n    #[init(ignore_state)]\n", "    #[init(ignore_state)]\n    #[private]\n")

OWNER_CHECK = """    #[init(ignore_state)]
    pub fn migrate() -> Self {
        let old: C = env::state_read().unwrap_or_else(|| env::panic_str("no state"));
        require!(env::predecessor_account_id() == old.owner, "only owner");
        Self { owner: old.owner, n: old.n + 1 }
    }
"""

ASSERT_CHECK = OWNER_CHECK.replace('require!(env::predecessor_account_id() == old.owner, "only owner");',
                                   'assert_eq!(env::predecessor_account_id(), old.owner, "only owner");')

IF_PANIC_CHECK = OWNER_CHECK.replace('require!(env::predecessor_account_id() == old.owner, "only owner");',
                                     'if env::predecessor_account_id() != old.owner { env::panic_str("only owner"); }')

SIGNER_CHECK = OWNER_CHECK.replace("predecessor_account_id", "signer_account_id")

GUARD_LINE = 'require!(env::predecessor_account_id() == old.owner, "only owner");'
# recognised: the comparison is followed by a rejection in the if/match body (or an else branch)
IF_RETURN = OWNER_CHECK.replace(GUARD_LINE, 'if env::predecessor_account_id() != old.owner { return Self { owner: old.owner, n: 0 }; }')
IF_UNREACHABLE = OWNER_CHECK.replace(GUARD_LINE, 'if env::predecessor_account_id() != old.owner { unreachable!("only owner") }')
IF_ELSE_PANIC = OWNER_CHECK.replace(GUARD_LINE, 'if env::predecessor_account_id() == old.owner { let _ok = 1; } else { panic!("only owner"); }')
IF_ELSE_IF_REQUIRE = OWNER_CHECK.replace(GUARD_LINE, 'if env::predecessor_account_id() == old.owner { let _ok = 1; }\n'
                                         '        else if old.n > 0 { require!(false, "no"); }')
MATCH_PANIC = OWNER_CHECK.replace(GUARD_LINE, 'match env::predecessor_account_id() {\n'
                                  '            o if o == old.owner => {}\n'
                                  '            _ => env::panic_str("only owner"),\n        }')
IF_PANIC_MULTILINE = OWNER_CHECK.replace(GUARD_LINE, 'if env::predecessor_account_id() != old.owner {\n'
                                         '            env::log_str("refused");\n            env::panic_str("only owner");\n        }')
REQUIRE_NE = OWNER_CHECK.replace(GUARD_LINE, 'require_ne!(env::predecessor_account_id(), old.owner, "x");')
# unknown: a comparison was seen but nothing rejects, or it is a tautology
IF_NO_REJECT = OWNER_CHECK.replace(GUARD_LINE, 'if env::predecessor_account_id() == old.owner { env::log_str("owner"); }')
IF_EMPTY = OWNER_CHECK.replace(GUARD_LINE, 'if env::predecessor_account_id() == old.owner { }')
MATCH_NO_REJECT = OWNER_CHECK.replace(GUARD_LINE, 'match env::predecessor_account_id() { _ => env::log_str("hi") }')
TAUT_REQUIRE = OWNER_CHECK.replace(GUARD_LINE, 'require!(env::predecessor_account_id() == env::predecessor_account_id(), "x");')
TAUT_ASSERT_EQ = OWNER_CHECK.replace(GUARD_LINE, 'assert_eq!(env::predecessor_account_id(), env::predecessor_account_id());')
TAUT_IF_PANIC = OWNER_CHECK.replace(GUARD_LINE, 'if env::predecessor_account_id() != env::predecessor_account_id() { panic!("never"); }')
TAUT_SPACING = OWNER_CHECK.replace(GUARD_LINE, 'require!(env::predecessor_account_id()==env :: predecessor_account_id ( ), "x");')
# an unknown comparison followed by a recognised one: recognised wins
UNKNOWN_THEN_GUARD = OWNER_CHECK.replace(GUARD_LINE, 'if env::predecessor_account_id() == old.owner { env::log_str("o"); }\n        ' + GUARD_LINE)
# the predecessor only in a let: no guard position at all
LET_ONLY = OWNER_CHECK.replace(GUARD_LINE, 'let _caller = env::predecessor_account_id();')

UNPROTECTED = """    #[init(ignore_state)]
    pub fn migrate() -> Self {
        let old: C = env::state_read().unwrap_or_else(|| env::panic_str("no state"));
        Self { owner: old.owner, n: old.n + 1 }
    }
"""

TAIL = "}\n"

TESTS = """
#[cfg(test)]
mod tests {
    use super::*;
    impl C {
        pub fn migrate() -> Self { Self::default() }
    }
}
"""


def crate(root: Path, *parts: str, name: str = "cand") -> Path:
    d = root / name
    (d / "src").mkdir(parents=True)
    (d / "Cargo.toml").write_text(CARGO)
    (d / "src" / "lib.rs").write_text(HEAD + "".join(parts) + TAIL)
    return d


class MigrationGuardFunctionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_private(self):
        self.assertEqual(migration_guard(crate(self.root, PRIVATE), "migrate"), ("private", {"file": "src/lib.rs", "line": 13}))

    def test_private_after_init_attribute(self):
        self.assertEqual(migration_guard(crate(self.root, PRIVATE_AFTER_INIT), "migrate")[0], "private")

    def test_owner_check_forms(self):
        for i, body in enumerate((OWNER_CHECK, ASSERT_CHECK, IF_PANIC_CHECK)):
            with self.subTest(body=body):
                st, detail = migration_guard(crate(self.root, body, name=f"owner{i}"), "migrate")
                self.assertEqual(st, "owner-check")
                self.assertEqual(detail, {"file": "src/lib.rs", "line": 12})

    def test_signer_check_does_not_count(self):
        self.assertEqual(migration_guard(crate(self.root, SIGNER_CHECK), "migrate")[0], "unprotected")

    def test_recognised_rejection_branches(self):
        for i, body in enumerate((IF_RETURN, IF_UNREACHABLE, IF_ELSE_PANIC, IF_ELSE_IF_REQUIRE, MATCH_PANIC,
                                  IF_PANIC_MULTILINE, REQUIRE_NE, UNKNOWN_THEN_GUARD)):
            with self.subTest(body=body):
                st, detail = migration_guard(crate(self.root, body, name=f"rec{i}"), "migrate")
                self.assertEqual(st, "owner-check")
                self.assertEqual(detail, {"file": "src/lib.rs", "line": 12})

    def test_comparison_without_rejection_or_tautology_is_unknown(self):
        for i, body in enumerate((IF_NO_REJECT, IF_EMPTY, MATCH_NO_REJECT, TAUT_REQUIRE, TAUT_ASSERT_EQ,
                                  TAUT_IF_PANIC, TAUT_SPACING)):
            with self.subTest(body=body):
                st, detail = migration_guard(crate(self.root, body, name=f"unk{i}"), "migrate")
                self.assertEqual(st, "owner-check-unknown")
                self.assertEqual(detail, {"file": "src/lib.rs", "line": 12})

    def test_predecessor_outside_a_guard_is_unprotected(self):
        self.assertEqual(migration_guard(crate(self.root, LET_ONLY), "migrate")[0], "unprotected")

    def test_unknown_comparison_deep_in_the_body_does_not_count(self):
        late = UNPROTECTED.replace("        Self { owner", "        " + "let _x = 1;\n        " * 16
                                   + "if env::predecessor_account_id() == old.owner { }\n        Self { owner")
        self.assertEqual(migration_guard(crate(self.root, late), "migrate")[0], "unprotected")

    def test_unprotected(self):
        self.assertEqual(migration_guard(crate(self.root, UNPROTECTED), "migrate"),
                         ("unprotected", {"file": "src/lib.rs", "line": 12}))

    def test_predecessor_check_deep_in_the_body_does_not_count(self):
        late = UNPROTECTED.replace("        Self { owner", "        " + "let _x = 1;\n        " * 16
                                   + "require!(env::predecessor_account_id() == old.owner);\n        Self { owner")
        self.assertEqual(migration_guard(crate(self.root, late), "migrate")[0], "unprotected")

    def test_not_found(self):
        self.assertEqual(migration_guard(crate(self.root, PRIVATE), "migrate_v2"), ("not-found", None))
        self.assertEqual(migration_guard(crate(self.root, "", name="empty"), "migrate"), ("not-found", None))

    def test_cfg_test_copy_is_ignored(self):
        d = crate(self.root, PRIVATE)
        (d / "src" / "lib.rs").write_text((d / "src" / "lib.rs").read_text() + TESTS)
        self.assertEqual(migration_guard(d, "migrate")[0], "private")
        d2 = crate(self.root, "", name="only_test")  # the only definition is in a #[cfg(test)] mod
        (d2 / "src" / "lib.rs").write_text((d2 / "src" / "lib.rs").read_text() + TESTS)
        self.assertEqual(migration_guard(d2, "migrate"), ("not-found", None))

    def test_target_dir_is_skipped(self):
        d = crate(self.root, PRIVATE)
        (d / "target" / "x").mkdir(parents=True)
        (d / "target" / "x" / "lib.rs").write_text(HEAD + UNPROTECTED + TAIL)
        self.assertEqual(migration_guard(d, "migrate")[0], "private")

    def test_ambiguous(self):
        d = crate(self.root, PRIVATE)
        (d / "src" / "other.rs").write_text(HEAD + UNPROTECTED + TAIL)
        st, detail = migration_guard(d, "migrate")
        self.assertEqual(st, "ambiguous")
        self.assertEqual(detail, {"definitions": [{"file": "src/lib.rs", "line": 13, "status": "private"},
                                                  {"file": "src/other.rs", "line": 12, "status": "unprotected"}]})

    def test_comment_and_string_do_not_fool_it(self):
        src = UNPROTECTED.replace("        let old", '        // require!(env::predecessor_account_id() == self.owner);\n'
                                  '        let _s = "env::predecessor_account_id() == self.owner";\n        let old')
        self.assertEqual(migration_guard(crate(self.root, src), "migrate")[0], "unprotected")

    def test_real_fixtures_are_private(self):
        self.assertEqual(migration_guard(FIXTURES / "escrow_v2", "migrate"), ("private", {"file": "src/lib.rs", "line": 118}))
        self.assertEqual(migration_guard(FIXTURES / "event_registration_waitlist", "migrate"),
                         ("private", {"file": "src/lib.rs", "line": 55}))
        self.assertEqual(migration_guard(FIXTURES / "escrow_v1", "migrate"), ("not-found", None))


class MigrationGuardGateTests(unittest.TestCase):
    """The gate on an upgrade proof with a migration call, candidate crate written per test."""

    BLOCK = ("DEPLOY BLOCKED: migration method migrate in src/lib.rs:12 is callable by anyone: it has no #[private] "
             "and no predecessor check; after the upgrade any account could call it and rewrite the state")
    FIX = ("  fix: add `#[private]` above `#[init(ignore_state)]`, or "
           "`require!(env::predecessor_account_id() == self.owner, \"only owner\")` as the first line of the body")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.wasm = self.root / "new.wasm"
        self.wasm.write_bytes(NEW)

    def gate(self, crate_dir: Path, rep=None, *extra):
        p = self.root / "sim.json"
        p.write_text(json.dumps(rep if rep is not None else report(upgrade_scenario())))
        args = ["gate", str(crate_dir), "--wasm", str(self.wasm), "--account", "demo.testnet",
                "--simulate-report", str(p), *extra]
        with mock.patch("urllib.request.urlopen", Node(code=OLD)):
            return run_cli(*args)

    def gate_report(self, crate_dir: Path):
        return json.loads((crate_dir / REPORT_DIR / REPORT_NAME).read_text())

    def test_private_passes_and_is_recorded(self):
        d = crate(self.root, PRIVATE)
        code, out, _ = self.gate(d)
        self.assertEqual(code, 0, out)
        self.assertIn("migration migrate: #[private] (src/lib.rs:13)", out)
        self.assertIn("with-init-call migrate", out)
        self.assertEqual(self.gate_report(d)["migration_guard"],
                         {"method": "migrate", "status": "private", "file": "src/lib.rs", "line": 13})

    def test_owner_check_passes(self):
        d = crate(self.root, OWNER_CHECK)
        code, out, _ = self.gate(d)
        self.assertEqual(code, 0, out)
        self.assertIn("migration migrate: owner check at src/lib.rs:12", out)
        self.assertEqual(self.gate_report(d)["migration_guard"]["status"], "owner-check")

    def test_unprotected_blocks(self):
        d = crate(self.root, UNPROTECTED)
        stale = d / REPORT_DIR / REPORT_NAME
        stale.parent.mkdir()
        stale.write_text("{}")
        code, out, _ = self.gate(d)
        self.assertEqual(code, 3, out)
        self.assertIn(self.BLOCK, out)
        self.assertIn(self.FIX, out)
        self.assertIn("proved by passing scenario", out)  # the proof itself was accepted first
        self.assertNotIn("near contract deploy", out)
        self.assertFalse(stale.exists())

    def test_unprotected_blocks_in_dry_run_too(self):
        d = crate(self.root, UNPROTECTED)
        code, out, _ = self.gate(d, None, "--dry-run")
        self.assertEqual(code, 3, out)
        self.assertIn(self.BLOCK, out)

    UNKNOWN_BLOCK = ("DEPLOY BLOCKED: migration method migrate in src/lib.rs:12 compares predecessor_account_id() but "
                     "no rejection branch was recognised (no panic!/env::panic_str/require!/assert!/return in the "
                     "if/match body, or the comparison is a tautology); the gate cannot tell that other callers are refused")

    def test_owner_check_unknown_blocks_and_suggests_private(self):
        d = crate(self.root, IF_NO_REJECT)
        stale = d / REPORT_DIR / REPORT_NAME
        stale.parent.mkdir()
        stale.write_text("{}")
        code, out, _ = self.gate(d)
        self.assertEqual(code, 3, out)
        self.assertIn(self.UNKNOWN_BLOCK, out)
        self.assertIn("  fix: add `#[private]` above `#[init(ignore_state)]` (recommended)", out)
        self.assertIn("proved by passing scenario", out)
        self.assertNotIn("near contract deploy", out)
        self.assertNotIn("callable by anyone", out)
        self.assertFalse(stale.exists())

    def test_tautology_blocks_in_dry_run(self):
        d = crate(self.root, TAUT_REQUIRE)
        code, out, _ = self.gate(d, None, "--dry-run")
        self.assertEqual(code, 3, out)
        self.assertIn(self.UNKNOWN_BLOCK, out)

    def test_recognised_branch_forms_pass(self):
        for i, body in enumerate((IF_RETURN, MATCH_PANIC, IF_ELSE_PANIC)):
            with self.subTest(body=body):
                d = crate(self.root, body, name=f"pass{i}")
                code, out, _ = self.gate(d)
                self.assertEqual(code, 0, out)
                self.assertIn("migration migrate: owner check at src/lib.rs:12", out)
                self.assertEqual(self.gate_report(d)["migration_guard"]["status"], "owner-check")

    def test_mcp_gate_deploy_blocks_unknown_owner_check(self):
        from bumm_guard import mcp_server
        d = crate(self.root, IF_EMPTY)
        p = self.root / "sim.json"
        p.write_text(json.dumps(report(upgrade_scenario())))
        with mock.patch.dict("os.environ", {"BUMM_GUARD_ALLOW_ANY_PATH": "1"}), \
                mock.patch("urllib.request.urlopen", Node(code=OLD)):
            data = mcp_server.gate_deploy(crate_dir=str(d), wasm=str(self.wasm), account="demo.testnet",
                                          simulate_report=str(p))
        self.assertTrue(data["blocked"])
        self.assertIsNone(data["deploy_command"])
        self.assertIn(self.UNKNOWN_BLOCK, data["messages"])
        self.assertIn("without a recognised owner check", data["note"])

    def test_ambiguous_with_unknown_definition(self):
        d = crate(self.root, PRIVATE)
        (d / "src" / "other.rs").write_text(HEAD + IF_NO_REJECT + TAIL)
        code, out, _ = self.gate(d)
        self.assertEqual(code, 3, out)
        self.assertIn("src/lib.rs:13 (private), src/other.rs:12 (owner-check-unknown)", out)

    def test_ambiguous_blocks_with_every_definition(self):
        d = crate(self.root, PRIVATE)
        (d / "src" / "other.rs").write_text(HEAD + UNPROTECTED + TAIL)
        code, out, _ = self.gate(d)
        self.assertEqual(code, 3, out)
        self.assertIn("DEPLOY BLOCKED: migration method migrate is defined several times with different access "
                      "control: src/lib.rs:13 (private), src/other.rs:12 (unprotected); make every definition "
                      "#[private]", out)
        self.assertNotIn("near contract deploy", out)

    def test_not_found_blocks(self):
        d = crate(self.root, PRIVATE)
        m = {"method": "migrate_v2", "args": {}, "gas": "100 Tgas"}
        stale = d / REPORT_DIR / REPORT_NAME
        stale.parent.mkdir()
        stale.write_text("{}")
        code, out, _ = self.gate(d, report(upgrade_scenario(migrate=m)))
        self.assertEqual(code, 3, out)
        self.assertIn(f"DEPLOY BLOCKED: migration method migrate_v2 not found in {d} sources: its access control "
                      "cannot be checked", out)
        self.assertNotIn("warning: migration method", out)
        self.assertNotIn("near contract deploy", out)
        self.assertFalse(stale.exists())

    def test_code_only_proof_skips_the_check(self):
        d = crate(self.root, UNPROTECTED)  # unprotected, but no migration call is made
        with mock.patch("bumm_guard.gate.migration_guard", side_effect=AssertionError("guard ran")):
            code, out, _ = self.gate(d, report(upgrade_scenario(name="code only", migrate=None)))
        self.assertEqual(code, 0, out)
        self.assertIn("without-init-call", out)
        self.assertIsNone(self.gate_report(d)["migration_guard"])

    def test_fresh_deploy_skips_the_check(self):
        d = crate(self.root, UNPROTECTED)
        with mock.patch("bumm_guard.gate.migration_guard", side_effect=AssertionError("guard ran")), \
                mock.patch("urllib.request.urlopen", Node(code=None)):
            code, out, _ = run_cli("gate", str(d), "--wasm", str(self.wasm), "--account", "demo.testnet")
        self.assertEqual(code, 0, out)
        self.assertIsNone(self.gate_report(d)["migration_guard"])

    def test_mcp_gate_deploy_blocks_unprotected(self):
        from bumm_guard import mcp_server
        d = crate(self.root, UNPROTECTED)
        p = self.root / "sim.json"
        p.write_text(json.dumps(report(upgrade_scenario())))
        with mock.patch.dict("os.environ", {"BUMM_GUARD_ALLOW_ANY_PATH": "1"}), \
                mock.patch("urllib.request.urlopen", Node(code=OLD)):
            data = mcp_server.gate_deploy(crate_dir=str(d), wasm=str(self.wasm), account="demo.testnet",
                                          simulate_report=str(p))
        self.assertTrue(data["blocked"])
        self.assertEqual(data["blocking_findings"], 0)
        self.assertIsNone(data["deploy_command"])
        self.assertIn(self.BLOCK, data["messages"])
        self.assertIn("a migration method callable by anyone", data["note"])
        self.assertEqual(data["upgrade"]["migrate"]["method"], "migrate")


if __name__ == "__main__":
    unittest.main()
