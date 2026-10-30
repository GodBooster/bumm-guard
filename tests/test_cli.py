import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bumm_guard import Finding, cli

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "near"


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_exit_code_1_on_high(self):
        for n in ("d1_vuln", "d2_vuln", "d3_vuln"):
            code, _, _ = run_cli("scan", str(FIXTURES / n), "--format", "json")
            self.assertEqual(code, 1, n)

    def test_exit_code_0_when_clean(self):
        for n in ("d1_safe", "d2_safe", "d3_safe", "proxy"):
            code, out, _ = run_cli("scan", str(FIXTURES / n), "--format", "json")
            self.assertEqual(code, 0, n)
            self.assertEqual(json.loads(out)["findings"], [])

    def test_exit_code_0_for_non_high_only(self):
        src = (
            "#[near] impl C { pub fn r(&self, #[callback_unwrap] v: u32) -> u32 { v } }\n"
        )
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.rs"
            p.write_text(src)
            code, out, _ = run_cli("scan", str(p), "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["findings"][0]["severity"], "info")

    def test_missing_path_exit_2(self):
        code, _, err = run_cli("scan", str(ROOT / "does-not-exist"))
        self.assertEqual(code, 2)
        self.assertIn("no such file", err)

    def test_json_shape(self):
        code, out, _ = run_cli("scan", str(FIXTURES), "--format", "json")
        data = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(set(data), {"tool", "version", "files_scanned", "summary", "findings", "errors"})
        self.assertEqual(data["summary"]["total"], 3)
        self.assertEqual(data["summary"]["by_rule"], {"BG-NEAR-001": 1, "BG-NEAR-002": 1, "BG-NEAR-003": 1})
        self.assertEqual(data["summary"]["by_severity"], {"high": 3})
        for f in data["findings"]:
            self.assertEqual(
                set(f), {"rule_id", "severity", "file", "line", "message", "fix_hint", "snippet", "rule_name"})
            self.assertIsInstance(f["line"], int)
            self.assertIn(f["severity"], ("critical", "high", "medium", "low", "info"))

    def test_text_format(self):
        code, out, _ = run_cli("scan", str(FIXTURES / "d3_vuln"))
        self.assertEqual(code, 1)
        self.assertIn("BG-NEAR-003", out)
        self.assertIn("src/lib.rs:18:", out)
        self.assertIn("1 finding(s)", out)

    def test_file_argument_and_target_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "target").mkdir()
            (Path(d) / "target" / "x.rs").write_text((FIXTURES / "d3_vuln/src/lib.rs").read_text())
            code, out, _ = run_cli("scan", d, "--format", "json")
            self.assertEqual((code, json.loads(out)["files_scanned"]), (0, 0))
            code, out, _ = run_cli("scan", str(Path(d) / "target" / "x.rs"), "--format", "json")
            self.assertEqual(code, 1)  # explicit file is always scanned

    def test_rules_command(self):
        code, out, _ = run_cli("rules")
        self.assertEqual(code, 0)
        for rid in ("BG-NEAR-001", "BG-NEAR-002", "BG-NEAR-003"):
            self.assertIn(rid, out)
        code, out, _ = run_cli("rules", "--format", "json")
        self.assertEqual([r["id"] for r in json.loads(out)], ["BG-NEAR-001", "BG-NEAR-002", "BG-NEAR-003"])

    def test_module_entrypoint(self):
        p = subprocess.run(
            [sys.executable, "-m", "bumm_guard", "scan", str(FIXTURES / "d1_vuln"), "--format", "json"],
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertEqual(json.loads(p.stdout)["findings"][0]["rule_id"], "BG-NEAR-001")


class ModelTests(unittest.TestCase):
    def test_to_dict_and_bumm_mapping(self):
        f = Finding("BG-NEAR-001", "high", "a.rs", 3, "m", "h", "s", "non-private-callback")
        self.assertEqual(f.to_dict()["line"], 3)
        v = f.to_bumm_vulnerability()
        self.assertEqual(v["category"], "non_private_callback")
        self.assertEqual(v["code_location"], "a.rs:3")
        self.assertEqual(v["source"], "custom_check")


if __name__ == "__main__":
    unittest.main()
