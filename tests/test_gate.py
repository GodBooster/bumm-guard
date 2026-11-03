"""Tests for `bumm-guard gate`. `near` is never called: execute paths are refused before that."""

import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bumm_guard import __version__, cli
from bumm_guard.gate import REPORT_DIR, REPORT_NAME, deploy_command, valid_account

FIXTURES = Path(__file__).parent / "fixtures" / "near"


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _empty_account(req, timeout=None):
    """urlopen stub: every account is empty (NEAR RPC ``NO_CONTRACT_CODE``), so --account means a fresh deploy."""
    body = {"jsonrpc": "2.0", "id": "x", "error": {"name": "HANDLER_ERROR", "cause": {"name": "NO_CONTRACT_CODE"},
                                                   "message": "Server error", "data": "no contract code"}}
    return io.BytesIO(json.dumps(body).encode())


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch("urllib.request.urlopen", _empty_account)  # no test touches the network
        patcher.start()
        self.addCleanup(patcher.stop)

    def crate(self, name):
        dst = Path(self.tmp.name) / name
        shutil.copytree(FIXTURES / name, dst, ignore=shutil.ignore_patterns("target"))
        return dst

    def wasm(self):
        w = Path(self.tmp.name) / "contract.wasm"
        w.write_bytes(b"\0asm\x01\0\0\0fake")
        return w

    def test_vulnerable_crate_is_blocked(self):
        crate = self.crate("d1_vuln")
        code, out, _ = run_cli("gate", str(crate), "--wasm", str(self.wasm()), "--account", "demo.testnet")
        self.assertEqual(code, 3)
        self.assertIn("DEPLOY BLOCKED", out)
        self.assertIn("BG-NEAR-001", out)
        self.assertNotIn("near contract deploy", out)
        self.assertFalse((crate / REPORT_DIR).exists())

    def test_stale_report_removed_when_blocked(self):
        crate = self.crate("d1_vuln")
        rep = crate / REPORT_DIR / REPORT_NAME
        rep.parent.mkdir()
        rep.write_text("{}")
        self.assertEqual(run_cli("gate", str(crate))[0], 3)
        self.assertFalse(rep.exists())

    def test_clean_crate_writes_report_and_prints_command(self):
        crate, wasm = self.crate("d1_safe"), self.wasm()
        code, out, _ = run_cli("gate", str(crate), "--wasm", str(wasm), "--account", "demo.testnet")
        self.assertEqual(code, 0)
        self.assertIn(f"near contract deploy demo.testnet use-file {wasm} without-init-call "
                      "network-config testnet sign-with-keychain send", out)
        data = json.loads((crate / REPORT_DIR / REPORT_NAME).read_text())
        for key in ("tool", "version", "timestamp_utc", "rule_ids_checked", "findings_count", "rs_sha256", "wasm"):
            self.assertIn(key, data)
        self.assertEqual(data["version"], __version__)
        self.assertEqual(data["rule_ids_checked"], ["BG-NEAR-001", "BG-NEAR-002", "BG-NEAR-003"])
        self.assertEqual(data["findings_count"], 0)
        self.assertEqual(data["rs_sha256"], {
            "src/lib.rs": hashlib.sha256((crate / "src/lib.rs").read_bytes()).hexdigest()})
        self.assertEqual(data["wasm"]["sha256"], hashlib.sha256(wasm.read_bytes()).hexdigest())
        self.assertTrue(data["timestamp_utc"].endswith("Z"))

    def test_dry_run_writes_nothing(self):
        crate = self.crate("d1_safe")
        code, out, _ = run_cli("gate", str(crate), "--dry-run", "--account", "demo.testnet")
        self.assertEqual(code, 0)
        self.assertIn("near contract deploy demo.testnet", out)
        self.assertFalse((crate / REPORT_DIR).exists())

    def test_missing_wasm_is_warned_not_hashed(self):
        crate = self.crate("d1_safe")
        code, out, err = run_cli("gate", str(crate), "--wasm", "/nonexistent/x.wasm", "--account", "demo.testnet")
        self.assertEqual(code, 0)
        self.assertIn("warning", err)
        self.assertIsNone(json.loads((crate / REPORT_DIR / REPORT_NAME).read_text())["wasm"]["sha256"])

    def test_execute_on_mainnet_is_refused(self):
        crate = self.crate("d1_safe")
        code, out, err = run_cli("gate", str(crate), "--wasm", str(self.wasm()), "--account", "demo.near",
                                 "--network", "mainnet", "--execute")
        self.assertEqual(code, 2)
        self.assertIn("testnet", err)
        self.assertNotIn("near contract deploy", out)
        self.assertFalse((crate / REPORT_DIR).exists())

    def test_mainnet_print_only_is_allowed(self):
        code, out, _ = run_cli("gate", str(self.crate("d1_safe")), "--account", "demo.near", "--network", "mainnet")
        self.assertEqual(code, 0)
        self.assertIn("network-config mainnet", out)

    def test_execute_without_near_exits_4(self):
        crate = self.crate("d1_safe")
        with mock.patch.dict(os.environ, {"PATH": ""}):
            code, _, err = run_cli("gate", str(crate), "--wasm", str(self.wasm()), "--account", "demo.testnet",
                                   "--execute")
        self.assertEqual(code, 4)
        self.assertIn("not found on PATH", err)

    def test_execute_blocked_crate_never_deploys(self):
        with mock.patch("bumm_guard.gate.subprocess.run") as run:
            code, out, _ = run_cli("gate", str(self.crate("d1_vuln")), "--wasm", str(self.wasm()),
                                   "--account", "demo.testnet", "--execute")
        self.assertEqual(code, 3)
        run.assert_not_called()

    def test_execute_requires_account_and_wasm(self):
        self.assertEqual(run_cli("gate", str(self.crate("d1_safe")), "--execute")[0], 2)

    def test_bad_inputs(self):
        self.assertEqual(run_cli("gate", str(Path(self.tmp.name) / "nope"))[0], 2)
        self.assertEqual(run_cli("gate", str(self.crate("d1_safe")), "--account", "Bad Account; rm")[0], 2)

    def test_helpers(self):
        self.assertTrue(valid_account("demo.testnet") and valid_account("a-b_c.d1.near"))
        self.assertFalse(valid_account("-x.near") or valid_account("a") or valid_account("A.near"))
        self.assertEqual(deploy_command("a.testnet", "-w.wasm", "testnet")[5], "./-w.wasm")


if __name__ == "__main__":
    unittest.main()
