"""Tests for `bumm-guard fix`. Run: python3 -m unittest discover -s tests"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from bumm_guard import cli
from bumm_guard.scanner import scan

FIXTURES = Path(__file__).parent / "fixtures" / "near"


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def code_lines(path: Path) -> list[str]:
    """Source lines without blank lines and `//` comments (the *_safe fixtures differ in comments only)."""
    return [l.rstrip() for l in path.read_text().splitlines() if l.strip() and not l.strip().startswith("//")]


def copy_crate(name: str, dest: str) -> Path:
    dst = Path(dest) / name
    shutil.copytree(FIXTURES / name, dst, ignore=shutil.ignore_patterns("target"))
    return dst


class FixDryRunTests(unittest.TestCase):
    def test_d1_diff_is_a_one_line_insert(self):
        code, out, _ = run_cli("fix", str(FIXTURES / "d1_vuln"))
        added = [l for l in out.splitlines() if l.startswith("+") and not l.startswith("+++")]
        removed = [l for l in out.splitlines() if l.startswith("-") and not l.startswith("---")]
        self.assertEqual(added, ["+    #[private]"])
        self.assertEqual(removed, [])
        self.assertEqual(code, 1)   # dry run: the high finding is still there

    def test_dry_run_does_not_touch_files(self):
        before = (FIXTURES / "d1_vuln/src/lib.rs").read_text()
        run_cli("fix", str(FIXTURES / "d1_vuln"))
        self.assertEqual((FIXTURES / "d1_vuln/src/lib.rs").read_text(), before)

    def test_nothing_to_fix_exit_0(self):
        code, out, _ = run_cli("fix", str(FIXTURES / "d1_safe"))
        self.assertEqual(code, 0)
        self.assertIn("nothing to fix", out)

    def test_missing_path_exit_2(self):
        code, _, err = run_cli("fix", str(FIXTURES / "nope"))
        self.assertEqual(code, 2)
        self.assertIn("no such file", err)

    def test_json_shape(self):
        code, out, _ = run_cli("fix", str(FIXTURES / "d3_vuln"), "--format", "json")
        data = json.loads(out)
        self.assertEqual(data["mode"], "dry-run")
        self.assertEqual([f["rule_id"] for f in data["fixes"]], ["BG-NEAR-003"])
        self.assertTrue(data["fixes"][0]["fixable"])
        self.assertFalse(data["fixes"][0]["applied"])
        self.assertEqual(len(data["diffs"]), 1)
        self.assertNotIn("remaining", data)


class FixApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_apply_d1(self):
        crate = copy_crate("d1_vuln", self.tmp.name)
        code, out, _ = run_cli("fix", str(crate), "--apply", "--format", "json")
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(data["remaining"]["findings"], [])
        self.assertTrue(data["fixes"][0]["applied"])
        self.assertEqual(scan([str(crate)]).findings, [])
        src = (crate / "src/lib.rs").read_text()
        lines = src.splitlines()
        i = next(n for n, l in enumerate(lines) if "pub fn on_paid" in l)
        self.assertEqual(lines[i - 1], "    #[private]")
        self.assertEqual(code_lines(crate / "src/lib.rs"), code_lines(FIXTURES / "d1_safe/src/lib.rs"))

    def test_apply_d3(self):
        crate = copy_crate("d3_vuln", self.tmp.name)
        code, _, _ = run_cli("fix", str(crate), "--apply")
        self.assertEqual(code, 0)
        self.assertEqual(scan([str(crate)]).findings, [])
        src = (crate / "src/lib.rs").read_text()
        self.assertIn("require!(env::predecessor_account_id() == self.owner", src)
        self.assertNotIn("signer_account_id", src)
        self.assertEqual(code_lines(crate / "src/lib.rs"), code_lines(FIXTURES / "d3_safe/src/lib.rs"))

    def test_d2_suggestion_only(self):
        crate = copy_crate("d2_vuln", self.tmp.name)
        before = (crate / "src/lib.rs").read_text()
        code, out, _ = run_cli("fix", str(crate), "--apply", "--format", "json")
        data = json.loads(out)
        self.assertEqual(code, 1)   # the high finding remains
        self.assertEqual((crate / "src/lib.rs").read_text(), before)
        (fix,) = data["fixes"]
        self.assertFalse(fix["applied"])
        self.assertEqual(fix["reason"], "needs manual rollback")
        patch = fix["suggested_patch"]
        self.assertIn("#[callback_result] r: Result<(), PromiseError>", patch)
        self.assertIn("if r.is_err()", patch)
        self.assertIn("TODO: revert `self.balances.insert(user.clone(), bal - amount.0)`", patch)
        self.assertEqual([f["rule_id"] for f in data["remaining"]["findings"]], ["BG-NEAR-002"])
        self.assertEqual(data["files_changed"], [])

    def test_apply_is_idempotent(self):
        crate = copy_crate("d1_vuln", self.tmp.name)
        run_cli("fix", str(crate), "--apply")
        once = (crate / "src/lib.rs").read_text()
        code, out, _ = run_cli("fix", str(crate), "--apply")
        self.assertEqual(code, 0)
        self.assertIn("nothing to fix", out)
        self.assertEqual((crate / "src/lib.rs").read_text(), once)

    def test_never_modifies_target_dir(self):
        d = Path(self.tmp.name) / "c" / "target"
        d.mkdir(parents=True)
        f = d / "lib.rs"
        f.write_text((FIXTURES / "d1_vuln/src/lib.rs").read_text())
        before = f.read_text()
        code, out, _ = run_cli("fix", str(f), "--apply")   # explicit file: scanned, but protected
        self.assertEqual(f.read_text(), before)
        self.assertEqual(code, 1)
        self.assertIn("target/", out)

    def test_crlf_preserved(self):
        f = Path(self.tmp.name) / "lib.rs"
        f.write_bytes((FIXTURES / "d1_vuln/src/lib.rs").read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(run_cli("fix", str(f), "--apply")[0], 0)
        data = f.read_bytes()
        self.assertIn(b"    #[private]\r\n    pub fn on_paid", data)
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))


CB = (
    "use near_sdk::{env, near, require, AccountId, Promise, PromiseError};\n"
    "#[near(contract_state)]\n#[derive(Default)]\npub struct C { balance: u128, owner: Option<AccountId> }\n"
    "#[near]\nimpl C {\n"
    "    pub fn go(&mut self) -> Promise {\n"
    "        let amt = 1u128;\n        self.balance -= amt;\n"
    "        Promise::new(env::predecessor_account_id()).then(Self::ext(env::current_account_id()).cb(amt))\n"
    "    }\n"
    "%s\n}\n"
)


def fixed(source: str) -> str:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "lib.rs"
        p.write_text(source)
        run_cli("fix", str(p), "--apply")
        return p.read_text()


class FixerEdgeCases(unittest.TestCase):
    def test_001_after_existing_attributes_and_indent(self):
        out = fixed(CB % "    #[payable]\n    pub fn cb(&mut self, x: u32) { self.balance += x as u128; }")
        self.assertIn("    #[payable]\n    #[private]\n    pub fn cb(", out)

    def test_001_attribute_on_same_line(self):
        out = fixed(CB % "    #[payable] pub fn cb(&mut self, x: u32) { self.balance += x as u128; }")
        self.assertIn("    #[payable] #[private] pub fn cb(", out)

    def test_003_qualified_path_and_only_guard(self):
        src = CB.replace("self.balance -= amt;", "require!(near_sdk::env::signer_account_id() == env::current_account_id(), \"x\");") \
            % "    #[private]\n    pub fn cb(&mut self, x: u32) { env::log_str(&format!(\"{}\", env::signer_account_id())); }"
        out = fixed(src)
        self.assertIn("require!(near_sdk::env::predecessor_account_id() == env::current_account_id()", out)
        self.assertIn('format!("{}", env::signer_account_id())', out)   # logging use untouched

    def test_003_review_finding_not_patched(self):
        src = CB % "    #[private]\n    pub fn cb(&mut self, x: u32) { self.owner = Some(env::signer_account_id()); }"
        self.assertEqual(fixed(src), src)

    def test_002_unused_binding_and_unwrap_variants(self):
        for sig, expect in (
            ("pub fn cb(&mut self, #[callback_result] r: Result<(), PromiseError>, x: u32) { env::log_str(\"d\"); }",
             "`r` is already declared"),
            ("pub fn cb(&mut self, #[callback_unwrap] v: u32) { env::log_str(\"d\"); }",
             "#[callback_result] v: Result<u32, PromiseError>"),
        ):
            with self.subTest(sig):
                with tempfile.TemporaryDirectory() as d:
                    p = Path(d) / "lib.rs"
                    p.write_text(CB % ("    #[private]\n    " + sig))
                    code, out, _ = run_cli("fix", str(p), "--format", "json")
                    fx = [f for f in json.loads(out)["fixes"] if f["rule_id"] == "BG-NEAR-002"]
                    self.assertTrue(fx and not fx[0]["applied"])
                    self.assertIn(expect, fx[0]["suggested_patch"])


@unittest.skipUnless(shutil.which("cargo"), "cargo not installed: skipping the compile check")
class FixedCratesCompile(unittest.TestCase):
    """Patched fixtures must still compile (`cargo check --offline`)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.target = Path(cls.tmp.name) / "target"   # shared between the crates: near-sdk builds once

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check_compiles(self, name: str):
        crate = copy_crate(name, self.tmp.name)
        shutil.copy(FIXTURES / "Cargo.lock", crate / "Cargo.lock")   # pin to the cached versions
        self.assertEqual(run_cli("fix", str(crate), "--apply")[0], 0)
        p = subprocess.run(["cargo", "check", "--offline", "--manifest-path", str(crate / "Cargo.toml")],
                           capture_output=True, text=True, env={**os.environ, "CARGO_TARGET_DIR": str(self.target)})
        if p.returncode != 0 and any(s in p.stderr for s in ("failed to download", "no matching package", "offline")):
            self.skipTest(f"dependencies of {name} not in the offline cargo cache: {p.stderr.strip()[-300:]}")
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_d1_compiles(self):
        self.check_compiles("d1_vuln")

    def test_d3_compiles(self):
        self.check_compiles("d3_vuln")


if __name__ == "__main__":
    unittest.main()
