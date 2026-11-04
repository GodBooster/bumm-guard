"""Gate on an account that already runs code: an upgrade needs a passing upgrade scenario
(docs/FORMATS.md §2a) from exactly the on-chain code to exactly the candidate wasm.

No test touches the network: ``urllib.request.urlopen`` is stubbed with a fake NEAR RPC node.
"""

import base64
import contextlib
import copy
import hashlib
import io
import json
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bumm_guard import cli
from bumm_guard.gate import REPORT_DIR, REPORT_NAME, deploy_command, find_upgrade_proof
from bumm_guard.storage import RpcError
from bumm_guard.verify import global_contract, onchain_code_sha256

FIXTURES = Path(__file__).parent / "fixtures" / "near"
OLD = b"\0asm\x01\0\0\0old-n06"
NEW = b"\0asm\x01\0\0\0new-n10"
OLD_SHA = hashlib.sha256(OLD).hexdigest()
NEW_SHA = hashlib.sha256(NEW).hexdigest()
MIGRATE = {"method": "migrate", "args": {}, "gas": "100 Tgas"}


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Node:
    """urlopen stub answering ``view_code`` like rpc.testnet.near.org (2026-11-03 shapes)."""

    def __init__(self, code=None, cause="NO_CONTRACT_CODE", down=False,
                 global_hash=None, global_account=None, account_down=False, account_cause=None):
        self.code, self.cause, self.down, self.calls = code, cause, down, []
        self.global_hash, self.global_account = global_hash, global_account
        self.account_down, self.account_cause = account_down, account_cause

    @staticmethod
    def _error(cause, p):
        return {"jsonrpc": "2.0", "id": "x", "error": {
            "name": "HANDLER_ERROR", "cause": {"name": cause, "info": {}}, "code": -32000,
            "message": "Server error", "data": f"{cause.lower()} for {p['params']['account_id']}"}}

    def __call__(self, req, timeout=None):
        p = json.loads(req.data)
        self.calls.append(p)
        if self.down:
            raise OSError("network down")
        rt = p["params"]["request_type"]
        assert p["method"] == "query" and rt in ("view_code", "view_account"), p
        if rt == "view_account":
            if self.account_down:
                raise OSError("network down")
            if self.account_cause:
                body = self._error(self.account_cause, p)
            else:  # shape of a real testnet reply; the global_* fields exist only on such accounts
                result = {"amount": "9996658206019321400000000", "block_hash": "B" * 44, "block_height": 276050083,
                          "code_hash": "9xkv" + "1" * 40, "locked": "0", "storage_paid_at": 0, "storage_usage": 207262}
                if self.global_hash:
                    result["global_contract_hash"] = self.global_hash
                if self.global_account:
                    result["global_contract_account_id"] = self.global_account
                body = {"jsonrpc": "2.0", "id": "x", "result": result}
        elif self.code is None:
            body = self._error(self.cause, p)
        else:
            body = {"jsonrpc": "2.0", "id": "x", "result": {"code_base64": base64.b64encode(self.code).decode()}}
        return io.BytesIO(json.dumps(body).encode())


def upgrade_scenario(name="with migrate", frm=OLD_SHA, to=NEW_SHA, passed=True, migrate=MIGRATE, after=True):
    step = {"index": 3, "kind": "upgrade", "by": "contract", "wasm": "new.wasm", "passed": passed,
            "expected": "ok", "actual": {"status": "success", "code_sha256": to, "gas_burnt_tgas": 16.7}}
    if migrate:
        step["method"] = migrate["method"]
        step["migrate"] = migrate
    steps = [step]
    if after:  # the proof needs something checked on the migrated state
        steps.append({"index": 4, "kind": "view", "method": "get_registered_count", "passed": True,
                      "expected": {"equals": 2}, "actual": {"status": "success", "value": 2}})
    return {"name": name, "passed": passed, "wasm_sha256": frm, "upgrade_wasm_sha256": to, "steps": steps}


def report(*scenarios):
    scen = list(scenarios)
    failed = sum(1 for s in scen if not s["passed"])
    ups = {s.get("upgrade_wasm_sha256") for s in scen if s.get("upgrade_wasm_sha256")}
    return {"tool": "bumm-guard", "wasm_sha256": OLD_SHA, "upgrade_wasm_sha256": ups.pop() if len(ups) == 1 else None,
            "scenarios": scen, "summary": {"scenarios": len(scen), "passed": len(scen) - failed, "failed": failed}}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.crate = self.root / "d1_safe"
        shutil.copytree(FIXTURES / "d1_safe", self.crate, ignore=shutil.ignore_patterns("target"))
        self.wasm = self.root / "new.wasm"
        self.wasm.write_bytes(NEW)


class UpgradeGateTests(Fixture):
    def gate(self, node, rep=None, *extra):
        args = ["gate", str(self.crate), "--wasm", str(self.wasm), "--account", "demo.testnet", *extra]
        if rep is not None:
            p = self.root / "sim.json"
            p.write_text(json.dumps(rep))
            args += ["--simulate-report", str(p)]
        with mock.patch("urllib.request.urlopen", node):
            return run_cli(*args)

    def gate_report(self):
        return json.loads((self.crate / REPORT_DIR / REPORT_NAME).read_text())

    def assertBlocked(self, result, *needles):
        code, out, _ = result
        self.assertEqual(code, 3, out)
        self.assertIn(f"DEPLOY BLOCKED: account demo.testnet already runs code {OLD_SHA}; this is an upgrade and "
                      f"no passing upgrade scenario proves {OLD_SHA} -> {NEW_SHA}", out)
        for n in needles:
            self.assertIn(n, out)
        self.assertNotIn("near contract deploy", out)
        self.assertFalse((self.crate / REPORT_DIR / REPORT_NAME).exists())

    # --- empty account: fresh deploy, as before

    def test_empty_account_is_a_fresh_deploy(self):
        node = Node(code=None)
        code, out, _ = self.gate(node)
        self.assertEqual(code, 0, out)
        self.assertIn("has no contract code: fresh deploy", out)
        self.assertIn("without-init-call", out)
        self.assertIsNone(self.gate_report()["upgrade"])
        self.assertEqual([c["params"]["request_type"] for c in node.calls], ["view_code", "view_account"])
        self.assertEqual([c["params"]["account_id"] for c in node.calls], ["demo.testnet"] * 2)

    def test_unknown_account_is_empty_too(self):
        code, out, _ = self.gate(Node(code=None, cause="UNKNOWN_ACCOUNT"))
        self.assertEqual(code, 0, out)
        self.assertIn("fresh deploy", out)

    # --- no code but a global contract (NEP-591): not empty

    GLOBAL = ("DEPLOY BLOCKED: demo.testnet uses a global contract (NEP-591): upgrades of global-contract "
              "accounts are not supported by this gate")

    def assertGlobalBlocked(self, result, detail):
        code, out, _ = result
        self.assertEqual(code, 3, out)
        self.assertIn(self.GLOBAL, out)
        self.assertIn(detail, out)
        self.assertNotIn("near contract deploy", out)
        self.assertNotIn("fresh deploy", out)
        self.assertFalse((self.crate / REPORT_DIR / REPORT_NAME).exists())

    def test_global_contract_by_hash_blocks(self):
        h = "H" * 43 + "1"
        self.assertGlobalBlocked(self.gate(Node(code=None, global_hash=h)), f"global contract hash {h}")

    def test_global_contract_by_account_blocks(self):
        self.assertGlobalBlocked(self.gate(Node(code=None, global_account="lib.testnet")),
                                 "global contract of account lib.testnet")

    def test_unknown_account_skips_global_check_or_treats_as_empty(self):
        code, out, _ = self.gate(Node(code=None, cause="UNKNOWN_ACCOUNT", account_cause="UNKNOWN_ACCOUNT"))
        self.assertEqual(code, 0, out)
        self.assertIn("fresh deploy", out)

    def test_view_account_failure_blocks(self):
        code, out, _ = self.gate(Node(code=None, account_down=True))
        self.assertEqual(code, 3, out)
        self.assertIn("DEPLOY BLOCKED: cannot read account demo.testnet", out)
        self.assertIn("global contract (NEP-591)", out)
        self.assertNotIn("near contract deploy", out)
        self.assertFalse((self.crate / REPORT_DIR / REPORT_NAME).exists())

    def test_fresh_deploy_with_upgrade_report_warns_not_blocks(self):
        rep = report(upgrade_scenario(frm=NEW_SHA, to="9" * 64))  # report bound to this wasm as the start code
        rep["wasm_sha256"] = NEW_SHA
        code, out, _ = self.gate(Node(code=None), rep)
        self.assertEqual(code, 0, out)
        self.assertIn("warning: simulate report has upgrade scenario(s) (with migrate) but demo.testnet has no code", out)

    # --- account with other code: upgrade

    def test_code_without_report_blocks(self):
        self.assertBlocked(self.gate(Node(code=OLD)), "--upgrade-wasm")

    def test_report_without_upgrade_scenario_blocks(self):
        rep = report({"name": "n06", "passed": True, "wasm_sha256": NEW_SHA, "upgrade_wasm_sha256": None, "steps": []})
        rep["wasm_sha256"] = NEW_SHA
        self.assertBlocked(self.gate(Node(code=OLD), rep), "no passing upgrade scenario for this pair")

    def test_proof_for_other_onchain_code_blocks(self):
        other = hashlib.sha256(b"something else").hexdigest()
        self.assertBlocked(self.gate(Node(code=OLD), report(upgrade_scenario(frm=other))),
                           f"proves {other} -> {NEW_SHA}")

    def test_proof_for_other_candidate_blocks(self):
        other = hashlib.sha256(b"another build").hexdigest()
        self.assertBlocked(self.gate(Node(code=OLD), report(upgrade_scenario(to=other))),
                           f"proves {OLD_SHA} -> {other}")

    def test_failed_upgrade_scenario_blocks(self):
        self.assertBlocked(self.gate(Node(code=OLD), report(upgrade_scenario(passed=False))), "did not pass")

    def test_passing_proof_with_migrate_prints_batch_call(self):
        code, out, _ = self.gate(Node(code=OLD), report(upgrade_scenario()))
        self.assertEqual(code, 0, out)
        want = shlex.join(["near", "contract", "deploy", "demo.testnet", "use-file", str(self.wasm),
                           "with-init-call", "migrate", "json-args", "{}", "prepaid-gas", "100 Tgas",
                           "attached-deposit", "0 NEAR", "network-config", "testnet", "sign-with-keychain", "send"])
        self.assertIn("  " + want, out)
        self.assertIn("NOT executed", out)
        self.assertIn("proved by passing scenario 'with migrate'", out)
        self.assertEqual(self.gate_report()["upgrade"], {
            "onchain_code_sha256": OLD_SHA, "candidate_sha256": NEW_SHA, "scenario": "with migrate",
            "migrate": {"method": "migrate", "args": {}, "gas": "100 Tgas"}, "passed": True,
            "post_upgrade_steps": [{"index": 4, "kind": "view", "method": "get_registered_count"}]})
        self.assertEqual(self.gate_report()["deploy"], {"status": "printed", "command": want})

    def test_upgrade_scenario_without_steps_after_upgrade_blocks(self):
        sc = upgrade_scenario(after=False)
        self.assertBlocked(self.gate(Node(code=OLD), report(sc)),
                           "has no steps after the upgrade: nothing was proven on the migrated state")

    def test_report_without_code_sha256_blocks(self):
        sc = upgrade_scenario()
        del sc["steps"][0]["actual"]["code_sha256"]  # report from a bumm-sim that did not record it
        self.assertBlocked(self.gate(Node(code=OLD), report(sc)), "report does not record the deployed code hash")

    def test_passing_code_only_proof_prints_without_init_call(self):
        code, out, _ = self.gate(Node(code=OLD), report(upgrade_scenario(name="code only", migrate=None)))
        self.assertEqual(code, 0, out)
        self.assertIn("without-init-call", out)
        self.assertNotIn("with-init-call", out)
        self.assertIn("code only (no migration call)", out)
        self.assertIsNone(self.gate_report()["upgrade"]["migrate"])

    def test_passing_proof_chosen_over_failing_negative_check(self):
        rep = report(upgrade_scenario(), upgrade_scenario(name="code only", migrate=None, passed=False))
        code, out, _ = self.gate(Node(code=OLD), rep)
        self.assertEqual(code, 0, out)
        self.assertIn("with-init-call migrate", out)
        self.assertIn("note: upgrade scenario 'code only' for this pair did not pass", out)
        self.assertIn("note: other scenario(s) in the report did not pass: code only", out)
        self.assertEqual(self.gate_report()["simulate"]["passed"], False)

    def test_migrate_args_and_gas_are_repeated(self):
        m = {"method": "migrate_v2", "args": {"owner": "alice.testnet", "cap": 5}, "gas": "2.5 Tgas"}
        code, out, _ = self.gate(Node(code=OLD), report(upgrade_scenario(migrate=m)), "--network", "mainnet")
        self.assertEqual(code, 0, out)
        self.assertIn("with-init-call migrate_v2 json-args '{\"cap\":5,\"owner\":\"alice.testnet\"}' "
                      "prepaid-gas '2.5 Tgas' attached-deposit '0 NEAR' network-config mainnet", out)

    def test_report_without_recorded_migrate_args_blocks(self):
        sc = upgrade_scenario()
        del sc["steps"][0]["migrate"]  # report from a bumm-sim that did not record migrate args
        self.assertBlocked(self.gate(Node(code=OLD), report(sc)), "re-run `bumm-guard simulate`")

    def test_same_code_already_deployed_is_no_change(self):
        code, out, _ = self.gate(Node(code=NEW))
        self.assertEqual(code, 0, out)
        self.assertIn(f"NO CHANGE: account demo.testnet (testnet) already runs this exact wasm ({NEW_SHA}); "
                      "nothing to deploy", out)
        self.assertNotIn("near contract deploy", out)
        self.assertNotIn("deploy command", out)
        self.assertIsNone(self.gate_report()["upgrade"])
        self.assertEqual(self.gate_report()["deploy"], {"status": "no-change", "command": None})

    def test_no_change_with_a_report_for_the_old_code_does_not_block(self):
        # after a proven upgrade the same report + the same wasm is run again: nothing to deploy, exit 0
        code, out, _ = self.gate(Node(code=NEW), report(upgrade_scenario()))
        self.assertEqual(code, 0, out)
        self.assertIn("NO CHANGE", out)
        self.assertIn("note: simulate report not checked against this wasm: nothing to deploy", out)
        self.assertNotIn("near contract deploy", out)
        self.assertIsNone(self.gate_report()["simulate"])

    def test_no_change_dry_run_writes_nothing(self):
        code, out, _ = self.gate(Node(code=NEW), None, "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("NO CHANGE", out)
        self.assertIn("dry-run: gate-report.json not written", out)
        self.assertFalse((self.crate / REPORT_DIR / REPORT_NAME).exists())

    def test_fresh_deploy_records_printed_command(self):
        code, out, _ = self.gate(Node(code=None))
        self.assertEqual(code, 0, out)
        dep = self.gate_report()["deploy"]
        self.assertEqual(dep["status"], "printed")
        self.assertIn("without-init-call", dep["command"])
        self.assertIn("  " + dep["command"], out)

    # --- (a) the upgrade transaction itself must have succeeded

    def test_upgrade_step_expected_to_fail_is_not_a_proof(self):
        sc = upgrade_scenario()  # scenario passes because the failing upgrade was what it expected
        sc["steps"][0]["expected"] = {"fail_contains": "MethodNotFound"}
        sc["steps"][0]["actual"] = {"status": "failure", "error": "MethodNotFound", "code_sha256": NEW_SHA}
        self.assertBlocked(self.gate(Node(code=OLD), report(sc)),
                           "upgrade scenario 'with migrate': the upgrade transaction did not succeed (status failure, "
                           'expected {"fail_contains": "MethodNotFound"}); a rolled-back upgrade proves nothing')

    def test_upgrade_step_failed_against_expectation_is_not_a_proof(self):
        sc = upgrade_scenario(passed=False)
        sc["steps"][0]["actual"]["status"] = "failure"
        self.assertBlocked(self.gate(Node(code=OLD), report(sc)),
                           "the upgrade transaction did not succeed (status failure); a rolled-back upgrade proves nothing")

    def test_upgrade_step_without_status_is_not_a_proof(self):
        sc = upgrade_scenario()
        del sc["steps"][0]["actual"]["status"]
        self.assertBlocked(self.gate(Node(code=OLD), report(sc)), "did not succeed (status None)")

    # --- (d) what was proved after the upgrade is listed; views only is a warning

    def test_post_upgrade_steps_listed_and_views_only_warns(self):
        code, out, _ = self.gate(Node(code=OLD), report(upgrade_scenario()))
        self.assertEqual(code, 0, out)
        self.assertIn("proved after upgrade (1 step(s)): view get_registered_count", out)
        self.assertIn("warning: only view calls were made on the migrated state; no state change was exercised", out)
        self.assertEqual(self.gate_report()["upgrade"]["post_upgrade_steps"],
                         [{"index": 4, "kind": "view", "method": "get_registered_count"}])

    def test_post_upgrade_call_step_silences_the_warning(self):
        sc = upgrade_scenario()
        sc["steps"] += [{"index": 5, "kind": "call", "method": "register", "by": "dave", "passed": True,
                         "expected": "ok", "actual": {"status": "success"}},
                        {"index": 6, "kind": "balance", "account": "contract", "passed": True,
                         "expected": {"at_least": "1 NEAR"}, "actual": {"status": "success", "value": "1"}},
                        {"index": 2, "kind": "call", "method": "register", "by": "alice", "passed": True}]
        code, out, _ = self.gate(Node(code=OLD), report(sc))
        self.assertEqual(code, 0, out)
        self.assertIn("proved after upgrade (3 step(s)): view get_registered_count, call register, balance contract", out)
        self.assertNotIn("only view calls", out)
        self.assertEqual([st["index"] for st in self.gate_report()["upgrade"]["post_upgrade_steps"]], [4, 5, 6])

    def test_rpc_down_blocks(self):
        code, out, _ = self.gate(Node(down=True))
        self.assertEqual(code, 3)
        self.assertIn("DEPLOY BLOCKED: cannot read the code on demo.testnet", out)

    def test_stale_report_removed_when_upgrade_blocked(self):
        rep_file = self.crate / REPORT_DIR / REPORT_NAME
        rep_file.parent.mkdir()
        rep_file.write_text("{}")
        self.assertBlocked(self.gate(Node(code=OLD)))

    def test_no_rpc_without_account_or_wasm(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            self.assertEqual(run_cli("gate", str(self.crate), "--wasm", str(self.wasm))[0], 0)
            code, out, _ = run_cli("gate", str(self.crate), "--account", "demo.testnet")
        self.assertEqual(code, 0)
        self.assertIn("warning: no --wasm: the code on demo.testnet was not checked", out)

    def test_findings_block_before_any_rpc(self):
        vuln = self.root / "d1_vuln"
        shutil.copytree(FIXTURES / "d1_vuln", vuln, ignore=shutil.ignore_patterns("target"))
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            code, out, _ = run_cli("gate", str(vuln), "--wasm", str(self.wasm), "--account", "demo.testnet")
        self.assertEqual(code, 3)
        self.assertIn("high/critical finding", out)


class McpGateDeployTests(Fixture):
    """``gate_deploy`` (MCP) runs the same logic; called directly, so the MCP SDK is not needed."""

    def call(self, node, rep=None, **kw):
        from bumm_guard import mcp_server
        args = {"crate_dir": str(self.crate), "wasm": str(self.wasm), "account": "demo.testnet", **kw}
        if rep is not None:
            p = self.root / "sim.json"
            p.write_text(json.dumps(rep))
            args["simulate_report"] = str(p)
        with mock.patch.dict("os.environ", {"BUMM_GUARD_ALLOW_ANY_PATH": "1"}), \
                mock.patch("urllib.request.urlopen", node):
            return mcp_server.gate_deploy(**args)

    def test_mcp_upgrade_without_proof_blocked(self):
        data = self.call(Node(code=OLD))
        self.assertTrue(data["blocked"])
        self.assertEqual(data["blocking_findings"], 0)
        self.assertIsNone(data["deploy_command"])
        self.assertIsNone(data["upgrade"])
        self.assertIn("this is an upgrade", " ".join(data["messages"]))
        self.assertIn("upgrade without a passing upgrade scenario", data["note"])

    def test_mcp_upgrade_with_proof(self):
        data = self.call(Node(code=OLD), report(upgrade_scenario()))
        self.assertTrue(data["passed"])
        self.assertFalse(data["deploy_executed"])
        self.assertIn("with-init-call migrate json-args '{}' prepaid-gas '100 Tgas'", data["deploy_command"])
        self.assertEqual(data["upgrade"]["scenario"], "with migrate")
        self.assertEqual(data["upgrade"]["post_upgrade_steps"], [{"index": 4, "kind": "view", "method": "get_registered_count"}])
        self.assertEqual(data["deploy"], {"status": "printed", "command": data["deploy_command"]})
    def test_mcp_no_change(self):
        data = self.call(Node(code=NEW))
        self.assertTrue(data["passed"])
        self.assertFalse(data["blocked"])
        self.assertIsNone(data["deploy_command"])
        self.assertEqual(data["deploy"], {"status": "no-change", "command": None})
        self.assertTrue(data["note"].startswith("NO CHANGE: the account already runs this exact wasm"))
        self.assertIn("NO CHANGE", " ".join(data["messages"]))

    def test_mcp_upgrade_check_proof_upgrade_failed(self):
        sc = upgrade_scenario()
        sc["steps"][0]["expected"] = {"fail_contains": "boom"}
        sc["steps"][0]["actual"]["status"] = "failure"
        data = self.call(Node(code=OLD), report(sc))
        self.assertTrue(data["blocked"])
        self.assertEqual(data["upgrade_check"]["status"], "proof-upgrade-failed")
        self.assertEqual(data["upgrade_check"]["proven_pairs"][0]["upgrade_status"], "failure")

    def test_mcp_simulate_report_path_checked(self):
        from bumm_guard import mcp_server
        with self.assertRaises(mcp_server.ToolInputError):
            mcp_server.gate_deploy(str(self.crate), simulate_report="/etc/passwd")


class HelperTests(unittest.TestCase):
    def test_onchain_code_sha256(self):
        with mock.patch("urllib.request.urlopen", Node(code=OLD)):
            self.assertEqual(onchain_code_sha256("a.testnet"), OLD_SHA)
        for cause in ("NO_CONTRACT_CODE", "UNKNOWN_ACCOUNT"):
            with mock.patch("urllib.request.urlopen", Node(code=None, cause=cause)):
                self.assertIsNone(onchain_code_sha256("a.testnet"))
        with mock.patch("urllib.request.urlopen", Node(code=None, cause="TIMEOUT_ERROR")):
            with self.assertRaises(RpcError):
                onchain_code_sha256("a.testnet")
        with mock.patch("urllib.request.urlopen", Node(down=True)):
            with self.assertRaises(RpcError):
                onchain_code_sha256("a.testnet")

    def test_find_upgrade_proof_rejects_empty_proofs(self):
        no_after = report(upgrade_scenario(after=False))
        proof, notes = find_upgrade_proof(no_after, OLD_SHA, NEW_SHA)
        self.assertIsNone(proof)
        self.assertEqual(notes, ["upgrade scenario 'with migrate' has no steps after the upgrade: "
                                 "nothing was proven on the migrated state"])
        sc = upgrade_scenario()
        sc["steps"][1]["index"] = 2  # earlier than the upgrade step: does not count
        self.assertIsNone(find_upgrade_proof(report(sc), OLD_SHA, NEW_SHA)[0])
        sc = upgrade_scenario()
        sc["steps"][1]["index"] = None  # no int index: does not count
        self.assertIsNone(find_upgrade_proof(report(sc), OLD_SHA, NEW_SHA)[0])
        sc = upgrade_scenario()
        del sc["steps"][0]["actual"]["code_sha256"]
        proof, notes = find_upgrade_proof(report(sc), OLD_SHA, NEW_SHA)
        self.assertIsNone(proof)
        self.assertEqual(notes, ["upgrade scenario 'with migrate': report does not record the deployed code hash "
                                 "(bumm-sim older than this gate): re-run `bumm-guard simulate`"])
        sc = upgrade_scenario()
        sc["steps"][0]["actual"]["code_sha256"] = "0" * 64
        self.assertIn("deployed code_sha256 differs from the candidate",
                      find_upgrade_proof(report(sc), OLD_SHA, NEW_SHA)[1][0])

    def test_global_contract(self):
        def probe(**kw):
            with mock.patch("urllib.request.urlopen", Node(code=None, **kw)):
                return global_contract("a.testnet")
        self.assertEqual(probe(global_hash="HASH"), "global contract hash HASH")
        self.assertEqual(probe(global_account="lib.testnet"), "global contract of account lib.testnet")
        self.assertEqual(probe(global_hash="HASH", global_account="lib.testnet"), "global contract hash HASH")
        self.assertIsNone(probe())
        self.assertIsNone(probe(account_cause="UNKNOWN_ACCOUNT"))
        with self.assertRaises(RpcError):
            probe(account_cause="TIMEOUT_ERROR")
        with self.assertRaises(RpcError):
            probe(account_down=True)
        malformed = io.BytesIO(json.dumps({"jsonrpc": "2.0", "id": "x", "result": "nope"}).encode())
        with mock.patch("urllib.request.urlopen", lambda req, timeout=None: malformed):
            with self.assertRaises(RpcError):
                global_contract("a.testnet")

    def test_deploy_command_shapes(self):
        self.assertEqual(deploy_command("a.testnet", "c.wasm", "testnet")[6], "without-init-call")
        cmd = deploy_command("a.testnet", "c.wasm", "testnet", {"method": "m", "args": {"b": 1, "a": 2}, "gas": None})
        self.assertEqual(cmd[6:14], ["with-init-call", "m", "json-args", '{"a":2,"b":1}', "prepaid-gas", "100 Tgas",
                                     "attached-deposit", "0 NEAR"])

    def test_find_upgrade_proof_ignores_non_upgrade_scenarios(self):
        rep = report({"name": "plain", "passed": True, "steps": [{"kind": "call"}]}, upgrade_scenario())
        proof, notes = find_upgrade_proof(rep, OLD_SHA, NEW_SHA)
        self.assertEqual(proof["scenario"], "with migrate")
        self.assertEqual(notes, [])
        two = report(upgrade_scenario(), upgrade_scenario(name="other", migrate={"method": "m2", "args": {}}))
        self.assertIn("different migrations", find_upgrade_proof(copy.deepcopy(two), OLD_SHA, NEW_SHA)[1][0])


if __name__ == "__main__":
    unittest.main()


class UpgradeCheckTests(Fixture):
    """``upgrade_check``: the structured reason for a refused upgrade (what gate_deploy returns to agents)."""

    def run_gate(self, rep=None):
        from bumm_guard.gate import run_gate
        kw = {}
        if rep is not None:
            p = self.root / "sim.json"
            p.write_text(json.dumps(rep))
            kw["simulate_report"] = str(p)
        with mock.patch("urllib.request.urlopen", Node(code=OLD)):
            return run_gate(str(self.crate), wasm=str(self.wasm), account="demo.testnet", dry_run=True, **kw)

    def test_no_report(self):
        out = self.run_gate()
        self.assertTrue(out.blocked)
        self.assertEqual(out.upgrade_check["status"], "no-report")
        self.assertEqual(out.upgrade_check["onchain_code_sha256"], OLD_SHA)
        self.assertEqual(out.upgrade_check["candidate_sha256"], NEW_SHA)
        self.assertEqual(out.upgrade_check["proven_pairs"], [])

    def test_report_proves_other_pair(self):
        other = hashlib.sha256(b"other").hexdigest()
        out = self.run_gate(report(upgrade_scenario(name="escrow", frm=other, to=NEW_SHA)))
        self.assertTrue(out.blocked)
        self.assertEqual(out.upgrade_check["status"], "report-proves-other-pair")
        self.assertEqual(out.upgrade_check["proven_pairs"],
                         [{"from": other, "to": NEW_SHA, "scenario": "escrow", "passed": True,
                           "upgrade_status": "success", "upgrade_succeeded": True}])

    def test_pair_present_but_failed(self):
        out = self.run_gate(report(upgrade_scenario(passed=False)))
        self.assertTrue(out.blocked)
        self.assertEqual(out.upgrade_check["status"], "no-proof")
        self.assertFalse(out.upgrade_check["proven_pairs"][0]["passed"])

    def test_absent_on_pass(self):
        out = self.run_gate(report(upgrade_scenario()))
        self.assertFalse(out.blocked)
        self.assertIsNone(out.upgrade_check)

    def test_pair_present_but_upgrade_transaction_failed(self):
        sc = upgrade_scenario()
        sc["steps"][0]["expected"] = {"fail_contains": "MethodNotFound"}
        sc["steps"][0]["actual"]["status"] = "failure"
        out = self.run_gate(report(sc))
        self.assertTrue(out.blocked)
        self.assertEqual(out.upgrade_check["status"], "proof-upgrade-failed")
        pair = out.upgrade_check["proven_pairs"][0]
        self.assertEqual((pair["passed"], pair["upgrade_status"], pair["upgrade_succeeded"]), (True, "failure", False))

    def test_failed_upgrade_next_to_a_passing_pair_is_still_no_proof(self):
        bad = upgrade_scenario(name="rolled back")
        bad["steps"][0]["actual"]["status"] = "failure"
        other = upgrade_scenario(name="other candidate", to="9" * 64)  # same on-chain code, other wasm
        out = self.run_gate(report(bad, other))
        self.assertTrue(out.blocked)
        self.assertEqual(out.upgrade_check["status"], "proof-upgrade-failed")
        good_but_unpassed = upgrade_scenario(passed=False)
        out = self.run_gate(report(bad, good_but_unpassed))
        self.assertEqual(out.upgrade_check["status"], "no-proof")  # a successful upgrade step exists for the pair


EVIDENCE = Path(__file__).parent.parent / "evidence"
PAIRS = {  # on-chain code -> candidate, as in evidence/UPGRADE_TESTNET.md
    "n06_to_n10": ("c3f50addfb067db8b9fcfc4b8dc6d1a6a197e66d392648bda372f7a7b3a3f617",
                   "8526195a51a5f5d94f15fab7df1c685804eb419a01dc1a734ac1c76d6220e079",
                   "upgrade-with-migrate-report-ci.json", "upgrade-code-only-report-ci.json",
                   "event_registration_waitlist"),
    "escrow_v1_to_v2": ("ecd8a805ea3252f1ffa2a285964d3e035d6e3c6be16e6974c33c479c3e034443",
                        "13425a042dec55eec364240e890a1645c7332f2898149353ebcb5830a5886013",
                        "escrow-upgrade-with-migrate-report-ci.json", "escrow-upgrade-code-only-report-ci.json",
                        "escrow_v2"),
}


class ShippedEvidenceTests(unittest.TestCase):
    """The CI reports committed under evidence/ must keep their verdicts: the with-migrate reports are
    accepted as proofs, the code-only reports are rejected. The wasm files are not in the repository,
    so the candidate hash is injected by stubbing ``sha256_file`` for the wasm path and the on-chain
    hash by stubbing ``onchain_code_sha256`` (no network)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wasm = Path(self.tmp.name) / "candidate.wasm"
        self.wasm.write_bytes(NEW)

    def gate(self, pair: str, report_name: str):
        from bumm_guard import gate as gate_mod
        onchain, candidate, _, _, crate = PAIRS[pair]
        real = gate_mod.sha256_file

        def fake_sha(path):
            return candidate if Path(path) == self.wasm else real(path)

        with mock.patch("bumm_guard.gate.sha256_file", fake_sha), \
                mock.patch("bumm_guard.gate.onchain_code_sha256", return_value=onchain), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            return run_cli("gate", str(FIXTURES / crate), "--wasm", str(self.wasm), "--account", "demo.testnet",
                           "--simulate-report", str(EVIDENCE / report_name), "--dry-run")

    def test_with_migrate_reports_are_proofs(self):
        for pair, (onchain, candidate, good, _, _) in PAIRS.items():
            with self.subTest(pair=pair):
                code, out, err = self.gate(pair, good)
                self.assertEqual(code, 0, out + err)
                self.assertIn(f"upgrade of demo.testnet: {onchain} -> {candidate}, proved by passing scenario", out)
                self.assertIn("migration migrate({}) in the same transaction", out)
                self.assertIn("proved after upgrade (", out)
                self.assertIn("call ", out.split("proved after upgrade (")[1].split("\n")[0])
                self.assertNotIn("only view calls", out)
                self.assertIn("migration migrate: #[private] (src/lib.rs:", out)
                self.assertIn("with-init-call migrate json-args '{}' prepaid-gas '100 Tgas'", out)

    def test_code_only_reports_are_rejected(self):
        for pair, (onchain, candidate, _, bad, _) in PAIRS.items():
            with self.subTest(pair=pair):
                code, out, err = self.gate(pair, bad)
                self.assertEqual(code, 3, out + err)
                self.assertIn(f"no passing upgrade scenario proves {onchain} -> {candidate}", out)
                self.assertIn("for this pair did not pass", out)
                self.assertNotIn("near contract deploy", out)
