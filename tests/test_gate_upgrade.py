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


MIGRATIONS = """
#[near]
impl Escrow {
    #[private]
    #[init(ignore_state)]
    pub fn migrate() -> Self { require!(env::predecessor_account_id() == env::current_account_id(), "only the contract itself may migrate"); env::panic_str("test stub") }

    #[private]
    #[init(ignore_state)]
    pub fn migrate_v2(owner: AccountId, cap: u32) -> Self { require!(env::predecessor_account_id() == env::current_account_id(), "only the contract itself may migrate"); env::panic_str("test stub") }
}
"""


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
        # a successful state-changing call on the migrated state (the gate refuses a proof with views only)
        steps.append({"index": 5, "kind": "call", "method": "register", "by": "bob", "passed": True,
                      "expected": "ok", "actual": {"status": "success", "value": None}})
    # the old code wrote state before the upgrade (the gate refuses a proof made on init-only state); kept
    # last in the list so that steps[0] stays the upgrade step for the tests that edit it
    steps.append({"index": 2, "kind": "call", "method": "register", "by": "alice", "passed": True,
                  "expected": "ok", "actual": {"status": "success", "value": None}})
    return {"name": name, "passed": passed, "wasm_sha256": frm, "upgrade_wasm_sha256": to, "steps": steps}


def report(*scenarios):
    scen = list(scenarios)
    failed = sum(1 for s in scen if not s["passed"])
    ups = {s.get("upgrade_wasm_sha256") for s in scen if s.get("upgrade_wasm_sha256")}
    return {"tool": "bumm-guard", "wasm_sha256": OLD_SHA, "upgrade_wasm_sha256": ups.pop() if len(ups) == 1 else None,
            "scenarios": scen, "summary": {"scenarios": len(scen), "passed": len(scen) - failed, "failed": failed,
                                           "steps": sum(len(s["steps"]) for s in scen),
                                           "steps_failed": sum(1 for s in scen for st in s["steps"] if not st["passed"])}}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.crate = self.root / "d1_safe"
        shutil.copytree(FIXTURES / "d1_safe", self.crate, ignore=shutil.ignore_patterns("target"))
        # the gate refuses a migration method it cannot find in the sources: give the copy two, with an explicit caller check (the crate builds with near-sdk 5.17.2, where #[private] on #[init] is not enforced)
        with open(self.crate / "src" / "lib.rs", "a", encoding="utf-8") as f:
            f.write(MIGRATIONS)
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
            "post_upgrade_steps": [{"index": 4, "kind": "view", "method": "get_registered_count"},
                                   {"index": 5, "kind": "call", "method": "register"}]})
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

    # --- (c) migrate args must not name scenario aliases

    def assertAliasBlocked(self, result, *needles):
        code, out, _ = result
        self.assertEqual(code, 3, out)
        self.assertIn("proved by passing scenario", out)  # the proof was accepted, then the args were refused
        self.assertIn("DEPLOY BLOCKED: migrate args of scenario 'with migrate' reference sandbox-only alias(es)", out)
        for n in needles:
            self.assertIn(n, out)
        self.assertIn("use literal account ids in the scenario's migrate args", out)
        self.assertNotIn("near contract deploy", out)
        self.assertFalse((self.crate / REPORT_DIR / REPORT_NAME).exists())

    def test_migrate_args_with_recorded_alias_block(self):
        sc = upgrade_scenario(migrate={"method": "migrate", "args": {"owner": "alice"}, "gas": "100 Tgas"})
        sc["accounts"] = {"alice": "alice.test.near", "contract": "contract.test.near"}
        self.assertAliasBlocked(self.gate(Node(code=OLD), report(sc)), "'alice'")

    def test_migrate_args_nested_alias_blocks(self):
        m = {"method": "migrate", "args": {"cfg": {"admins": ["x.testnet", "bob"], "n": 1}}, "gas": "100 Tgas"}
        sc = upgrade_scenario(migrate=m)
        sc["accounts"] = {"alice": "alice.test.near", "bob": "bob.test.near", "contract": "contract.test.near"}
        self.assertAliasBlocked(self.gate(Node(code=OLD), report(sc)), "'bob'")

    def test_migrate_args_contract_alias_blocks_even_without_recorded_accounts(self):
        m = {"method": "migrate", "args": {"owner": "contract"}, "gas": "100 Tgas"}
        self.assertAliasBlocked(self.gate(Node(code=OLD), report(upgrade_scenario(migrate=m))), "'contract'")

    def test_migrate_args_fallback_to_signers_when_accounts_not_recorded(self):
        m = {"method": "migrate", "args": {"owner": "carol"}, "gas": "100 Tgas"}
        sc = upgrade_scenario(migrate=m)
        sc["steps"].insert(0, {"index": 0, "kind": "call", "method": "register", "by": "carol", "passed": True,
                               "expected": "ok", "actual": {"status": "success"}})
        self.assertAliasBlocked(self.gate(Node(code=OLD), report(sc)), "'carol'")

    def test_migrate_args_with_literal_ids_pass(self):
        m = {"method": "migrate", "args": {"owner": "alice.testnet", "note": "alice is not an alias here"},
             "gas": "100 Tgas"}
        sc = upgrade_scenario(migrate=m)
        sc["accounts"] = {"alice": "alice.test.near", "contract": "contract.test.near"}
        code, out, _ = self.gate(Node(code=OLD), report(sc))
        self.assertEqual(code, 0, out)
        self.assertIn("with-init-call migrate", out)

    # --- (d) what was proved after the upgrade is listed; views only is a warning

    def test_post_upgrade_steps_listed(self):
        code, out, _ = self.gate(Node(code=OLD), report(upgrade_scenario()))
        self.assertEqual(code, 0, out)
        self.assertIn("proved after upgrade (2 step(s)): view get_registered_count, call register", out)
        self.assertNotIn("only view calls", out)
        self.assertEqual(self.gate_report()["upgrade"]["post_upgrade_steps"],
                         [{"index": 4, "kind": "view", "method": "get_registered_count"},
                          {"index": 5, "kind": "call", "method": "register"}])

    def test_views_only_after_the_upgrade_is_refused(self):
        sc = upgrade_scenario()
        sc["steps"] = [st for st in sc["steps"] if st["index"] != 5]   # only the view after the upgrade
        self.assertBlocked(self.gate(Node(code=OLD), report(sc)), "makes no successful call after the upgrade")

    def test_proof_without_state_written_before_the_upgrade_is_blocked(self):
        sc = upgrade_scenario()
        sc["steps"] = [st for st in sc["steps"] if st["index"] >= 3]   # init -> upgrade -> view, nothing written
        self.assertBlocked(self.gate(Node(code=OLD), report(sc)), "makes no successful call before the upgrade")
        refused = upgrade_scenario()
        refused["steps"][-1].update(expected={"fail_contains": "Event is full"},
                                    actual={"status": "failure", "error": "Event is full"})
        self.assertBlocked(self.gate(Node(code=OLD), report(refused)), "makes no successful call before the upgrade")

    def test_forged_passed_flag_on_a_synthetic_report_is_blocked(self):
        sc = upgrade_scenario()
        sc["steps"][1].update(passed=False, actual={"status": "failure", "error": "Cannot deserialize"})
        rep = report(sc)                              # summary adds up: one step failed ...
        rep["summary"].update(failed=0, passed=1)     # ... but the verdict was edited
        self.assertBlocked(self.gate(Node(code=OLD), rep), "step 4 did not pass")

    def test_post_upgrade_steps_of_every_kind_are_listed(self):
        sc = upgrade_scenario()
        sc["steps"] += [{"index": 6, "kind": "balance", "account": "contract", "passed": True,
                         "expected": {"at_least": "1 NEAR"}, "actual": {"status": "success", "value": "1"}}]
        code, out, _ = self.gate(Node(code=OLD), report(sc))
        self.assertEqual(code, 0, out)
        self.assertIn("proved after upgrade (3 step(s)): view get_registered_count, call register, balance contract", out)
        self.assertEqual([st["index"] for st in self.gate_report()["upgrade"]["post_upgrade_steps"]], [4, 5, 6])

    # --- the proof must be one upgrade, on readable stored state, with a successful call, optionally pinned

    def after_step(self, index, method, kind="call", passed=True, expected="ok", **actual):
        return {"index": index, "kind": kind, "method": method, "by": "alice", "passed": passed,
                "expected": expected, "actual": actual}

    def test_two_upgrade_steps_are_refused(self):
        sc = upgrade_scenario()
        again = copy.deepcopy(sc["steps"][0])
        again["index"] = 6
        sc["steps"].append(again)
        self.assertBlocked(self.gate(Node(code=OLD), report(sc)), "has 2 upgrade steps")

    def test_expected_state_read_failure_after_the_upgrade_is_refused(self):
        sc = upgrade_scenario()
        sc["steps"].append(self.after_step(6, "get_registrant", expected={"fail_contains": "Cannot deserialize element"},
                                           status="failure", error="Smart contract panicked: Cannot deserialize element"))
        self.assertBlocked(self.gate(Node(code=OLD), report(sc)), "could not read stored state",
                           "step 6 (call get_registrant)")

    def test_expected_json_argument_failure_after_the_upgrade_is_fine(self):
        sc = upgrade_scenario()
        sc["steps"].append(self.after_step(6, "register", expected={"fail_contains": "Failed to deserialize input"},
                                           status="failure",
                                           error="Smart contract panicked: Failed to deserialize input from JSON."))
        code, out, _ = self.gate(Node(code=OLD), report(sc))
        self.assertEqual(code, 0, out)

    def test_repeated_migration_call_failing_on_the_new_layout_is_fine(self):
        sc = upgrade_scenario()
        sc["steps"].append(self.after_step(6, "migrate", expected={"fail_contains": "Cannot deserialize"},
                                           status="failure",
                                           error="Smart contract panicked: Cannot deserialize the contract state."))
        code, out, _ = self.gate(Node(code=OLD), report(sc))
        self.assertEqual(code, 0, out)
        self.assertIn("with-init-call migrate", out)

    def test_only_a_failed_call_after_the_upgrade_is_refused(self):
        sc = upgrade_scenario()
        sc["steps"][2].update(expected={"fail_contains": "full"}, actual={"status": "failure", "error": "Event is full"})
        self.assertBlocked(self.gate(Node(code=OLD), report(sc)), "makes no successful call after the upgrade")

    SCENARIO_HASH = hashlib.sha256(b"scenario file the owner approved").hexdigest()

    def pinned_report(self, recorded=None):
        sc = upgrade_scenario()
        sc["scenario_sha256"] = recorded or self.SCENARIO_HASH
        return report(sc)

    def test_scenario_pin_match_is_accepted_and_printed(self):
        code, out, _ = self.gate(Node(code=OLD), self.pinned_report(), "--scenario-sha256", self.SCENARIO_HASH)
        self.assertEqual(code, 0, out)
        self.assertIn(f"scenario pin: {self.SCENARIO_HASH} (matches)", out)
        self.assertNotIn("no scenario pin", out)

    def test_no_scenario_pin_is_noted(self):
        code, out, _ = self.gate(Node(code=OLD), self.pinned_report())
        self.assertEqual(code, 0, out)
        self.assertIn("note: no scenario pin (--scenario-sha256): the proof is whatever scenario the report's "
                      "author chose to run", out)

    def test_scenario_pin_mismatch_is_refused(self):
        other = hashlib.sha256(b"another file").hexdigest()
        self.assertBlocked(self.gate(Node(code=OLD), self.pinned_report(other), "--scenario-sha256", self.SCENARIO_HASH),
                           f"has scenario_sha256 {other}, not the pinned {self.SCENARIO_HASH}")
        sc = upgrade_scenario()  # a report that records no hash cannot match a pin
        self.assertBlocked(self.gate(Node(code=OLD), report(sc), "--scenario-sha256", self.SCENARIO_HASH),
                           "not the pinned")

    def test_malformed_scenario_pin_exits_2(self):
        for bad in ("abc", self.SCENARIO_HASH.upper(), self.SCENARIO_HASH + "0", ""):
            with self.subTest(pin=bad):
                code, out, err = self.gate(Node(code=OLD), self.pinned_report(), "--scenario-sha256", bad)
                self.assertEqual(code, 2, out)
                self.assertIn("--scenario-sha256 must be 64 lowercase hex", err)
                self.assertNotIn("near contract deploy", out)

    def test_missing_wasm_with_account_exits_2(self):
        node = Node(code=OLD)
        with mock.patch("urllib.request.urlopen", node):
            code, out, err = run_cli("gate", str(self.crate), "--wasm", str(self.root / "nope.wasm"),
                                     "--account", "demo.testnet")
        self.assertEqual(code, 2)
        self.assertIn("wasm file not found", err)
        self.assertNotIn("near contract deploy", out)
        self.assertEqual(node.calls, [])

    def test_failing_scenario_with_the_same_migration_outvotes_the_proof(self):
        rep = report(upgrade_scenario(), upgrade_scenario(name="same migration, fails", passed=False))
        code, out, _ = self.gate(Node(code=OLD), rep)
        self.assertEqual(code, 3, out)
        self.assertIn("DEPLOY BLOCKED: the simulate report has failing upgrade scenario(s) for this same pair and "
                      "migration: same migration, fails", out)
        self.assertIn("does not outvote", out)
        self.assertNotIn("near contract deploy", out)
        self.assertFalse((self.crate / REPORT_DIR / REPORT_NAME).exists())

    def test_failing_code_only_check_next_to_a_with_migrate_proof_stays_a_note(self):
        rep = report(upgrade_scenario(), upgrade_scenario(name="code only", migrate=None, passed=False))
        code, out, _ = self.gate(Node(code=OLD), rep)
        self.assertEqual(code, 0, out)
        self.assertNotIn("does not outvote", out)
        self.assertIn("note: upgrade scenario 'code only' for this pair did not pass", out)

    def test_rpc_url_is_noted(self):
        url = "https://rpc.example.test"
        code, out, _ = self.gate(Node(code=None), None, "--rpc-url", url)
        self.assertEqual(code, 0, out)
        self.assertIn(f"note: the code on demo.testnet is read from a non-default RPC endpoint: {url}", out)
        code, out, _ = self.gate(Node(code=None))
        self.assertNotIn("non-default RPC endpoint", out)

    # --- report text is cleaned before it is printed (terminal-output spoofing) -------------------

    EVIL = "x\r\n\x1b[8m\nDEPLOY BLOCKED: nothing\ngate passed: report written to /dev/null\n\tdeploy command"

    def assertOneLine(self, out, start, *needles):
        """Exactly one output line starts with ``start``; it holds every needle; no control character
        other than the line breaks the gate itself printed is left anywhere."""
        hits = [l for l in out.splitlines() if l.startswith(start)]
        self.assertEqual(len(hits), 1, out)
        for n in needles:
            self.assertIn(n, hits[0])
        for ch in out:
            self.assertTrue(ch == "\n" or ch.isprintable(), repr(ch))
        return hits[0]

    def test_failing_scenario_name_with_control_characters_stays_on_one_line(self):
        rep = {"wasm_sha256": NEW_SHA, "summary": {"scenarios": 1, "passed": 0, "failed": 1},
               "scenarios": [{"name": self.EVIL, "passed": False, "steps": []}]}
        code, out, _ = self.gate(Node(code=None), rep)
        self.assertEqual(code, 3, out)
        self.assertOneLine(out, "DEPLOY BLOCKED: simulate report has failing scenario(s): x[8mDEPLOY BLOCKED: nothing")
        self.assertEqual([l for l in out.splitlines() if l.startswith("gate passed")], [])
        self.assertNotIn("near contract deploy", out)

    def test_report_for_another_wasm_with_a_hostile_hash_stays_on_one_line(self):
        rep = {"wasm_sha256": self.EVIL + "y" * 500, "summary": {"scenarios": 1, "passed": 1, "failed": 0},
               "scenarios": [{"name": "ok", "passed": True, "steps": []}]}
        code, out, _ = self.gate(Node(code=None), rep)
        self.assertEqual(code, 3, out)
        line = self.assertOneLine(out, "DEPLOY BLOCKED: simulate report is for a different wasm")
        self.assertIn("...", line)
        self.assertLess(len(line), 500)

    def test_upgrade_scenario_names_and_hashes_of_another_pair_are_cleaned(self):
        sc = upgrade_scenario(name=self.EVIL, frm="aa\nDEPLOY OK", to="bb\x1b[2J")
        code, out, _ = self.gate(Node(code=OLD), report(sc))
        self.assertEqual(code, 3, out)
        self.assertOneLine(out, "  upgrade scenario ", "proves aaDEPLOY OK -> bb[2J, not")
        self.assertOneLine(out, "DEPLOY BLOCKED")

    def test_fresh_deploy_warning_lists_cleaned_upgrade_scenario_names(self):
        code, out, _ = self.gate(Node(code=None), report(upgrade_scenario(name=self.EVIL, frm=NEW_SHA)))
        self.assertOneLine(out, "warning: simulate report has upgrade scenario(s) (x[8mDEPLOY BLOCKED: nothing")

    def test_passing_upgrade_with_hostile_names_and_steps_prints_clean_lines(self):
        good = upgrade_scenario(name="good\nDEPLOY BLOCKED: fake")
        good["steps"].append({"index": 6, "kind": "call\x1b[8m", "method": "register\nDEPLOY BLOCKED: fake2",
                              "passed": True, "expected": "ok", "actual": {"status": "success"}})
        bad = {"name": self.EVIL, "passed": False, "steps": []}
        code, out, _ = self.gate(Node(code=OLD), report(good, bad))
        self.assertEqual(code, 0, out)
        self.assertOneLine(out, "upgrade of demo.testnet", "'goodDEPLOY BLOCKED: fake'")
        self.assertOneLine(out, "proved after upgrade (3 step(s))", "call[8m registerDEPLOY BLOCKED: fake2")
        self.assertOneLine(out, "note: other scenario(s) in the report did not pass: x[8mDEPLOY BLOCKED: nothing")
        self.assertEqual([l for l in out.splitlines() if l.startswith("DEPLOY BLOCKED")], [])
        up = self.gate_report()["upgrade"]
        self.assertEqual(up["scenario"], "goodDEPLOY BLOCKED: fake")
        self.assertEqual(up["post_upgrade_steps"][2], {"index": 6, "kind": "call[8m",
                                                       "method": "registerDEPLOY BLOCKED: fake2"})

    def test_long_scenario_name_is_cut(self):
        code, out, _ = self.gate(Node(code=OLD), report(upgrade_scenario(name="n" * 5000)))
        self.assertEqual(code, 0, out)
        self.assertIn("proved by passing scenario '" + "n" * 200 + "...'", out)
        self.assertEqual(self.gate_report()["upgrade"]["scenario"], "n" * 200 + "...")

    def test_migration_method_with_control_characters_is_not_a_proof(self):
        for migrate in ({"method": "migrate\n  near contract deploy evil.testnet", "args": {}, "gas": "100 Tgas"},
                        {"method": "migrate", "args": {}, "gas": "100 Tgas\nsend"},
                        {"method": "m" * 300, "args": {}, "gas": None},
                        {"method": "", "args": {}, "gas": None},
                        {"method": "migrate", "args": {}, "gas": 100}):
            with self.subTest(migrate=migrate):
                result = self.gate(Node(code=OLD), report(upgrade_scenario(migrate=migrate)))
                self.assertBlocked(result, "is not plain text")
                self.assertOneLine(result[1], "DEPLOY BLOCKED")

    def test_alias_scenario_is_found_by_its_cleaned_name(self):
        sc = upgrade_scenario(name="up\ngrade", migrate={"method": "migrate", "args": {"owner": "alice"}, "gas": None})
        sc["accounts"] = {"alice": "10 NEAR"}
        code, out, _ = self.gate(Node(code=OLD), report(sc))
        self.assertEqual(code, 3, out)
        self.assertOneLine(out, "DEPLOY BLOCKED: migrate args of scenario 'upgrade' reference sandbox-only", "'alice'")

    def test_rpc_down_blocks(self):
        code, out, _ = self.gate(Node(down=True))
        self.assertEqual(code, 3)
        self.assertIn("DEPLOY BLOCKED: cannot read the code on demo.testnet", out)

    def test_rpc_failure_is_marked_on_the_outcome(self):
        # review-job reads this mark to tell "target unreadable" from a verdict on the submission
        from bumm_guard.gate import run_gate
        for node, text in ((Node(down=True), "cannot read the code on demo.testnet (testnet)"),
                           (Node(code=None, account_down=True), "cannot read account demo.testnet (testnet)")):
            with mock.patch("urllib.request.urlopen", node):
                g = run_gate(str(self.crate), wasm=str(self.wasm), account="demo.testnet", dry_run=True)
            self.assertEqual((g.exit_code, g.blocked), (3, True))
            self.assertTrue(g.rpc_error.startswith(text), g.rpc_error)
        with mock.patch("urllib.request.urlopen", Node(code=OLD)):
            g = run_gate(str(self.crate), wasm=str(self.wasm), account="demo.testnet", dry_run=True)
        self.assertEqual((g.exit_code, g.rpc_error), (3, None))

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


class StateDigestTests(Fixture):
    """The state digest and the access probe of the simulate report (docs/FORMATS.md section 3): optional;
    a report without them is judged by the older rules."""

    D_INIT = {"keys": 1, "bytes": 40, "sha256": "a" * 64}
    D_UP = {"keys": 3, "bytes": 120, "sha256": "b" * 64}

    def proof(self, init="default", before="default", probe=None, **kw):
        sc = upgrade_scenario(**kw)
        init = self.D_INIT if init == "default" else init
        before = self.D_UP if before == "default" else before
        if init is not None:
            sc["state_after_init"] = init
        if before is not None:
            sc["steps"][0]["state_before"] = before
        if probe is not None:
            sc["steps"][0]["access_probe"] = probe
        return report(sc)

    def find(self, rep):
        return find_upgrade_proof(rep, OLD_SHA, NEW_SHA)

    def gate(self, rep):
        p = self.root / "sim.json"
        p.write_text(json.dumps(rep))
        args = ["gate", str(self.crate), "--wasm", str(self.wasm), "--account", "demo.testnet",
                "--simulate-report", str(p)]
        with mock.patch("urllib.request.urlopen", Node(code=OLD)):
            return run_cli(*args)

    def gate_report(self):
        return json.loads((self.crate / REPORT_DIR / REPORT_NAME).read_text())

    def test_equal_digests_are_not_a_proof(self):
        proof, notes = self.find(self.proof(before=dict(self.D_INIT)))
        self.assertIsNone(proof)
        self.assertIn("identical to the state right after init", " ".join(notes))
        code, out, _ = self.gate(self.proof(before=dict(self.D_INIT)))
        self.assertEqual(code, 3, out)
        self.assertIn("identical to the state right after init", out)
        self.assertNotIn("near contract deploy", out)

    def test_different_digests_are_accepted_and_recorded(self):
        proof, _ = self.find(self.proof())
        self.assertEqual(proof["state_at_upgrade"], {"keys": 3, "bytes": 120, "changed_since_init": True})
        self.assertNotIn("access_probe", proof)
        code, out, _ = self.gate(self.proof())
        self.assertEqual(code, 0, out)
        self.assertIn("state at the upgrade: 3 key(s), 120 byte(s), changed since init", out)
        self.assertEqual(self.gate_report()["upgrade"]["state_at_upgrade"],
                         {"keys": 3, "bytes": 120, "changed_since_init": True})

    def test_malformed_digest_is_ignored_and_the_older_rule_applies(self):
        bad = [{"keys": 3, "bytes": 120}, {"keys": 3, "bytes": 120, "sha256": "z" * 64},
               {"keys": 3, "bytes": 120, "sha256": "B" * 64}, {"keys": 3, "bytes": 120, "sha256": "b" * 63},
               {"keys": "3", "bytes": 120, "sha256": "b" * 64}, {"keys": True, "bytes": 120, "sha256": "b" * 64},
               {"keys": 3, "bytes": 1.5, "sha256": "b" * 64}, "nope", 7, []]
        for b in bad:
            with self.subTest(before=b):
                # equal sha256 would refuse if the digest were read; a malformed one is as if absent
                proof, _ = self.find(self.proof(before=b))
                self.assertIsNotNone(proof)
                self.assertNotIn("state_at_upgrade", proof)
            with self.subTest(init=b):
                proof, _ = self.find(self.proof(init=b, before=dict(self.D_INIT)))
                self.assertIsNotNone(proof)
                self.assertNotIn("state_at_upgrade", proof)
        # the older rule still applies: no successful call before the upgrade
        sc = upgrade_scenario()
        sc["steps"] = [st for st in sc["steps"] if st["index"] >= 3]
        sc["state_after_init"], sc["steps"][0]["state_before"] = self.D_INIT, {"keys": "x"}
        proof, notes = self.find(report(sc))
        self.assertIsNone(proof)
        self.assertIn("makes no successful call before the upgrade", " ".join(notes))

    def test_only_one_digest_is_ignored(self):
        for init, before in ((self.D_INIT, None), (None, self.D_UP), (None, None)):
            with self.subTest(init=init, before=before):
                proof, _ = self.find(self.proof(init=init, before=before))
                self.assertIsNotNone(proof)
                self.assertNotIn("state_at_upgrade", proof)
        proof, _ = self.find(self.proof(init=self.D_INIT, before=None))
        self.assertNotIn("state_at_upgrade", proof)
        code, out, _ = self.gate(self.proof(init=self.D_INIT, before=None))
        self.assertEqual(code, 0, out)
        self.assertNotIn("state at the upgrade", out)

    def test_probe_success_is_refused_even_when_the_step_passed(self):
        rep = self.proof(probe={"method": "migrate", "by": "bumm-sim-access-probe-not-an-alias.test.near",
                                "status": "success"})
        self.assertTrue(rep["scenarios"][0]["steps"][0]["passed"])
        proof, notes = self.find(rep)
        self.assertIsNone(proof)
        self.assertIn("accepted a call from another account", " ".join(notes))
        code, out, _ = self.gate(rep)
        self.assertEqual(code, 3, out)
        self.assertIn("accepted a call from another account", out)
        self.assertNotIn("near contract deploy", out)

    def test_probe_success_is_refused_without_digests_too(self):
        proof, notes = self.find(self.proof(init=None, before=None, probe={"method": "migrate", "by": "x", "status": "success"}))
        self.assertIsNone(proof)
        self.assertIn("accepted a call from another account", " ".join(notes))

    def test_probe_not_run_counts_as_absent(self):
        proof, _ = self.find(self.proof(probe={"method": "migrate", "by": "x", "status": "not-run"}))
        self.assertIsNotNone(proof)
        self.assertNotIn("access_probe", proof)
        for bad in ("maybe", None, 3):
            with self.subTest(status=bad):
                proof, _ = self.find(self.proof(probe={"method": "migrate", "by": "x", "status": bad}))
                self.assertNotIn("access_probe", proof)

    def test_probe_failure_is_recorded_with_cleaned_text(self):
        proof, _ = self.find(self.proof(probe={"method": "migrate", "by": "p.test.near", "status": "failure",
                                               "error": "Method migrate is private"}))
        self.assertEqual(proof["access_probe"], {"method": "migrate", "by": "p.test.near", "status": "failure",
                                                 "error": "Method migrate is private", "same_state": None,
                                                 "control_status": None})

    def test_control_status_is_success_failure_or_none(self):
        for given, want in (("success", "success"), ("failure", "failure"), ("not-run", None), ("ok", None),
                            (None, None), (7, None)):
            with self.subTest(control_status=given):
                probe = {"method": "migrate", "by": "p", "status": "failure", "control_status": given,
                         "control_error": "ignored by the proof"}
                proof, _ = self.find(self.proof(probe=probe))
                self.assertIs(proof["access_probe"]["control_status"], want)
        proof, _ = self.find(self.proof(probe={"method": "migrate", "by": "p", "status": "failure"}))
        self.assertIsNone(proof["access_probe"]["control_status"])

    def test_probe_failure_with_any_text_is_recorded(self):
        for err in ("only owner", "Smart contract panicked: no", None):
            with self.subTest(error=err):
                probe = {"method": "migrate", "by": "p", "status": "failure"}
                if err is not None:
                    probe["error"] = err
                proof, _ = self.find(self.proof(probe=probe))
                self.assertEqual(proof["access_probe"]["status"], "failure")
                self.assertEqual(proof["access_probe"]["error"], err)

    def test_same_state_is_true_false_or_none(self):
        base = {"method": "migrate", "by": "p", "status": "failure", "error": "e"}
        cases = ((dict(base, state_sha256=self.D_UP["sha256"]), "default", True),
                 (dict(base, state_sha256="c" * 64), "default", False),
                 (dict(base), "default", None),                    # the probe has no digest
                 (dict(base, state_sha256=7), "default", None),    # not a string
                 (dict(base, state_sha256="c" * 64), None, None))  # the step has no state_before
        for i, (probe, before, want) in enumerate(cases):
            with self.subTest(i=i):
                proof, _ = self.find(self.proof(probe=probe, before=before))
                self.assertIs(proof["access_probe"]["same_state"], want)

    def test_same_state_is_none_when_the_step_digest_is_malformed(self):
        probe = {"method": "migrate", "by": "p", "status": "failure", "state_sha256": "b" * 64}
        proof, _ = self.find(self.proof(probe=probe, before={"keys": 3, "bytes": 120}))
        self.assertIsNone(proof["access_probe"]["same_state"])

    def test_probe_success_note_names_the_cause(self):
        rep = self.proof(probe={"method": "migrate", "by": "p", "status": "success"})
        _, notes = self.find(rep)
        text = " ".join(notes)
        self.assertIn("accepted a call from another account", text)
        self.assertIn("not yet migrated", text)
        self.assertIn("before 5.18.0", text)

    def test_probe_without_error_has_error_none(self):
        proof, _ = self.find(self.proof(probe={"method": "migrate", "by": "p", "status": "failure"}))
        self.assertEqual(proof["access_probe"]["error"], None)

    def test_report_without_the_new_fields_has_the_old_proof_shape(self):
        proof, _ = self.find(report(upgrade_scenario()))
        self.assertEqual(set(proof), {"onchain_code_sha256", "candidate_sha256", "scenario", "migrate", "passed",
                                      "post_upgrade_steps"})
        code, out, _ = self.gate(report(upgrade_scenario()))
        self.assertEqual(code, 0, out)
        self.assertNotIn("state at the upgrade", out)
        self.assertNotIn("access probe", out)
        self.assertNotIn("access_probe", self.gate_report().get("migration_guard") or {})

    def test_hostile_probe_text_comes_out_as_one_clean_line(self):
        evil = "x\r\n\x1b[8m\nDEPLOY BLOCKED: nothing\ngate passed"
        probe = {"method": "migrate", "by": evil + "b" * 500, "status": "failure",
                 "error": "Method migrate is private " + evil + "e" * 500}
        proof, _ = self.find(self.proof(probe=probe))
        for key in ("by", "error"):
            v = proof["access_probe"][key]
            self.assertLessEqual(len(v), 203)   # TEXT_MAX (200) plus the "..." of a cut
            self.assertTrue(v.endswith("..."), v)
            self.assertTrue(v.isprintable(), repr(v))
        rep = self.proof(probe=dict(probe, status="success"))
        code, out, _ = self.gate(rep)
        self.assertEqual(code, 3, out)
        hits = [l for l in out.splitlines() if "accepted a call from another account" in l]
        self.assertEqual(len(hits), 1, out)
        for ch in out:
            self.assertTrue(ch == "\n" or ch.isprintable(), repr(ch))
        self.assertFalse(any(l.startswith(("DEPLOY BLOCKED: nothing", "gate passed")) for l in out.splitlines()), out)

    def test_mcp_gate_deploy_carries_the_new_lines(self):
        from bumm_guard import mcp_server
        probe = {"method": "migrate", "by": "p.test.near", "status": "failure", "error": "Method migrate is private",
                 "state_sha256": self.D_UP["sha256"], "control_status": "success"}
        p = self.root / "sim.json"
        p.write_text(json.dumps(self.proof(probe=probe)))
        with mock.patch.dict("os.environ", {"BUMM_GUARD_ALLOW_ANY_PATH": "1"}), \
                mock.patch("urllib.request.urlopen", Node(code=OLD)):
            data = mcp_server.gate_deploy(crate_dir=str(self.crate), wasm=str(self.wasm), account="demo.testnet",
                                          simulate_report=str(p))
        self.assertTrue(data["passed"], data["messages"])
        self.assertIn("state at the upgrade: 3 key(s), 120 byte(s), changed since init", data["messages"])
        self.assertIn("access probe: migrate sent by another account to the new code on the not yet migrated "
                      "state was refused (Method migrate is private); the same call from the contract account "
                      "went through", data["messages"])
        self.assertEqual(data["upgrade"]["state_at_upgrade"]["keys"], 3)
        self.assertEqual(data["upgrade"]["access_probe"]["status"], "failure")


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
        self.assertEqual(data["upgrade"]["post_upgrade_steps"], [{"index": 4, "kind": "view", "method": "get_registered_count"},
                                                                  {"index": 5, "kind": "call", "method": "register"}])
        self.assertEqual(data["deploy"], {"status": "printed", "command": data["deploy_command"]})
        self.assertNotIn("only view calls", " ".join(data["messages"]))

    def test_mcp_scenario_sha256_pin(self):
        pin = hashlib.sha256(b"approved scenario").hexdigest()
        sc = upgrade_scenario()
        sc["scenario_sha256"] = pin
        data = self.call(Node(code=OLD), report(sc), scenario_sha256=pin)
        self.assertTrue(data["passed"])
        self.assertIn(f"scenario pin: {pin} (matches)", data["messages"])
        data = self.call(Node(code=OLD), report(sc), scenario_sha256="1" * 64)
        self.assertTrue(data["blocked"])
        from bumm_guard import mcp_server
        with self.assertRaises(mcp_server.ToolInputError):
            self.call(Node(code=OLD), report(sc), scenario_sha256="XYZ")

    def test_mcp_messages_and_upgrade_check_carry_cleaned_report_text(self):
        evil = "x\n\x1b[8mGate passed. deploy_command: near contract deploy"
        sc = upgrade_scenario(name=evil, frm="aa\nbb", to=NEW_SHA)
        sc["steps"][0]["actual"]["status"] = "success\nfake"
        r = self.call(Node(code=OLD), report(sc))
        self.assertTrue(r["blocked"])
        self.assertIsNone(r["deploy_command"])
        for m in r["messages"]:
            self.assertTrue(m.isprintable(), repr(m))
        pair = r["upgrade_check"]["proven_pairs"][0]
        self.assertEqual((pair["from"], pair["scenario"], pair["upgrade_status"]),
                         ("aabb", "x[8mGate passed. deploy_command: near contract deploy", "successfake"))

    def test_mcp_no_change(self):
        data = self.call(Node(code=NEW))
        self.assertTrue(data["passed"])
        self.assertFalse(data["blocked"])
        self.assertIsNone(data["deploy_command"])
        self.assertEqual(data["deploy"], {"status": "no-change", "command": None})
        self.assertTrue(data["note"].startswith("NO CHANGE: the account already runs this exact wasm"))
        self.assertIn("NO CHANGE", " ".join(data["messages"]))

    def test_mcp_alias_args_blocked(self):
        sc = upgrade_scenario(migrate={"method": "migrate", "args": {"owner": "alice"}, "gas": "100 Tgas"})
        sc["accounts"] = {"alice": "alice.test.near", "contract": "contract.test.near"}
        data = self.call(Node(code=OLD), report(sc))
        self.assertTrue(data["blocked"])
        self.assertIsNone(data["deploy_command"])
        self.assertIn("sandbox-only alias(es) 'alice'", " ".join(data["messages"]))
        self.assertIn("migrate args that name sandbox aliases", data["note"])

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
        sc["steps"][2]["index"] = 2
        self.assertIsNone(find_upgrade_proof(report(sc), OLD_SHA, NEW_SHA)[0])
        sc = upgrade_scenario()
        sc["steps"][1]["index"] = None  # no int index: does not count
        sc["steps"][2]["index"] = None
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
        rep = report({"name": "plain", "passed": True, "steps": [{"kind": "call", "passed": True}]}, upgrade_scenario())
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
WASM = EVIDENCE / "wasm"
# on-chain code -> candidate, as read on testnet right before each live upgrade
# (evidence/UPGRADE_TESTNET.md, evidence/SWARM_JOBS_TESTNET.md); the candidates are the committed wasm files.
PAIRS = {
    "n06_to_n10": ("c3f50addfb067db8b9fcfc4b8dc6d1a6a197e66d392648bda372f7a7b3a3f617",
                   "8526195a51a5f5d94f15fab7df1c685804eb419a01dc1a734ac1c76d6220e079",
                   "upgrade-with-migrate-report-ci.json", "upgrade-code-only-report-ci.json",
                   "event_registration_waitlist"),
    "escrow_v1_to_v2": ("ecd8a805ea3252f1ffa2a285964d3e035d6e3c6be16e6974c33c479c3e034443",
                        "13425a042dec55eec364240e890a1645c7332f2898149353ebcb5830a5886013",
                        "escrow-upgrade-with-migrate-report-ci.json", "escrow-upgrade-code-only-report-ci.json",
                        "escrow_v2"),
    "swarm_jobs_v1_to_v2": ("bcace58397f2ee597dd5687187ea9e178e3cd5405dfdb810553214e416a9a92b",
                            "5dbcf8cd767c096c3e207e8c804e5f950bc5460de8be0e6e4132426009d33ba5",
                            "swarm-jobs-upgrade-with-migrate-report-ci.json",
                            "swarm-jobs-upgrade-code-only-report-ci.json",
                            "swarm_jobs_v2"),
}


class ShippedEvidenceTests(unittest.TestCase):
    """Offline replay of the gate on the committed evidence: the real committed wasm (evidence/wasm/), the
    CI reports committed under evidence/ and the fixture crates. Only the on-chain read is replaced: the
    account's code hash is the old-version hash recorded on testnet before each live upgrade, and any
    network use fails the test. The with-migrate reports must be accepted as proofs of exactly that pair,
    the code-only reports must be rejected."""

    def gate(self, pair: str, report_name: str):
        onchain, _, _, _, crate = PAIRS[pair]
        with mock.patch("bumm_guard.gate.onchain_code_sha256", return_value=onchain), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            return run_cli("gate", str(FIXTURES / crate), "--wasm", str(WASM / f"{crate}.wasm"),
                           "--account", "demo.testnet", "--simulate-report", str(EVIDENCE / report_name),
                           "--dry-run")

    def test_committed_wasm_are_the_candidates(self):
        for pair, (_, candidate, _, _, crate) in PAIRS.items():
            with self.subTest(pair=pair):
                self.assertEqual(hashlib.sha256((WASM / f"{crate}.wasm").read_bytes()).hexdigest(), candidate)

    def test_with_migrate_reports_are_proofs_that_the_source_check_then_blocks(self):
        """The committed reports prove the pair, but the three fixture crates have ``#[private]`` on an
        ``#[init]`` method with near-sdk 5.17.2 (no caller check is generated) and the committed reports
        have no access probe: the gate refuses the upgrade (``private-unenforced``)."""
        for pair, (onchain, candidate, good, _, crate) in PAIRS.items():
            with self.subTest(pair=pair):
                code, out, err = self.gate(pair, good)
                self.assertEqual(code, 3, out + err)
                self.assertIn(f"upgrade of demo.testnet: {onchain} -> {candidate}, proved by passing scenario", out)
                self.assertIn("migration migrate({}) in the same transaction", out)
                self.assertIn("proved after upgrade (", out)
                self.assertIn("call ", out.split("proved after upgrade (")[1].split("\n")[0])
                self.assertNotIn("only view calls", out)
                self.assertIn("DEPLOY BLOCKED: migration method migrate in src/lib.rs:", out)
                self.assertIn("has #[private] on an #[init] method, but this crate builds with near-sdk 5.17.2: "
                              "before 5.18.0 near-sdk generates no caller check for init methods, so any account "
                              "can call it; the report has no access probe that says otherwise", out)
                self.assertIn("  fix: make `require!(env::predecessor_account_id() == env::current_account_id(), ", out)
                self.assertNotIn("near contract deploy", out)
                self.assertNotIn("with-init-call", out)

    def test_code_only_reports_are_rejected(self):
        for pair, (onchain, candidate, _, bad, _) in PAIRS.items():
            with self.subTest(pair=pair):
                code, out, err = self.gate(pair, bad)
                self.assertEqual(code, 3, out + err)
                self.assertIn(f"no passing upgrade scenario proves {onchain} -> {candidate}", out)
                self.assertIn("for this pair did not pass", out)
                self.assertNotIn("near contract deploy", out)

    def forged(self, pair: str, edit) -> Path:
        """A copy of the committed code-only report of ``pair`` after ``edit`` (in a temp dir)."""
        _, _, _, bad, _ = PAIRS[pair]
        rep = json.loads((EVIDENCE / bad).read_text())
        edit(rep)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = Path(tmp) / bad
        path.write_text(json.dumps(rep))
        return path

    def test_forged_passed_flag_on_a_code_only_report_is_blocked(self):
        def flip(rep):
            rep["scenarios"][0]["passed"] = True
        for pair in PAIRS:
            with self.subTest(pair=pair):
                code, out, err = self.gate(pair, self.forged(pair, flip))
                self.assertEqual(code, 3, out + err)
                self.assertIn("says it passed, but step", out)
                self.assertIn("did not pass: the report contradicts itself", out)
                self.assertNotIn("near contract deploy", out)

    def test_forged_step_flags_and_verdict_are_blocked(self):
        def flip_all(rep):  # every flag says passed, the chain's answers (failures) are left as they were
            sc = rep["scenarios"][0]
            sc["passed"] = True
            for st in sc["steps"]:
                st["passed"] = True
            rep["summary"].update(passed=1, failed=0, steps_failed=0)
        for pair in PAIRS:
            with self.subTest(pair=pair):
                code, out, err = self.gate(pair, self.forged(pair, flip_all))
                self.assertEqual(code, 3, out + err)
                self.assertIn("is marked passed but its status is failure", out)
                self.assertNotIn("near contract deploy", out)

    def test_forged_summary_is_blocked(self):
        """A with-migrate report whose summary no longer adds up to its scenarios is not a proof."""
        _, _, good, _, _ = PAIRS["escrow_v1_to_v2"]
        rep = json.loads((EVIDENCE / good).read_text())
        rep["summary"]["steps_failed"] = 3
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / good).write_text(json.dumps(rep))
        code, out, err = self.gate("escrow_v1_to_v2", tmp / good)
        self.assertEqual(code, 3, out + err)
        self.assertIn("summary.steps_failed is 3 but the scenarios show 0", out)


OFFICIAL = Path(__file__).parent / "fixtures" / "near_official"


def git_blob_id(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class OfficialExampleEvidenceTests(unittest.TestCase):
    """The third-party pairs built from the official near-sdk-rs example examples/versioned
    (tests/fixtures/near_official/NOTICE.md), sandbox only. The "on-chain" hash is the old wasm the
    sandbox ran. A passing code-only report is a proof without any migrate call; a failing one is not."""

    V0, PLAIN, NEW = (hashlib.sha256((WASM / f"{n}.wasm").read_bytes()).hexdigest()
                      for n in ("versioned_v0", "versioned_plain", "versioned"))

    def gate(self, onchain: str, report: Path):
        with mock.patch("bumm_guard.gate.onchain_code_sha256", return_value=onchain), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            return run_cli("gate", str(OFFICIAL / "versioned"), "--wasm", str(WASM / "versioned.wasm"),
                           "--account", "demo.testnet", "--simulate-report", str(report), "--dry-run")

    def test_vendored_example_is_the_upstream_files(self):
        # git blob ids of examples/versioned at near-sdk-rs 7476cbe4 (tag near-sdk-v5.17.2)
        want = {"Cargo.toml": "e0f8f6076728d0b36f5ba2f4f2afb19da16c67bd",
                "README.md": "8219ffd667a56c0bebe40cb6fdd2909dc995a6a5",
                ".cargo/config.toml": "dadf1d355a9f4576ab064e3e4be787a1dccc741d",
                "src/lib.rs": "7b3d55e1249294d52c99046954bcb179ced95c5f"}
        files = sorted(p.relative_to(OFFICIAL / "versioned").as_posix()
                       for p in (OFFICIAL / "versioned").rglob("*") if p.is_file() and ".bumm-guard" not in p.parts)
        self.assertEqual(files, sorted(want))
        for name, blob in want.items():
            with self.subTest(file=name):
                self.assertEqual(git_blob_id(OFFICIAL / "versioned" / name), blob)
        self.assertEqual((OFFICIAL / "UPSTREAM_COMMIT").read_text().strip(), "7476cbe4328571322094f9e463f1dc9602933cb3")

    def test_reports_are_bound_to_the_committed_wasm(self):
        for name, old in (("official-versioned-v0-upgrade-report-ci.json", self.V0),
                          ("official-versioned-plain-upgrade-report-ci.json", self.PLAIN)):
            with self.subTest(report=name):
                rep = json.loads((EVIDENCE / name).read_text())
                self.assertEqual((rep["wasm_sha256"], rep["upgrade_wasm_sha256"]), (old, self.NEW))

    def test_v0_report_is_a_code_only_proof(self):
        code, out, err = self.gate(self.V0, EVIDENCE / "official-versioned-v0-upgrade-report-ci.json")
        self.assertEqual(code, 0, out + err)
        self.assertIn(f"upgrade of demo.testnet: {self.V0} -> {self.NEW}, proved by passing scenario", out)
        self.assertIn("proved after upgrade (10 step(s))", out)
        self.assertNotIn("migration migrate", out)
        self.assertIn("use-file", out)
        self.assertIn("without-init-call", out)

    def test_plain_struct_report_is_refused(self):
        code, out, err = self.gate(self.PLAIN, EVIDENCE / "official-versioned-plain-upgrade-report-ci.json")
        self.assertEqual(code, 3, out + err)
        self.assertIn(f"no passing upgrade scenario proves {self.PLAIN} -> {self.NEW}", out)
        self.assertNotIn("near contract deploy", out)

    def test_v0_proof_does_not_cover_the_plain_struct_code(self):
        code, out, err = self.gate(self.PLAIN, EVIDENCE / "official-versioned-v0-upgrade-report-ci.json")
        self.assertEqual(code, 3, out + err)
        self.assertNotIn("near contract deploy", out)

    def test_forged_plain_struct_report_is_refused(self):
        rep = json.loads((EVIDENCE / "official-versioned-plain-upgrade-report-ci.json").read_text())
        rep["scenarios"][0]["passed"] = True
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / "forged.json").write_text(json.dumps(rep))
        code, out, err = self.gate(self.PLAIN, tmp / "forged.json")
        self.assertEqual(code, 3, out + err)
        self.assertIn("the report contradicts itself (edited?)", out)
