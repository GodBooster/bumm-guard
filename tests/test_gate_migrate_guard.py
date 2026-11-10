"""An upgrade proof with a migration call also needs the migration method to be protected in the
candidate crate's sources: ``#[private]`` (but not on an ``#[init]`` method of a crate whose near-sdk is before 5.18.0 or not known, which
generate no check there) or a ``predecessor_account_id()`` check in the body. A refused access probe in the report
(a call of the migration by another account that failed, on the same state, with the control call from the
contract account succeeding) outranks a source check that did not recognise the guard.

Same stubs as ``test_gate_upgrade.py``: no test touches the network.
"""

import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from bumm_guard.gate import (REPORT_DIR, REPORT_NAME, migration_guard, near_sdk_version, private_on_init_enforced,
                             probe_refused)

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
near-sdk = "{sdk}"
"""
# The helper crates ask for near-sdk 5.18.0 unless a test says otherwise: there ``#[private]`` is enforced on
# init methods too. The 5.17.2 cases (``private-unenforced``) pass ``sdk="=5.17.2"``; ``sdk=None``: no Cargo.toml.
SDK_ENFORCED = "=5.18.0"
SDK_UNENFORCED = "=5.17.2"

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


def crate(root: Path, *parts: str, name: str = "cand", sdk: str | None = SDK_ENFORCED) -> Path:
    d = root / name
    (d / "src").mkdir(parents=True)
    if sdk is not None:
        (d / "Cargo.toml").write_text(CARGO.format(sdk=sdk))
    (d / "src" / "lib.rs").write_text(HEAD + "".join(parts) + TAIL)
    return d


def write_lock(folder: Path, *versions: str, extra: str = "") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    body = "version = 4\n\n" + "".join(f'[[package]]\nname = "near-sdk"\nversion = "{v}"\n\n' for v in versions)
    (folder / "Cargo.lock").write_text(body + extra)


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

    def test_private_on_init_with_an_old_near_sdk_is_unenforced(self):
        for i, sdk in enumerate(("=5.17.2", "5.17.2", "^5.1", "=5.0.0", "=4.1.1")):
            with self.subTest(sdk=sdk):
                d = crate(self.root, PRIVATE, name=f"old{i}", sdk=sdk)
                self.assertEqual(migration_guard(d, "migrate"), ("private-unenforced", {"file": "src/lib.rs", "line": 13}))
        d = crate(self.root, PRIVATE_AFTER_INIT, name="after", sdk=SDK_UNENFORCED)
        self.assertEqual(migration_guard(d, "migrate")[0], "private-unenforced")

    def test_private_on_init_is_private_when_5_18_0_or_later_is_known(self):
        for name, sdk in (("new", "=5.18.0"), ("newer", "=5.19.1"), ("v6", "6.0.0"), ("short", "5.18"),
                          ("caret", "^5.20.1"), ("range", ">=5.18, <6")):
            with self.subTest(sdk=sdk):
                d = crate(self.root, PRIVATE, name=name, sdk=sdk)
                self.assertEqual(migration_guard(d, "migrate"), ("private", {"file": "src/lib.rs", "line": 13}))

    def test_private_on_init_with_an_unknown_version_fails_closed(self):
        for name, sdk in (("nofile", None), ("star", "*"), ("loose", "5.17.2"), ("major", "5")):
            with self.subTest(sdk=sdk):
                d = crate(self.root, PRIVATE, name=name, sdk=sdk)
                self.assertEqual(migration_guard(d, "migrate"), ("private-unenforced", {"file": "src/lib.rs", "line": 13}))
        d = crate(self.root, PRIVATE, name="ws")
        (d / "Cargo.toml").write_text(CARGO.format(sdk="x").replace('near-sdk = "x"', "near-sdk = { workspace = true }"))
        self.assertEqual(migration_guard(d, "migrate")[0], "private-unenforced")

    def test_private_on_init_follows_the_cargo_lock(self):
        d = crate(self.root, PRIVATE, name="locked_old", sdk="5.20")      # the lock says 5.17.2: old wins over the requirement
        write_lock(d, "5.17.2")
        self.assertEqual(migration_guard(d, "migrate")[0], "private-unenforced")
        d = crate(self.root, PRIVATE, name="locked_new", sdk=None)
        write_lock(d, "5.18.0")
        self.assertEqual(migration_guard(d, "migrate")[0], "private")

    def test_unknown_version_with_an_owner_check_in_the_body_is_an_owner_check(self):
        check = 'require!(env::predecessor_account_id() == env::current_account_id(), "x");'
        both = PRIVATE.replace("{\n", "{\n        " + check + "\n", 1)
        for i, sdk in enumerate((None, "=5.17.2")):
            self.assertEqual(migration_guard(crate(self.root, both, name=f"oc{i}", sdk=sdk), "migrate")[0], "owner-check")

    def test_explicit_caller_check_with_an_old_near_sdk_is_an_owner_check(self):
        check = 'require!(env::predecessor_account_id() == env::current_account_id(), "only the contract itself may migrate");'
        both = PRIVATE.replace("{\n", "{\n        " + check + "\n", 1)
        self.assertIn(check, both)
        self.assertEqual(migration_guard(crate(self.root, both, sdk=SDK_UNENFORCED), "migrate"),
                         ("owner-check", {"file": "src/lib.rs", "line": 13}))

    def test_an_unrecognised_comparison_next_to_private_on_init_stays_unenforced(self):
        body = PRIVATE.replace("{\n", "{\n        if env::predecessor_account_id() == old_owner() { }\n", 1)
        self.assertEqual(migration_guard(crate(self.root, body, sdk=SDK_UNENFORCED), "migrate")[0],
                         "private-unenforced")

    def test_private_on_a_method_that_is_not_init_is_private_on_any_version(self):
        plain = PRIVATE.replace("    #[init(ignore_state)]\n", "")
        self.assertNotIn("#[init", plain)
        d = crate(self.root, plain, sdk=SDK_UNENFORCED)
        self.assertEqual(migration_guard(d, "migrate")[0], "private")

    def test_private_unenforced_in_one_definition_next_to_private_is_ambiguous(self):
        d = crate(self.root, PRIVATE, sdk=SDK_UNENFORCED)
        self.assertEqual(migration_guard(d, "migrate")[0], "private-unenforced")
        (d / "src" / "other.rs").write_text(HEAD + OWNER_CHECK + TAIL)
        st, detail = migration_guard(d, "migrate")
        self.assertEqual(st, "ambiguous")
        self.assertEqual([x["status"] for x in detail["definitions"]], ["private-unenforced", "owner-check"])

    def test_real_fixtures(self):
        # near-sdk "=5.17.2" in their Cargo.toml: #[private] on #[init] is not enforced
        for name, line in (("escrow_v2", 118), ("swarm_jobs_v2", None), ("event_registration_waitlist", 55)):
            with self.subTest(fixture=name):
                st, detail = migration_guard(FIXTURES / name, "migrate")
                self.assertEqual(st, "private-unenforced")
                self.assertEqual(detail["file"], "src/lib.rs")
                if line:
                    self.assertEqual(detail["line"], line)
        # the same source plus the explicit check as the first line of migrate
        st, detail = migration_guard(FIXTURES / "escrow_v3", "migrate")
        self.assertEqual(st, "owner-check")
        self.assertEqual(detail["file"], "src/lib.rs")
        self.assertEqual(migration_guard(FIXTURES / "escrow_v1", "migrate"), ("not-found", None))


class NearSdkVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.n = 0

    def toml(self, text: str) -> Path:
        """A fresh crate directory whose Cargo.toml is ``text``."""
        self.n += 1
        d = self.root / f"c{self.n}"
        d.mkdir()
        (d / "Cargo.toml").write_text('[package]\nname = "x"\n' + text)
        return d

    def deps(self, dep_line: str | None, extra: str = "") -> Path:
        return self.toml("[dependencies]\n" + (dep_line + "\n" if dep_line else "") + extra)

    def version(self, dep_line, extra=""):
        return near_sdk_version(self.deps(dep_line, extra))

    def enforced(self, dep_line, extra=""):
        return private_on_init_enforced(self.deps(dep_line, extra))

    def test_exact_requirement_is_the_version(self):
        self.assertEqual(self.version('near-sdk = "=5.17.2"'), (5, 17, 2))
        self.assertEqual(self.version('near-sdk="=5.17.2"'), (5, 17, 2))
        self.assertEqual(self.version('near-sdk = "=5.18.0"', 'near-sdk-sim = "4.0.0"\n'), (5, 18, 0))
        self.assertEqual(self.version('near-sdk = { version = "=5.17.2", features = ["non-contract-usage"] }'),
                         (5, 17, 2))
        self.assertEqual(self.version('near-sdk = { default-features = false, version = "=5.18.0" }'), (5, 18, 0))

    def test_inline_table_may_span_lines(self):
        self.assertEqual(self.version('near-sdk = {\n    version = "=5.17.2",\n    features = ["abi"],\n}'), (5, 17, 2))

    def test_dependency_table_form(self):
        d = self.toml('[dependencies.near-sdk]\nversion = "=5.17.2"\nfeatures = ["abi"]\n')
        self.assertEqual(near_sdk_version(d), (5, 17, 2))
        d = self.toml('[dependencies.near-sdk]\nfeatures = ["abi"]\nversion = "=5.18.0"\n[features]\nx = []\n')
        self.assertEqual(near_sdk_version(d), (5, 18, 0))

    def test_non_exact_requirement_is_not_a_version(self):
        for req in ("5.17.2", "5.18.0", "^5.3", "5", "~5.17.2", ">=5.1, <6", "*"):
            with self.subTest(req=req):
                self.assertIsNone(self.version(f'near-sdk = "{req}"'))
        self.assertIsNone(self.version('near-sdk = { version = ">=5.17.2, <5.18" }'))

    def test_dev_and_build_dependencies_never_count(self):
        d = self.toml('[dev-dependencies]\nnear-sdk = { version = "=4.0.0" }\n[dependencies]\nnear-sdk = "=5.18.0"\n')
        self.assertEqual(near_sdk_version(d), (5, 18, 0))
        d = self.toml('[dev-dependencies]\nnear-sdk = "=4.0.0"\n[dependencies]\nnear-sdk = { version = "=5.17.2" }\n'
                      '[build-dependencies]\nnear-sdk = "=3.0.0"\n')
        self.assertEqual(near_sdk_version(d), (5, 17, 2))
        d = self.toml('[dev-dependencies]\nnear-sdk = "=4.0.0"\n[build-dependencies]\nnear-sdk = "=3.0.0"\n')
        self.assertIsNone(near_sdk_version(d))
        self.assertIsNone(private_on_init_enforced(d))
        d = self.toml('[dev-dependencies.near-sdk]\nversion = "=4.0.0"\n[dependencies.near-sdk]\nversion = "=5.17.2"\n')
        self.assertEqual(near_sdk_version(d), (5, 17, 2))
        d = self.toml('[dev-dependencies]\nnear-sdk = "=4.0.0"\n[dependencies]\nnear-sdk = "5.20"\n')
        self.assertIsNone(near_sdk_version(d))
        self.assertIs(private_on_init_enforced(d), True)       # the 5.20 of [dependencies], not the dev 4.0.0

    def test_none_when_unreadable(self):
        self.assertIsNone(near_sdk_version(self.root / "nowhere"))             # no Cargo.toml
        self.assertIsNone(self.version(None))                                 # no near-sdk line
        self.assertIsNone(self.version(None, 'near-sdk-sim = "=4.0.0"\n'))    # another crate with that prefix
        self.assertIsNone(self.version("near-sdk = { workspace = true }"))
        self.assertIsNone(self.version('near-sdk = { git = "https://example.invalid/near-sdk-rs" }'))
        self.assertIsNone(self.version('near-sdk = { path = "../near-sdk" }'))
        self.assertIsNone(self.version('sdk = { package = "near-sdk", version = "=5.17.2" }'))

    def test_cargo_lock_in_the_crate_directory(self):
        d = self.deps('near-sdk = "5"')
        write_lock(d, "5.17.2")
        self.assertEqual(near_sdk_version(d), (5, 17, 2))
        write_lock(d, "5.19.0")
        self.assertEqual(near_sdk_version(d), (5, 19, 0))

    def test_cargo_lock_wins_over_an_exact_requirement(self):
        d = self.deps('near-sdk = "=5.17.2"')
        write_lock(d, "5.18.0")
        self.assertEqual(near_sdk_version(d), (5, 18, 0))

    def test_cargo_lock_two_levels_above(self):
        write_lock(self.root, "5.17.2")
        d = self.root / "ws" / "contracts" / "cand"
        d.mkdir(parents=True)
        (d / "Cargo.toml").write_text('[package]\nname = "x"\n[dependencies]\nnear-sdk = { workspace = true }\n')
        self.assertEqual(near_sdk_version(d), (5, 17, 2))
        self.assertIs(private_on_init_enforced(d), False)

    def test_the_nearest_cargo_lock_is_used(self):
        write_lock(self.root, "5.17.2")
        d = self.root / "ws" / "cand"
        write_lock(d.parent, "5.18.0")
        d.mkdir()
        self.assertEqual(near_sdk_version(d), (5, 18, 0))

    def test_cargo_lock_more_than_five_levels_above_is_not_looked_at(self):
        write_lock(self.root, "5.17.2")
        d = self.root.joinpath(*"abcdef")
        d.mkdir(parents=True)
        self.assertIsNone(near_sdk_version(d))
        d5 = self.root.joinpath(*"vwxyz")          # five levels below the lock: found
        d5.mkdir(parents=True)
        self.assertEqual(near_sdk_version(d5), (5, 17, 2))

    def test_the_lowest_version_if_the_lock_has_several(self):
        d = self.deps('near-sdk = "5"')
        write_lock(d, "5.19.0", "5.17.2", "5.18.1")
        self.assertEqual(near_sdk_version(d), (5, 17, 2))
        self.assertIs(private_on_init_enforced(d), False)

    def test_a_lock_without_near_sdk_falls_back_to_cargo_toml(self):
        d = self.deps('near-sdk = "=5.17.2"')
        (d / "Cargo.lock").write_text('version = 4\n\n[[package]]\nname = "near-sdk-macros"\nversion = "5.19.0"\n')
        self.assertEqual(near_sdk_version(d), (5, 17, 2))
        d = self.deps('near-sdk = "5.18"')
        (d / "Cargo.lock").write_text('version = 4\n\n[[package]]\nname = "serde"\nversion = "1.0.0"\n')
        self.assertIsNone(near_sdk_version(d))
        self.assertIs(private_on_init_enforced(d), True)

    def test_enforced_from_a_known_version(self):
        self.assertIs(self.enforced('near-sdk = "=5.17.2"'), False)
        self.assertIs(self.enforced('near-sdk = "=5.0.0"'), False)
        self.assertIs(self.enforced('near-sdk = "=5.18.0"'), True)
        self.assertIs(self.enforced('near-sdk = "=5.19.1"'), True)
        d = self.deps('near-sdk = "5.20"')
        write_lock(d, "5.17.2")
        self.assertIs(private_on_init_enforced(d), False)       # resolved version beats the requirement
        d = self.deps('near-sdk = "5"')
        write_lock(d, "5.18.0")
        self.assertIs(private_on_init_enforced(d), True)

    def test_enforced_from_the_lower_bound_of_the_requirement(self):
        for req in ("5.18", "^5.18", "^5.20.1", ">=5.18, <6", "~5.18", "5.18.0", "6", "6.0.0"):
            with self.subTest(req=req):
                self.assertIs(self.enforced(f'near-sdk = "{req}"'), True)
        self.assertIs(self.enforced('near-sdk = { version = "^5.20.1", features = ["abi"] }'), True)

    def test_enforced_is_none_when_it_cannot_be_told(self):
        self.assertIsNone(private_on_init_enforced(self.root / "nowhere"))
        for req in ("5.17.2", "5", "^5.1", "~5.17.2", "*", ">=5.1, <6", "5.17"):
            with self.subTest(req=req):
                self.assertIsNone(self.enforced(f'near-sdk = "{req}"'))
        for line in ("near-sdk = { workspace = true }", 'near-sdk = { git = "https://example.invalid/near-sdk-rs" }',
                     'near-sdk = { path = "../near-sdk" }', 'sdk = { package = "near-sdk", version = "5.20" }'):
            with self.subTest(line=line):
                self.assertIsNone(self.enforced(line))
        self.assertIsNone(self.enforced(None))

    def test_the_fixtures(self):
        # the version comes from tests/fixtures/near/Cargo.lock, one level above each fixture
        for name in ("escrow_v2", "escrow_v3", "swarm_jobs_v2", "event_registration_waitlist"):
            with self.subTest(fixture=name):
                self.assertEqual(near_sdk_version(FIXTURES / name), (5, 17, 2))
                self.assertIs(private_on_init_enforced(FIXTURES / name), False)


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

    UNENFORCED_BLOCK = ("DEPLOY BLOCKED: migration method migrate in src/lib.rs:13 has #[private] on an #[init] method, "
                        "but this crate builds with near-sdk 5.17.2: before 5.18.0 near-sdk generates no caller "
                        "check for init methods, so any account can call it")
    UNENFORCED_FIX = ("  fix: make `require!(env::predecessor_account_id() == env::current_account_id(), "
                      "\"only the contract itself may migrate\")` the first line of the body, or build with "
                      "near-sdk 5.18.0 or later")

    def test_private_unenforced_blocks_without_a_probe(self):
        d = crate(self.root, PRIVATE, sdk=SDK_UNENFORCED)
        stale = d / REPORT_DIR / REPORT_NAME
        stale.parent.mkdir()
        stale.write_text("{}")
        code, out, _ = self.gate(d)
        self.assertEqual(code, 3, out)
        self.assertIn(self.UNENFORCED_BLOCK + "; the report has no access probe that says otherwise", out)
        self.assertIn(self.UNENFORCED_FIX, out)
        self.assertIn("proved by passing scenario", out)
        self.assertNotIn("near contract deploy", out)
        self.assertNotIn("migration migrate: #[private]", out)
        self.assertFalse(stale.exists())

    def test_private_unenforced_blocks_in_dry_run_too(self):
        code, out, _ = self.gate(crate(self.root, PRIVATE, sdk=SDK_UNENFORCED), None, "--dry-run")
        self.assertEqual(code, 3, out)
        self.assertIn(self.UNENFORCED_BLOCK, out)

    def test_private_with_near_sdk_5_18_passes(self):
        code, out, _ = self.gate(crate(self.root, PRIVATE, sdk="=5.18.0"))
        self.assertEqual(code, 0, out)

    def test_private_unenforced_with_the_explicit_check_passes_as_an_owner_check(self):
        check = 'require!(env::predecessor_account_id() == env::current_account_id(), "x");'
        d = crate(self.root, PRIVATE.replace("{\n", "{\n        " + check + "\n", 1), sdk=SDK_UNENFORCED)
        code, out, _ = self.gate(d)
        self.assertEqual(code, 0, out)
        self.assertIn("migration migrate: owner check at src/lib.rs:13", out)
        self.assertEqual(self.gate_report(d)["migration_guard"]["status"], "owner-check")

    def test_mcp_gate_deploy_blocks_private_unenforced(self):
        from bumm_guard import mcp_server
        d = crate(self.root, PRIVATE, sdk=SDK_UNENFORCED)
        p = self.root / "sim.json"
        p.write_text(json.dumps(report(upgrade_scenario())))
        with mock.patch.dict("os.environ", {"BUMM_GUARD_ALLOW_ANY_PATH": "1"}), \
                mock.patch("urllib.request.urlopen", Node(code=OLD)):
            data = mcp_server.gate_deploy(crate_dir=str(d), wasm=str(self.wasm), account="demo.testnet",
                                          simulate_report=str(p))
        self.assertTrue(data["blocked"])
        self.assertIsNone(data["deploy_command"])
        self.assertTrue(any(m.startswith(self.UNENFORCED_BLOCK) for m in data["messages"]), data["messages"])

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


class AccessProbeGateTests(unittest.TestCase):
    """The access probe (docs/FORMATS.md section 3: the migration call of another account, sent to the new code
    on the not yet migrated state, and the control call of the contract account) next to the source check of the
    migration method."""

    PRIVATE_ERR = "Method migrate is private"
    STATE = "b" * 64            # the state_before digest that probe_report puts on the upgrade step
    REFUSED = ("access probe: migrate sent by another account to the new code on the not yet migrated state was "
               "refused ({err}); the same call from the contract account went through")
    NOTE = ("note: access probe inconclusive: the call by another account failed, but {why}; the source check decides")
    WHY_STATE = "the probe sandbox's state differed from the scenario run's"
    WHY_CONTROL = "the same call from the contract account did not succeed there either"
    # status failure, but one of the three conditions missing: (id, probe_report kwargs, why)
    INCONCLUSIVE = (("state differs", dict(state_sha256="c" * 64), WHY_STATE),
                    ("state unknown", dict(state_sha256=None), WHY_STATE),
                    ("control failed", dict(control_status="failure", control_error="gas"), WHY_CONTROL),
                    ("control missing", dict(control_status=None), WHY_CONTROL),
                    ("control junk", dict(control_status="maybe"), WHY_CONTROL))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.wasm = self.root / "new.wasm"
        self.wasm.write_bytes(NEW)
        self.n = 0

    gate = MigrationGuardGateTests.gate
    gate_report = MigrationGuardGateTests.gate_report

    def probe_report(self, status="failure", error=PRIVATE_ERR, method="migrate", state_sha256=STATE,
                     control_status="success", control_error=None):
        """A report whose upgrade step has a probe; by default a refused one (failure, same state, control ok).
        ``None`` leaves a field out; ``status=None`` leaves the whole probe out."""
        sc = upgrade_scenario()
        sc["state_after_init"] = {"keys": 1, "bytes": 40, "sha256": "a" * 64}
        sc["steps"][0]["state_before"] = {"keys": 3, "bytes": 120, "sha256": self.STATE}
        if status is not None:
            pr = {"method": method, "by": "p.test.near", "status": status}
            for key, val in (("error", error), ("state_sha256", state_sha256), ("control_status", control_status),
                             ("control_error", control_error)):
                if val is not None:
                    pr[key] = val
            sc["steps"][0]["access_probe"] = pr
        return report(sc)

    def crates(self):
        """One crate per source status that blocks without a refused probe."""
        self.n += 1
        amb = crate(self.root, PRIVATE, name=f"amb{self.n}")
        (amb / "src" / "other.rs").write_text(HEAD + UNPROTECTED + TAIL)
        return {"unprotected": crate(self.root, UNPROTECTED, name=f"unp{self.n}"),
                "owner-check-unknown": crate(self.root, IF_NO_REJECT, name=f"unk{self.n}"),
                "ambiguous": amb,
                "private-unenforced": crate(self.root, PRIVATE, name=f"unf{self.n}", sdk=SDK_UNENFORCED)}

    # rule 1: probe_refused

    def test_probe_refused_needs_all_three_conditions(self):
        ok = {"status": "failure", "same_state": True, "control_status": "success"}
        self.assertTrue(probe_refused(ok))
        self.assertFalse(probe_refused(None))
        for change in ({"status": "success"}, {"status": "not-run"}, {"same_state": False}, {"same_state": None},
                       {"control_status": "failure"}, {"control_status": None}):
            with self.subTest(change=change):
                self.assertFalse(probe_refused(dict(ok, **change)))
        self.assertFalse(probe_refused({"status": "failure", "same_state": True}))   # no control key at all

    # rules 1 and 2: a refused probe

    def test_refused_probe_accepts_what_the_source_check_could_not_recognise(self):
        for status, d in self.crates().items():
            with self.subTest(status=status):
                code, out, _ = self.gate(d, self.probe_report())
                self.assertEqual(code, 0, out)
                self.assertIn(self.REFUSED.format(err=self.PRIVATE_ERR), out.splitlines())
                self.assertNotIn("inconclusive", out)
                lines = [l for l in out.splitlines() if l.startswith("migration migrate: the source check")]
                self.assertEqual(len(lines), 1, out)
                self.assertTrue(lines[0].startswith(f"migration migrate: the source check did not recognise its "
                                                    f"guard ({status}"), lines[0])
                self.assertTrue(lines[0].endswith("; accepted on the access probe"), lines[0])
                self.assertIn("with-init-call migrate", out)
                mg = self.gate_report(d)["migration_guard"]
                self.assertEqual(mg["status"], status)
                self.assertEqual(mg["access_probe"], {"method": "migrate", "by": "p.test.near", "status": "failure",
                                                      "error": self.PRIVATE_ERR, "same_state": True,
                                                      "control_status": "success"})

    def test_a_refused_probe_counts_whatever_its_text(self):
        for err in ("Method migrate is private", "Method is private", "only owner", "Cannot deserialize the contract "
                    "state", "this is privately held", "only the contract itself may migrate", None):
            for status, d in self.crates().items():
                with self.subTest(error=err, status=status):
                    code, out, _ = self.gate(d, self.probe_report(error=err))
                    self.assertEqual(code, 0, out)
                    self.assertIn(self.REFUSED.format(err=err or "no error text"), out.splitlines())
                    self.assertIn("; accepted on the access probe", out)
                    self.assertIn("with-init-call migrate", out)

    def test_no_error_text_is_said_so(self):
        d = crate(self.root, PRIVATE, name="noerr")
        code, out, _ = self.gate(d, self.probe_report(error=None))
        self.assertEqual(code, 0, out)
        self.assertIn(self.REFUSED.format(err="no error text"), out.splitlines())

    def test_refused_line_has_no_state_note_any_more(self):
        d = crate(self.root, PRIVATE, name="plain")
        code, out, _ = self.gate(d, self.probe_report())
        self.assertEqual(code, 0, out)
        self.assertNotIn("differed", out)
        self.assertNotIn("; note:", out)

    # rules 1 and 3: failure that is not refused is inconclusive, the source check decides

    def test_failure_without_one_of_the_three_conditions_is_not_refused(self):
        for tag, kw, why in self.INCONCLUSIVE:
            for status, d in self.crates().items():
                with self.subTest(case=tag, status=status):
                    code, out, _ = self.gate(d, self.probe_report(**kw))
                    self.assertEqual(code, 3, out)
                    self.assertIn(self.NOTE.format(why=why), out.splitlines())
                    self.assertNotIn("access probe: migrate sent by another account", out)
                    self.assertNotIn("accepted on the access probe", out)
                    self.assertNotIn("near contract deploy", out)
                    self.assertNotIn("accepted a call from another account", out)

    def test_inconclusive_probe_lets_the_source_check_pass_what_it_recognises(self):
        for tag, kw, why in self.INCONCLUSIVE:
            for name, body, line in (("priv", PRIVATE, "migration migrate: #[private] (src/lib.rs:13)"),
                                     ("own", OWNER_CHECK, "migration migrate: owner check at src/lib.rs:12")):
                with self.subTest(case=tag, source=name):
                    d = crate(self.root, body, name=f"{name}{self.n}")
                    self.n += 1
                    code, out, _ = self.gate(d, self.probe_report(**kw))
                    self.assertEqual(code, 0, out)
                    self.assertIn(line, out)
                    self.assertIn(self.NOTE.format(why=why), out.splitlines())
                    self.assertNotIn("access probe: migrate sent by another account", out)

    def test_inconclusive_probe_is_recorded_in_the_gate_report(self):
        d = crate(self.root, OWNER_CHECK, name="inconc")
        self.gate(d, self.probe_report(control_status="failure"))
        self.assertEqual(self.gate_report(d)["migration_guard"]["access_probe"],
                         {"method": "migrate", "by": "p.test.near", "status": "failure", "error": self.PRIVATE_ERR,
                          "same_state": True, "control_status": "failure"})

    # rule 4: success and not-run

    def test_probe_success_blocks_whatever_the_source_says(self):
        for status, d in self.crates().items():
            with self.subTest(status=status):
                code, out, _ = self.gate(d, self.probe_report(status="success", error=None))
                self.assertEqual(code, 3, out)
                self.assertIn("accepted a call from another account", out)
                self.assertNotIn("accepted on the access probe", out)
                self.assertNotIn("near contract deploy", out)
        d = crate(self.root, OWNER_CHECK, name="own")      # a recognised source check does not outvote a probe that went through
        code, out, _ = self.gate(d, self.probe_report(status="success", error=None))
        self.assertEqual(code, 3, out)
        self.assertIn("accepted a call from another account", out)

    def test_no_probe_or_not_run_changes_nothing(self):
        for status, d in self.crates().items():
            with self.subTest(status=status):
                for rep in (self.probe_report(status=None), self.probe_report(status="not-run")):
                    code, out, _ = self.gate(d, rep)
                    self.assertEqual(code, 3, out)
                    self.assertNotIn("access probe:", out)
                    self.assertNotIn("inconclusive", out)
                    self.assertNotIn("accepted on the access probe", out)
                    self.assertNotIn("near contract deploy", out)
        d = crate(self.root, UNPROTECTED, name="unp2")
        self.gate(d, self.probe_report(status=None))
        self.assertFalse((d / REPORT_DIR / REPORT_NAME).exists())

    # rule 7: the private-unenforced block message

    def test_private_unenforced_known_version_without_a_probe_has_the_suffix(self):
        d = crate(self.root, PRIVATE, name="unf_none", sdk=SDK_UNENFORCED)
        code, out, _ = self.gate(d, self.probe_report(status=None))
        self.assertEqual(code, 3, out)
        self.assertIn("has #[private] on an #[init] method, but this crate builds with near-sdk 5.17.2: before 5.18.0 "
                      "near-sdk generates no caller check for init methods, so any account can call it; the report "
                      "has no access probe that says otherwise", out)
        self.assertIn("  fix: make `require!(env::predecessor_account_id() == env::current_account_id(), ", out)

    def test_private_unenforced_unknown_version_message(self):
        unknown = ("has #[private] on an #[init] method, but the near-sdk version of this crate cannot be told from "
                   "its Cargo.lock or Cargo.toml: before 5.18.0 near-sdk generates no caller check for init methods, "
                   "so any account can call it")
        for i, sdk in enumerate((None, "5.17.2", "*")):
            with self.subTest(sdk=sdk):
                d = crate(self.root, PRIVATE, name=f"unk_sdk{i}", sdk=sdk)
                code, out, _ = self.gate(d, self.probe_report(status=None))
                self.assertEqual(code, 3, out)
                self.assertIn(unknown + "; the report has no access probe that says otherwise", out)
                self.assertNotIn("this crate builds with near-sdk", out)
                code, out, _ = self.gate(d, self.probe_report(control_status="failure"))
                self.assertEqual(code, 3, out)
                self.assertIn(unknown, out)
                self.assertNotIn("no access probe that says otherwise", out)

    def test_private_unenforced_message_uses_the_locked_version(self):
        d = crate(self.root, PRIVATE, name="unf_lock", sdk="5")
        write_lock(d, "5.12.0")
        code, out, _ = self.gate(d, self.probe_report(status=None))
        self.assertEqual(code, 3, out)
        self.assertIn("but this crate builds with near-sdk 5.12.0: before 5.18.0", out)

    def test_private_unenforced_suffix_only_without_a_failed_probe(self):
        d = crate(self.root, PRIVATE, name="unf_suffix", sdk=SDK_UNENFORCED)
        suffix = "; the report has no access probe that says otherwise"
        for rep in (self.probe_report(status=None), self.probe_report(status="not-run", error="the replay failed")):
            code, out, _ = self.gate(d, rep)
            self.assertEqual(code, 3, out)
            self.assertIn("has #[private] on an #[init] method", out)
            self.assertIn(suffix, out)
            self.assertNotIn("access probe:", out)
            self.assertNotIn("near contract deploy", out)
        for tag, kw, why in self.INCONCLUSIVE:         # an inconclusive probe: no suffix, the note instead
            with self.subTest(case=tag):
                code, out, _ = self.gate(d, self.probe_report(**kw))
                self.assertEqual(code, 3, out)
                self.assertIn("has #[private] on an #[init] method", out)
                self.assertNotIn(suffix, out)
                self.assertIn(self.NOTE.format(why=why), out.splitlines())
        code, out, _ = self.gate(d, self.probe_report(status="success", error=None))   # a probe that succeeded blocks too
        self.assertEqual(code, 3, out)

    def test_private_unenforced_with_a_refused_probe_passes(self):
        for i, sdk in enumerate((SDK_UNENFORCED, None)):
            d = crate(self.root, PRIVATE, name=f"unf_ref{i}", sdk=sdk)
            code, out, _ = self.gate(d, self.probe_report())
            self.assertEqual(code, 0, out)
            self.assertIn("migration migrate: the source check did not recognise its guard (private-unenforced at "
                          "src/lib.rs:13); accepted on the access probe", out)
            self.assertEqual(self.gate_report(d)["migration_guard"]["status"], "private-unenforced")

    def test_not_found_still_blocks_with_a_refused_probe(self):
        d = crate(self.root, PRIVATE, name="nf")
        sc = upgrade_scenario(migrate={"method": "migrate_v2", "args": {}, "gas": "100 Tgas"})
        sc["state_after_init"] = {"keys": 1, "bytes": 40, "sha256": "a" * 64}
        sc["steps"][0]["state_before"] = {"keys": 3, "bytes": 120, "sha256": self.STATE}
        sc["steps"][0]["access_probe"] = {"method": "migrate_v2", "by": "p", "status": "failure",
                                          "error": "Method migrate_v2 is private", "state_sha256": self.STATE,
                                          "control_status": "success"}
        code, out, _ = self.gate(d, report(sc))
        self.assertEqual(code, 3, out)
        self.assertIn("migration method migrate_v2 not found", out)
        self.assertNotIn("near contract deploy", out)

    def test_recognised_guards_still_print_their_own_line_with_a_refused_probe(self):
        for name, body, line in (("priv", PRIVATE, "migration migrate: #[private] (src/lib.rs:13)"),
                                 ("own", OWNER_CHECK, "migration migrate: owner check at src/lib.rs:12")):
            with self.subTest(name=name):
                d = crate(self.root, body, name=name)
                code, out, _ = self.gate(d, self.probe_report())
                self.assertEqual(code, 0, out)
                self.assertIn(line, out)
                self.assertNotIn("accepted on the access probe", out)
                self.assertIn(self.REFUSED.format(err=self.PRIVATE_ERR), out.splitlines())

    def test_same_state_and_control_status_in_the_recorded_probe(self):
        d = crate(self.root, PRIVATE, name="rec")
        for kw, same, control in ((dict(), True, "success"), (dict(state_sha256="c" * 64), False, "success"),
                                  (dict(state_sha256=None), None, "success"),
                                  (dict(control_status="failure"), True, "failure"),
                                  (dict(control_status=None), True, None)):
            with self.subTest(kw=kw):
                code, out, _ = self.gate(d, self.probe_report(**kw))
                self.assertEqual(code, 0, out)      # #[private] with 5.18.0: the source check passes on its own
                pr = self.gate_report(d)["migration_guard"]["access_probe"]
                self.assertIs(pr["same_state"], same)
                self.assertEqual(pr["control_status"], control)
                self.assertNotIn("control_error", pr)

    def test_probe_is_in_the_gate_report_migration_guard(self):
        d = crate(self.root, OWNER_CHECK, name="inrep")
        self.gate(d, self.probe_report())
        self.assertEqual(self.gate_report(d)["migration_guard"],
                         {"method": "migrate", "status": "owner-check", "file": "src/lib.rs", "line": 12,
                          "access_probe": {"method": "migrate", "by": "p.test.near", "status": "failure",
                                           "error": self.PRIVATE_ERR, "same_state": True,
                                           "control_status": "success"}})

    def test_mcp_gate_deploy_accepts_unprotected_with_a_refused_probe(self):
        from bumm_guard import mcp_server
        d = crate(self.root, UNPROTECTED, name="mcp")
        p = self.root / "sim.json"
        p.write_text(json.dumps(self.probe_report()))
        with mock.patch.dict("os.environ", {"BUMM_GUARD_ALLOW_ANY_PATH": "1"}), \
                mock.patch("urllib.request.urlopen", Node(code=OLD)):
            data = mcp_server.gate_deploy(crate_dir=str(d), wasm=str(self.wasm), account="demo.testnet",
                                          simulate_report=str(p))
        self.assertTrue(data["passed"], data["messages"])
        self.assertTrue(any(m.endswith("; accepted on the access probe") for m in data["messages"]))
        self.assertIn(self.REFUSED.format(err=self.PRIVATE_ERR), data["messages"])


if __name__ == "__main__":
    unittest.main()
