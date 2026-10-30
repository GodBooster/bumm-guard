"""MCP server tests: start ``bumm_guard.mcp_server`` over stdio and drive it with the SDK client.

Skipped when the optional ``mcp`` package is not installed (``pip install -e '.[mcp]'``).
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    HAVE_MCP = True
except ImportError:
    HAVE_MCP = False

ROOT = Path(__file__).resolve().parent.parent
D1_VULN = "tests/fixtures/near/d1_vuln"
EXPECTED_TOOLS = {"list_rules", "scan_contract", "fix_contract", "gate_deploy"}


def payload(result) -> dict:
    """Tool result as a dict: structured content if present, else the JSON text block."""
    sc = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if sc:
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    return json.loads(result.content[0].text)


@unittest.skipUnless(HAVE_MCP, "the optional 'mcp' package is not installed: run `pip install -e '.[mcp]'` to enable these tests")
class McpServerTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def session(self, cwd: Path = ROOT, **env):
        e = {"PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1"}
        e.update(env)
        params = StdioServerParameters(command=sys.executable, args=["-m", "bumm_guard.mcp_server"],
                                       cwd=str(cwd), env=e)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                yield s

    async def test_lists_four_tools_without_execute(self):
        async with self.session() as s:
            tools = (await s.list_tools()).tools
            self.assertEqual({t.name for t in tools}, EXPECTED_TOOLS)
            gate = next(t for t in tools if t.name == "gate_deploy")
            self.assertNotIn("execute", (getattr(gate, "input_schema", None) or gate.inputSchema)["properties"])
            self.assertIn("NEVER EXECUTES", gate.description)
            fix = next(t for t in tools if t.name == "fix_contract")
            self.assertIn("DRY-RUN", fix.description)
            for t in tools:
                self.assertTrue(t.description)

    async def test_list_rules(self):
        async with self.session() as s:
            data = payload(await s.call_tool("list_rules", {}))
            self.assertEqual([r["id"] for r in data["rules"]], ["BG-NEAR-001", "BG-NEAR-002", "BG-NEAR-003"])
            self.assertEqual(set(data["rules"][0]), {"id", "name", "severity", "summary"})

    async def test_scan_contract_d1_vuln(self):
        async with self.session() as s:
            r = await s.call_tool("scan_contract", {"path": D1_VULN})
            self.assertFalse(r.is_error if hasattr(r, "is_error") else r.isError)
            data = payload(r)
            self.assertEqual(data["summary"]["total"], 1)
            self.assertEqual([f["rule_id"] for f in data["findings"]], ["BG-NEAR-001"])
            self.assertEqual(data["findings"][0]["file"], D1_VULN + "/src/lib.rs")

    async def test_scan_matches_cli_json(self):
        import subprocess
        cli = subprocess.run([sys.executable, "-m", "bumm_guard", "scan", D1_VULN, "--format", "json"],
                             cwd=ROOT, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT)})
        async with self.session() as s:
            self.assertEqual(payload(await s.call_tool("scan_contract", {"path": D1_VULN})), json.loads(cli.stdout))

    async def test_fix_contract_default_is_dry_run(self):
        src = ROOT / D1_VULN / "src" / "lib.rs"
        before = src.read_bytes()
        async with self.session() as s:
            data = payload(await s.call_tool("fix_contract", {"path": D1_VULN}))
        self.assertEqual(data["mode"], "dry-run")
        self.assertEqual(len(data["diffs"]), 1)
        self.assertIn("+    #[private]", next(iter(data["diffs"].values())))
        self.assertNotIn("remaining", data)
        self.assertEqual(src.read_bytes(), before)

    async def test_fix_contract_apply_writes_and_rescans(self):
        with tempfile.TemporaryDirectory() as d:
            crate = Path(d) / "d1_vuln"
            shutil.copytree(ROOT / D1_VULN, crate)
            lib = crate / "src" / "lib.rs"
            before = lib.read_text()
            async with self.session(cwd=Path(d)) as s:
                data = payload(await s.call_tool("fix_contract", {"path": "d1_vuln", "apply": True}))
            self.assertEqual(data["mode"], "apply")
            self.assertEqual(data["remaining"]["summary"]["total"], 0)
            self.assertNotEqual(lib.read_text(), before)
            self.assertIn("#[private]", lib.read_text())

    async def test_gate_deploy_blocked_on_d1_vuln(self):
        async with self.session() as s:
            data = payload(await s.call_tool("gate_deploy", {"crate_dir": D1_VULN}))
        self.assertTrue(data["blocked"])
        self.assertFalse(data["passed"])
        self.assertEqual(data["blocking_findings"], 1)
        self.assertIsNone(data["deploy_command"])
        self.assertFalse(data["deploy_executed"])
        self.assertIsNone(data["report_path"])

    async def test_gate_deploy_passes_on_safe_and_returns_command_text(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(ROOT / "tests/fixtures/near/d1_safe", Path(d) / "c")
            async with self.session(cwd=Path(d)) as s:
                data = payload(await s.call_tool(
                    "gate_deploy", {"crate_dir": "c", "account": "demo.testnet", "network": "testnet"}))
            self.assertTrue(data["passed"])
            self.assertFalse(data["deploy_executed"])
            self.assertTrue(data["deploy_command"].startswith("near contract deploy demo.testnet"))
            self.assertTrue(Path(data["report_path"]).is_file())

    async def test_path_outside_cwd_is_rejected(self):
        async with self.session() as s:
            for tool, args in (("scan_contract", {"path": ".."}),
                               ("scan_contract", {"path": str(Path(tempfile.gettempdir()))}),
                               ("fix_contract", {"path": "../"}),
                               ("gate_deploy", {"crate_dir": ".."}),
                               ("gate_deploy", {"crate_dir": D1_VULN, "wasm": "/etc/hosts"})):
                r = await s.call_tool(tool, args)
                self.assertTrue(r.is_error if hasattr(r, "is_error") else r.isError, (tool, args))
                self.assertIn("outside", r.content[0].text)

    async def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            os.symlink(outside, Path(d) / "link")
            async with self.session(cwd=Path(d)) as s:
                r = await s.call_tool("scan_contract", {"path": "link"})
                self.assertTrue(r.is_error if hasattr(r, "is_error") else r.isError)

    async def test_allow_any_path_env(self):
        with tempfile.TemporaryDirectory() as d:
            async with self.session(cwd=Path(d), BUMM_GUARD_ALLOW_ANY_PATH="1") as s:
                data = payload(await s.call_tool("scan_contract", {"path": str(ROOT / D1_VULN)}))
            self.assertEqual(data["summary"]["total"], 1)

    async def test_missing_path_is_a_tool_error(self):
        async with self.session() as s:
            r = await s.call_tool("scan_contract", {"path": "does/not/exist"})
            self.assertTrue(r.is_error if hasattr(r, "is_error") else r.isError)


if __name__ == "__main__":
    unittest.main()
