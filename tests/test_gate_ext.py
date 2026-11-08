"""Tests for the gate extension (docs/FORMATS.md §4): simulate binding, storage estimate, --verify-deployed.

No test touches the network: ``urllib.request.urlopen`` is stubbed. One optional live test is guarded by
``BUMM_GUARD_LIVE_RPC=1``.
"""

import base64
import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from bumm_guard import cli
from bumm_guard.gate import REPORT_DIR, REPORT_NAME
from bumm_guard.storage import (DEFAULT_PER_BYTE, RPC_URLS, RpcError, estimate_storage, fetch_per_byte,
                                locked_near, rpc_call, simulate_state_bytes)
from bumm_guard.verify import SmokeSpecError, parse_smoke, post_deploy_ok, verify_deployed

FIXTURES = Path(__file__).parent / "fixtures" / "near"
WASM_BYTES = b"\0asm\x01\0\0\0" + b"payload" * 20
WASM_SHA = hashlib.sha256(WASM_BYTES).hexdigest()
PER_BYTE = "10000000000000000000"


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class FakeResp:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeRpc:
    """Stub for urlopen: records calls, answers like a NEAR RPC node."""

    def __init__(self, code=WASM_BYTES, per_byte=PER_BYTE, views=None, account=None, fail=False):
        self.calls, self.code, self.per_byte, self.fail = [], code, per_byte, fail
        self.views = views or {}          # method -> bytes result, or str error
        self.account = account

    def __call__(self, req, timeout=None):
        if self.fail:
            raise urllib.error.URLError("network down")
        p = json.loads(req.data)
        self.calls.append((req.full_url, p, timeout))
        wrap = lambda result: FakeResp({"jsonrpc": "2.0", "id": "x", "result": result})
        if p["method"] == "EXPERIMENTAL_protocol_config":
            return wrap({"runtime_config": {"storage_amount_per_byte": self.per_byte}})
        rt = p["params"]["request_type"]
        if rt == "view_code":
            if self.code is None:
                return wrap({"error": "contract code of account demo.testnet has not been deployed"})
            return wrap({"code_base64": base64.b64encode(self.code).decode(), "hash": "ignored"})
        if rt == "view_account":
            return wrap(self.account or {"amount": "5000000000000000000000000", "storage_usage": 182})
        if rt == "call_function":
            r = self.views.get(p["params"]["method_name"], "MethodNotFound")
            if isinstance(r, str):
                return FakeResp({"jsonrpc": "2.0", "id": "x", "error": {"message": "wasm execution failed", "data": r}})
            return wrap({"result": list(r), "logs": []})
        raise AssertionError(p)


def sim_report(wasm_sha=WASM_SHA, failed=0, names=("a", "b"), **extra):
    scen = [{"name": n, "passed": not (failed and i < failed), "steps": []} for i, n in enumerate(names)]
    return {"tool": "bumm-guard", "wasm_sha256": wasm_sha, "scenarios": scen,
            "summary": {"scenarios": len(scen), "passed": len(scen) - failed, "failed": failed,
                        "steps": 0, "steps_failed": 0}, **extra}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Default: every account is empty (fresh deploy); tests that need RPC answers patch again.
        patcher = mock.patch("urllib.request.urlopen", FakeRpc(code=None))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = Path(self.tmp.name)
        self.wasm = self.root / "c.wasm"
        self.wasm.write_bytes(WASM_BYTES)

    def crate(self, name="d1_safe"):
        dst = self.root / name
        shutil.copytree(FIXTURES / name, dst, ignore=shutil.ignore_patterns("target"))
        return dst

    def report_file(self, **kw):
        p = self.root / "sim.json"
        p.write_text(json.dumps(sim_report(**kw)))
        return p

    def gate_report(self, crate):
        return json.loads((crate / REPORT_DIR / REPORT_NAME).read_text())


class StorageTests(Base):
    def test_protocol_config_parsed(self):
        rpc = FakeRpc(per_byte="12345")
        with mock.patch("urllib.request.urlopen", rpc):
            per_byte, source = fetch_per_byte("testnet")
        self.assertEqual(per_byte, 12345)
        self.assertTrue(source.startswith("EXPERIMENTAL_protocol_config via RPC testnet, 20"))
        url, payload, timeout = rpc.calls[0]
        self.assertEqual(url, RPC_URLS["testnet"])
        self.assertEqual(payload["method"], "EXPERIMENTAL_protocol_config")
        self.assertEqual(payload["params"], {"finality": "final"})
        self.assertEqual(timeout, 10)

    def test_rpc_url_override_and_mainnet(self):
        rpc = FakeRpc()
        with mock.patch("urllib.request.urlopen", rpc):
            fetch_per_byte("mainnet")
            fetch_per_byte("testnet", "http://127.0.0.1:3030")
        self.assertEqual([c[0] for c in rpc.calls], ["https://rpc.mainnet.near.org", "http://127.0.0.1:3030"])
        self.assertEqual(RPC_URLS, {"testnet": "https://rpc.testnet.near.org", "mainnet": "https://rpc.mainnet.near.org"})

    def test_fallback_on_failure_and_garbage(self):
        with mock.patch("urllib.request.urlopen", FakeRpc(fail=True)):
            self.assertEqual(fetch_per_byte("testnet"), (DEFAULT_PER_BYTE, "default (RPC unavailable)"))
        with mock.patch("urllib.request.urlopen", FakeRpc(per_byte="not a number")):
            self.assertEqual(fetch_per_byte("testnet")[1], "default (RPC unavailable)")
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("slow")):
            self.assertEqual(fetch_per_byte("testnet")[0], DEFAULT_PER_BYTE)

    def test_non_http_rpc_url_refused(self):
        with self.assertRaises(RpcError):
            rpc_call("file:///etc/passwd", "x", {})
        self.assertEqual(fetch_per_byte("testnet", "file:///etc/passwd")[1], "default (RPC unavailable)")

    def test_locked_near_arithmetic(self):
        self.assertEqual(locked_near(225_057, 10**19), "2.2506")
        self.assertEqual(locked_near(100_000, 10**19), "1.0000")
        self.assertEqual(locked_near(0, 10**19), "0.0000")
        self.assertEqual(locked_near(1, 10**19), "0.0000")
        self.assertEqual(locked_near(5, 10**19), "0.0001")      # 0.00005 rounds half up

    def test_estimate_block_keys_and_values(self):
        with mock.patch("urllib.request.urlopen", FakeRpc()):
            est = estimate_storage(self.wasm, "testnet")
        self.assertEqual(set(est), {"code_bytes", "state_bytes_after_init", "storage_amount_per_byte_yocto",
                                    "source", "locked_near_for_code", "note"})
        self.assertEqual(est["code_bytes"], len(WASM_BYTES))
        self.assertIsNone(est["state_bytes_after_init"])
        self.assertEqual(est["storage_amount_per_byte_yocto"], PER_BYTE)
        self.assertIn("locked balance, not a fee", est["note"])

    def test_estimate_with_account_reports_balance(self):
        rpc = FakeRpc()
        with mock.patch("urllib.request.urlopen", rpc):
            est = estimate_storage(self.wasm, "testnet", account="demo.testnet")
        self.assertEqual(est["account"], {"id": "demo.testnet", "storage_usage": 182,
                                          "amount_yocto": "5000000000000000000000000", "amount_near": "5.0000"})
        self.assertEqual([c[1]["method"] for c in rpc.calls], ["EXPERIMENTAL_protocol_config", "query"])

    def test_simulate_state_bytes_never_invented(self):
        self.assertIsNone(simulate_state_bytes(sim_report()))
        self.assertEqual(simulate_state_bytes(sim_report(storage_usage=4242)), 4242)
        self.assertIsNone(simulate_state_bytes(sim_report(storage_usage="4242")))
        self.assertIsNone(simulate_state_bytes(None))


class SimulateBindingTests(Base):
    def test_accepted_report_and_exact_keys(self):
        crate = self.crate()
        abi = self.root / "abi.json"
        abi.write_text("{}")
        code, out, _ = run_cli("gate", str(crate), "--wasm", str(self.wasm), "--abi", str(abi),
                               "--simulate-report", str(self.report_file()), "--account", "demo.testnet")
        self.assertEqual(code, 0)
        self.assertNotIn("not verified", out)
        data = self.gate_report(crate)
        self.assertEqual(data["artifact"], {"wasm_sha256": WASM_SHA,
                                            "abi_sha256": hashlib.sha256(b"{}").hexdigest()})
        self.assertEqual(data["simulate"], {"report": str(self.root / "sim.json"), "passed": True, "scenarios": 2})
        self.assertIsNone(data["storage_estimate"])
        self.assertIsNone(data["post_deploy"])
        self.assertEqual(data["wasm"]["sha256"], WASM_SHA)

    def test_missing_report_is_allowed_but_warned(self):
        crate = self.crate()
        code, out, _ = run_cli("gate", str(crate), "--wasm", str(self.wasm))
        self.assertEqual(code, 0)
        self.assertIn("no scenario report: behaviour not verified", out)
        data = self.gate_report(crate)
        self.assertIsNone(data["simulate"])
        self.assertEqual(data["artifact"]["abi_sha256"], None)

    def test_wrong_wasm_hash_blocks_exit_3(self):
        crate = self.crate()
        code, out, _ = run_cli("gate", str(crate), "--wasm", str(self.wasm),
                               "--simulate-report", str(self.report_file(wasm_sha="0" * 64)))
        self.assertEqual(code, 3)
        self.assertIn("DEPLOY BLOCKED: simulate report is for a different wasm", out)
        self.assertNotIn("near contract deploy", out)
        self.assertFalse((crate / REPORT_DIR).exists())

    def test_null_report_hash_blocks(self):
        rep = self.root / "sim.json"
        data = sim_report()
        data["wasm_sha256"] = None
        rep.write_text(json.dumps(data))
        self.assertEqual(run_cli("gate", str(self.crate()), "--wasm", str(self.wasm), "--simulate-report", str(rep))[0], 3)

    def test_failed_scenario_blocks_and_names_it(self):
        crate = self.crate()
        code, out, _ = run_cli("gate", str(crate), "--wasm", str(self.wasm),
                               "--simulate-report", str(self.report_file(failed=1, names=("N06 capacity", "N06 dup"))))
        self.assertEqual(code, 3)
        self.assertIn("DEPLOY BLOCKED", out)
        self.assertIn("N06 capacity", out)
        self.assertNotIn("N06 dup", out)
        self.assertFalse((crate / REPORT_DIR).exists())

    def test_block_removes_stale_report(self):
        crate = self.crate()
        self.assertEqual(run_cli("gate", str(crate), "--wasm", str(self.wasm))[0], 0)
        self.assertTrue((crate / REPORT_DIR / REPORT_NAME).is_file())
        code, out, _ = run_cli("gate", str(crate), "--wasm", str(self.wasm),
                               "--simulate-report", str(self.report_file(failed=1)))
        self.assertEqual(code, 3)
        self.assertFalse((crate / REPORT_DIR / REPORT_NAME).exists())

    def test_summary_failed_nonzero_blocks_even_if_scenarios_look_ok(self):
        rep = self.root / "sim.json"
        data = sim_report()
        data["summary"]["failed"] = 1
        rep.write_text(json.dumps(data))
        self.assertEqual(run_cli("gate", str(self.crate()), "--wasm", str(self.wasm), "--simulate-report", str(rep))[0], 3)

    def test_fresh_report_with_a_forged_scenario_flag_blocks(self):
        rep = self.root / "sim.json"
        data = sim_report(names=("a",))
        data["scenarios"][0]["steps"] = [{"index": 0, "kind": "call", "method": "register", "passed": False,
                                          "expected": "ok", "actual": {"status": "failure", "error": "x"}}]
        data["summary"].update(steps=1, steps_failed=1)   # summary adds up, the scenario verdict was edited
        rep.write_text(json.dumps(data))
        code, out, _ = run_cli("gate", str(self.crate()), "--wasm", str(self.wasm), "--simulate-report", str(rep))
        self.assertEqual(code, 3, out)
        self.assertIn("simulate report contradicts itself (edited?): scenario 'a' says it passed, but step 0 did not pass", out)

    def test_bad_inputs_exit_2(self):
        crate = str(self.crate())
        self.assertEqual(run_cli("gate", crate, "--wasm", str(self.wasm), "--simulate-report", "/nope.json")[0], 2)
        bad = self.root / "bad.json"
        bad.write_text("[]")
        self.assertEqual(run_cli("gate", crate, "--wasm", str(self.wasm), "--simulate-report", str(bad))[0], 2)
        self.assertEqual(run_cli("gate", crate, "--simulate-report", str(self.report_file()))[0], 2)   # no wasm
        self.assertEqual(run_cli("gate", crate, "--wasm", str(self.wasm), "--abi", "/nope.json")[0], 2)

    def test_vulnerable_crate_still_blocked_first(self):
        code, out, _ = run_cli("gate", str(self.crate("d1_vuln")), "--wasm", str(self.wasm),
                               "--simulate-report", str(self.report_file()))
        self.assertEqual(code, 3)
        self.assertIn("high/critical finding", out)


class GateStorageCliTests(Base):
    def test_estimate_storage_flag_writes_block_and_state_from_simulate(self):
        crate = self.crate()
        rep = self.report_file(storage_usage=777)
        with mock.patch("urllib.request.urlopen", FakeRpc()):
            code, out, _ = run_cli("gate", str(crate), "--wasm", str(self.wasm), "--simulate-report", str(rep),
                                   "--estimate-storage", "--network", "mainnet", "--account", "demo.near")
        self.assertEqual(code, 0)
        est = self.gate_report(crate)["storage_estimate"]
        self.assertEqual(est["state_bytes_after_init"], 777)
        self.assertEqual(est["code_bytes"], len(WASM_BYTES))
        self.assertEqual(est["account"]["storage_usage"], 182)
        self.assertIn("storage:", out)

    def test_rpc_down_uses_default_and_says_so(self):
        crate = self.crate()
        with mock.patch("urllib.request.urlopen", FakeRpc(fail=True)):
            code, out, _ = run_cli("gate", str(crate), "--wasm", str(self.wasm), "--estimate-storage")
        self.assertEqual(code, 0)
        est = self.gate_report(crate)["storage_estimate"]
        self.assertEqual(est["source"], "default (RPC unavailable)")
        self.assertEqual(est["storage_amount_per_byte_yocto"], str(DEFAULT_PER_BYTE))

    def test_no_rpc_without_the_flag(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            self.assertEqual(run_cli("gate", str(self.crate()), "--wasm", str(self.wasm))[0], 0)

    def test_estimate_needs_wasm(self):
        self.assertEqual(run_cli("gate", str(self.crate()), "--estimate-storage")[0], 2)


class VerifyDeployedTests(Base):
    def test_parse_smoke(self):
        self.assertEqual(parse_smoke("get_count"), ("get_count", "{}", None))
        self.assertEqual(parse_smoke('is_registered:{"account_id":"a.testnet"}'),
                         ("is_registered", '{"account_id":"a.testnet"}', None))
        for bad in ("new", "init_state", "migrate", "Migrate_v2", "bad-name", "x:{not json", ":{}", "x:{} junk"):
            with self.assertRaises(SmokeSpecError, msg=bad):
                parse_smoke(bad)

    def test_parse_smoke_expectations(self):
        self.assertEqual(parse_smoke("get_count=2"), ("get_count", "{}", "2"))
        self.assertEqual(parse_smoke('get_deal:{"id":0}={"buyer":"a.testnet","released":false}'),
                         ("get_deal", '{"id":0}', '{"buyer":"a.testnet","released":false}'))
        self.assertEqual(parse_smoke('get_owner=null'), ("get_owner", "{}", "null"))
        self.assertEqual(parse_smoke('get_list:[1,2]=[]'), ("get_list", "[1,2]", "[]"))
        # an "=" inside the args JSON does not start the expectation
        self.assertEqual(parse_smoke('f:{"k":"a=b"}'), ("f", '{"k":"a=b"}', None))
        self.assertEqual(parse_smoke('f:{"k":"a=b"}="x=y"'), ("f", '{"k":"a=b"}', '"x=y"'))
        for bad in ("get_count=", "get_count={nope", "get_count:{}=", "new=1"):
            with self.assertRaises(SmokeSpecError, msg=bad):
                parse_smoke(bad)

    def test_smoke_expectation_match_and_mismatch(self):
        rpc = FakeRpc(views={"get_registered_count": b"2", "get_deal": b'{"buyer":"a.testnet","released":false}'})
        with mock.patch("urllib.request.urlopen", rpc):
            block = verify_deployed("demo.testnet", self.wasm, "testnet",
                                    ["get_registered_count=2", 'get_deal:{"id":0}={"released":false,"buyer":"a.testnet"}',
                                     "get_registered_count=3", "get_registered_count"])
        self.assertEqual(block["smoke"][0], {"view": "get_registered_count", "expected": 2, "ok": True, "result": 2})
        self.assertEqual(block["smoke"][1], {"view": "get_deal", "args": {"id": 0},
                                             "expected": {"released": False, "buyer": "a.testnet"},
                                             "ok": True, "result": {"buyer": "a.testnet", "released": False}})
        self.assertEqual(block["smoke"][2], {"view": "get_registered_count", "expected": 3, "ok": False, "result": 2,
                                             "error": "expected 3, got 2"})
        self.assertEqual(block["smoke"][3], {"view": "get_registered_count", "ok": True, "result": 2})
        self.assertFalse(post_deploy_ok(block))

    def test_cli_smoke_expectation_exit_5_and_prints_expected_vs_actual(self):
        d = self.root / "work"
        d.mkdir()
        with mock.patch("urllib.request.urlopen", FakeRpc(views={"get_n": b"7"})):
            code, out, _ = run_cli("gate", str(d), "--verify-deployed", "demo.testnet", "--wasm", str(self.wasm),
                                   "--smoke", "get_n=7")
            self.assertEqual(code, 0, out)
            self.assertIn("smoke get_n: ok: 7 (as expected)", out)
            self.assertEqual(self.gate_report(d)["post_deploy"]["smoke"][0]["expected"], 7)
            code, out, _ = run_cli("gate", str(d), "--verify-deployed", "demo.testnet", "--wasm", str(self.wasm),
                                   "--smoke", "get_n=8")
        self.assertEqual(code, 5, out)
        self.assertIn("smoke get_n: FAILED: expected 8, got 7", out)
        self.assertIn("post-deploy verification FAILED", out)
        self.assertEqual(self.gate_report(d)["post_deploy"]["smoke"][0],
                         {"view": "get_n", "expected": 8, "ok": False, "result": 7, "error": "expected 8, got 7"})
        with mock.patch("urllib.request.urlopen", FakeRpc()):
            self.assertEqual(run_cli("gate", str(d), "--verify-deployed", "demo.testnet", "--wasm", str(self.wasm),
                                     "--smoke", "get_n={bad")[0], 2)

    def test_match_and_smoke_ok(self):
        rpc = FakeRpc(views={"get_registered_count": b"2", "is_registered": b"true"})
        with mock.patch("urllib.request.urlopen", rpc):
            block = verify_deployed("demo.testnet", self.wasm, "testnet",
                                    ["get_registered_count", 'is_registered:{"account_id":"alice.testnet"}'])
        self.assertEqual(set(block), {"account", "code_hash_on_chain", "matches_artifact", "smoke", "note"})
        self.assertEqual(block["code_hash_on_chain"], WASM_SHA)
        self.assertTrue(block["matches_artifact"])
        self.assertEqual(block["smoke"][0], {"view": "get_registered_count", "ok": True, "result": 2})
        self.assertEqual(block["smoke"][1]["result"], True)
        calls = [c[1]["params"] for c in rpc.calls if c[1]["params"]["request_type"] == "call_function"]
        self.assertEqual(base64.b64decode(calls[0]["args_base64"]), b"{}")
        self.assertEqual(json.loads(base64.b64decode(calls[1]["args_base64"])), {"account_id": "alice.testnet"})
        self.assertTrue(all(c[1]["params"]["request_type"] != "send_tx" for c in rpc.calls))

    def test_mismatch(self):
        with mock.patch("urllib.request.urlopen", FakeRpc(code=b"\0asm\x01\0\0\0other")):
            block = verify_deployed("demo.testnet", self.wasm)
        self.assertFalse(block["matches_artifact"])
        self.assertEqual(block["code_hash_on_chain"], hashlib.sha256(b"\0asm\x01\0\0\0other").hexdigest())

    def test_smoke_error_text_recorded(self):
        with mock.patch("urllib.request.urlopen", FakeRpc(views={"get_x": "Smart contract panicked: boom"})):
            block = verify_deployed("demo.testnet", self.wasm, smoke=["get_x"])
        self.assertTrue(block["matches_artifact"])
        self.assertFalse(block["smoke"][0]["ok"])
        self.assertIn("panicked: boom", block["smoke"][0]["error"])

    def test_no_contract_and_rpc_down(self):
        with mock.patch("urllib.request.urlopen", FakeRpc(code=None)):
            block = verify_deployed("demo.testnet", self.wasm, smoke=["get_x"])
        self.assertIsNone(block["code_hash_on_chain"])
        self.assertFalse(block["matches_artifact"])
        self.assertIn("not been deployed", block["error"])
        self.assertFalse(block["smoke"][0]["ok"])
        with mock.patch("urllib.request.urlopen", FakeRpc(fail=True)):
            self.assertFalse(verify_deployed("demo.testnet", self.wasm)["matches_artifact"])

    def test_cli_exit_0_and_report_created(self):
        d = self.root / "work"
        d.mkdir()
        with mock.patch("urllib.request.urlopen", FakeRpc(views={"get_n": b"7"})):
            code, out, err = run_cli("gate", str(d), "--verify-deployed", "demo.testnet", "--wasm", str(self.wasm),
                                     "--smoke", "get_n")
        self.assertEqual(code, 0, err)
        self.assertIn("MATCH", out)
        data = self.gate_report(d)
        self.assertTrue(data["post_deploy"]["matches_artifact"])
        self.assertEqual(data["post_deploy"]["account"], "demo.testnet")

    def test_cli_exit_5_on_mismatch_and_on_failed_smoke(self):
        d = self.root / "work"
        d.mkdir()
        with mock.patch("urllib.request.urlopen", FakeRpc(code=b"different")):
            code, out, _ = run_cli("gate", str(d), "--verify-deployed", "demo.testnet", "--wasm", str(self.wasm))
        self.assertEqual(code, 5)
        self.assertIn("MISMATCH", out)
        self.assertFalse(self.gate_report(d)["post_deploy"]["matches_artifact"])
        with mock.patch("urllib.request.urlopen", FakeRpc(views={})):
            code, out, _ = run_cli("gate", str(d), "--verify-deployed", "demo.testnet", "--wasm", str(self.wasm),
                                   "--smoke", "get_n")
        self.assertEqual(code, 5)
        self.assertIn("FAILED", out)

    def test_cli_rpc_down_is_not_compared_not_a_mismatch(self):
        d = self.root / "work"
        d.mkdir()
        with mock.patch("urllib.request.urlopen", side_effect=OSError("[Errno 1] Operation not permitted")):
            code, out, err = run_cli("gate", str(d), "--verify-deployed", "demo.testnet", "--wasm", str(self.wasm),
                                     "--smoke", "get_n", "--dry-run")
        self.assertEqual(code, 5)
        self.assertNotIn("MISMATCH", out)
        self.assertIn("NOT COMPARED (the deployed code could not be read)", out)
        self.assertIn("FAILED (not verified: the RPC did not answer)", out)
        self.assertIn("could not fetch deployed code", err)

    def test_cli_merges_into_existing_gate_report(self):
        crate = self.crate()
        with mock.patch("urllib.request.urlopen", FakeRpc()):
            self.assertEqual(run_cli("gate", str(crate), "--wasm", str(self.wasm),
                                     "--simulate-report", str(self.report_file()), "--estimate-storage")[0], 0)
            before = self.gate_report(crate)
            self.assertEqual(run_cli("gate", str(crate), "--verify-deployed", "demo.testnet",
                                     "--wasm", str(self.wasm))[0], 0)
        after = self.gate_report(crate)
        self.assertEqual(after["post_deploy"]["matches_artifact"], True)
        for k in ("artifact", "simulate", "storage_estimate", "rs_sha256", "timestamp_utc"):
            self.assertEqual(after[k], before[k])
        self.assertEqual(set(after) - {"post_deploy"}, set(before) - {"post_deploy"})

    def test_cli_input_errors_exit_2_without_network(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            w = str(self.wasm)
            self.assertEqual(run_cli("gate", "--verify-deployed", "demo.testnet")[0], 2)                 # no wasm
            self.assertEqual(run_cli("gate", "--verify-deployed", "Bad Acct", "--wasm", w)[0], 2)
            self.assertEqual(run_cli("gate", "--verify-deployed", "a.testnet", "--wasm", w, "--smoke", "new")[0], 2)
            self.assertEqual(run_cli("gate", "--verify-deployed", "a.testnet", "--wasm", w, "--execute")[0], 2)
            self.assertEqual(run_cli("gate", "--verify-deployed", "a.testnet", "--wasm", "/nope.wasm")[0], 2)
            self.assertEqual(run_cli("gate")[0], 2)
            self.assertEqual(run_cli("gate", str(self.crate()), "--smoke", "get_n")[0], 2)


@unittest.skipUnless(os.environ.get("BUMM_GUARD_LIVE_RPC") == "1", "set BUMM_GUARD_LIVE_RPC=1 to call the public testnet RPC")
class LiveRpcTests(unittest.TestCase):
    def test_testnet_protocol_config(self):
        result = rpc_call(RPC_URLS["testnet"], "EXPERIMENTAL_protocol_config", {"finality": "final"})
        per_byte = int(result["runtime_config"]["storage_amount_per_byte"])
        print(f"\nLIVE storage_amount_per_byte (testnet) = {per_byte}")
        self.assertGreater(per_byte, 0)
        self.assertEqual(fetch_per_byte("testnet")[0], per_byte)


if __name__ == "__main__":
    unittest.main()
