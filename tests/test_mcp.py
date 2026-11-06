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
EXPECTED_TOOLS = {"list_rules", "scan_contract", "fix_contract", "gate_deploy", "estimate_storage",
                  "verify_deployed", "review_job"}


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

    async def test_lists_seven_tools_without_execute(self):
        async with self.session() as s:
            tools = (await s.list_tools()).tools
            self.assertEqual({t.name for t in tools}, EXPECTED_TOOLS)
            gate = next(t for t in tools if t.name == "gate_deploy")
            props = (getattr(gate, "input_schema", None) or gate.inputSchema)["properties"]
            self.assertNotIn("execute", props)
            self.assertTrue({"simulate_report", "account", "network"} <= set(props), props)
            self.assertIn("NEVER EXECUTES", gate.description)
            self.assertIn("UPGRADE", gate.description)
            fix = next(t for t in tools if t.name == "fix_contract")
            self.assertIn("DRY-RUN", fix.description)
            review = next(t for t in tools if t.name == "review_job")
            rprops = (getattr(review, "input_schema", None) or review.inputSchema)["properties"]
            self.assertFalse({"execute", "sign", "send"} & set(rprops), rprops)
            self.assertIn("NEVER SIGNS OR SENDS", review.description)
            for t in tools:
                self.assertTrue(t.description)
            for name in ("estimate_storage", "verify_deployed"):
                t = next(t for t in tools if t.name == name)
                self.assertIn("READ-ONLY", t.description)
                self.assertNotIn("execute", (getattr(t, "input_schema", None) or t.inputSchema)["properties"])

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

    async def test_rpc_tools_refuse_paths_outside_cwd(self):
        # Nothing can be stubbed in the server subprocess: use inputs that are refused before any network call.
        async with self.session() as s:
            r = await s.call_tool("estimate_storage", {"wasm": "/etc/hosts"})
            self.assertTrue(r.is_error if hasattr(r, "is_error") else r.isError)
            self.assertIn("outside", r.content[0].text)
            r = await s.call_tool("verify_deployed", {"account": "demo.testnet", "wasm": "/etc/hosts"})
            self.assertTrue(r.is_error if hasattr(r, "is_error") else r.isError)

    async def test_missing_path_is_a_tool_error(self):
        async with self.session() as s:
            r = await s.call_tool("scan_contract", {"path": "does/not/exist"})
            self.assertTrue(r.is_error if hasattr(r, "is_error") else r.isError)


class McpToolFunctionTests(unittest.TestCase):
    """The two RPC tools as plain functions (no ``mcp`` package needed), with urllib stubbed.

    The stdio server runs in a subprocess, where urllib cannot be stubbed, so the RPC paths are tested here.
    """

    def test_estimate_storage_and_verify_deployed_with_stubbed_rpc(self):
        import base64
        import hashlib
        from unittest import mock
        from bumm_guard import mcp_server

        wasm_bytes = b"\0asm\x01\0\0\0" + b"x" * 100
        with tempfile.TemporaryDirectory() as d:
            w = Path(d) / "c.wasm"
            w.write_bytes(wasm_bytes)

            class Resp:
                def __init__(self, result):
                    self.body = json.dumps({"jsonrpc": "2.0", "id": "x", "result": result}).encode()
                def read(self):
                    return self.body
                def __enter__(self):
                    return self
                def __exit__(self, *a):
                    return False

            def fake(req, timeout=None):
                p = json.loads(req.data)
                if p["method"] == "EXPERIMENTAL_protocol_config":
                    return Resp({"runtime_config": {"storage_amount_per_byte": "10000000000000000000"}})
                if p["params"]["request_type"] == "view_code":
                    return Resp({"code_base64": base64.b64encode(wasm_bytes).decode(), "hash": "x"})
                return Resp({"result": list(b"3"), "logs": []})

            with mock.patch("urllib.request.urlopen", side_effect=fake), \
                    mock.patch.dict(os.environ, {mcp_server.ALLOW_ANY_PATH_ENV: "1"}):
                est = mcp_server.estimate_storage(str(w), "testnet")["storage_estimate"]
                ver = mcp_server.verify_deployed("demo.testnet", str(w), "testnet", ["get_x"])
                ver_ok = mcp_server.verify_deployed("demo.testnet", str(w), "testnet", ["get_x=3"])
                ver_bad = mcp_server.verify_deployed("demo.testnet", str(w), "testnet", ["get_x=4"])
                with self.assertRaises(mcp_server.ToolInputError):
                    mcp_server.verify_deployed("demo.testnet", str(w), "testnet", ["new"])
                with self.assertRaises(mcp_server.ToolInputError):
                    mcp_server.verify_deployed("demo.testnet", str(w), "testnet", ["get_x={"])
        self.assertEqual(est["code_bytes"], len(wasm_bytes))
        self.assertEqual(est["locked_near_for_code"], "0.0011")
        self.assertTrue(ver["verified"])
        self.assertEqual(ver["post_deploy"]["code_hash_on_chain"], hashlib.sha256(wasm_bytes).hexdigest())
        self.assertEqual(ver["post_deploy"]["smoke"], [{"view": "get_x", "ok": True, "result": 3}])
        self.assertTrue(ver_ok["verified"])
        self.assertEqual(ver_ok["post_deploy"]["smoke"], [{"view": "get_x", "expected": 3, "ok": True, "result": 3}])
        self.assertFalse(ver_bad["verified"])
        self.assertEqual(ver_bad["post_deploy"]["smoke"][0]["error"], "expected 4, got 3")


if __name__ == "__main__":
    unittest.main()
