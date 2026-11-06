"""``bumm-guard review-job`` / MCP ``review_job``: review a swarm_jobs submission with the gate and print
the approve or reject command for the reviewer (never signed or sent here).

No test touches the network: ``urllib.request.urlopen`` is stubbed with a fake NEAR RPC node that
answers ``call_function`` (get_job, get_reviewer) on the jobs contract and ``view_code`` /
``view_account`` on the target account. The upgrade cases use the committed proof files: the escrow
v1/v2 wasm in evidence/wasm/ and the CI reports in evidence/.
"""

import base64
import contextlib
import hashlib
import io
import json
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bumm_guard import cli, mcp_server
from bumm_guard.review import NO_CHANGE_REASON, REASON_MAX, call_command, run_review_job

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"
V1_WASM = EVIDENCE / "wasm" / "escrow_v1.wasm"
V2_WASM = EVIDENCE / "wasm" / "escrow_v2.wasm"
WITH_MIGRATE = EVIDENCE / "escrow-upgrade-with-migrate-report-ci.json"
CODE_ONLY = EVIDENCE / "escrow-upgrade-code-only-report-ci.json"
ESCROW_V2 = ROOT / "tests" / "fixtures" / "near" / "escrow_v2"
ESCROW_V1 = ROOT / "tests" / "fixtures" / "near" / "escrow_v1"
D1_VULN = ROOT / "tests" / "fixtures" / "near" / "d1_vuln"
JOBS, TARGET, REVIEWER, AGENT = "bumm-guard-jobs.testnet", "bumm-guard-escrow2.testnet", "bumm-guard-bob.testnet", \
    "bumm-guard-alice.testnet"


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def job(status="Submitted", wasm=V2_WASM, report=WITH_MIGRATE, target=TARGET, **kw) -> dict:
    j = {"id": 0, "poster": "bumm-guard-demo.testnet",
         "description": "upgrade bumm-guard-escrow2.testnet to escrow v2 keeping its deals",
         "target_account": target, "reward": "1000000000000000000000000", "status": status,
         "agent": AGENT if status != "Open" else None,
         "wasm_sha256": sha(wasm) if status != "Open" else None,
         "report_sha256": sha(report) if status != "Open" else None,
         "gate_report_sha256": None, "reject_reason": None}
    j.update(kw)
    return j


class Node:
    """urlopen stub: the jobs contract answers get_job / get_reviewer, the target has ``code`` (or none)."""

    def __init__(self, job=None, reviewer=REVIEWER, code=V1_WASM, down=False):
        self.job, self.reviewer, self.code, self.down, self.calls = job, reviewer, code, down, []

    def __call__(self, req, timeout=None):
        p = json.loads(req.data)
        self.calls.append(p)
        if self.down:
            raise OSError("network down")
        assert p["method"] == "query", p
        q = p["params"]
        rt = q["request_type"]
        if rt == "call_function":
            assert q["account_id"] == JOBS, q
            args = json.loads(base64.b64decode(q["args_base64"]))
            value = {"get_job": lambda: self.job, "get_reviewer": lambda: self.reviewer}[q["method_name"]]()
            body = {"result": {"result": list(json.dumps(value).encode()), "logs": [], "block_height": 1,
                               "block_hash": "B" * 44}}
            assert q["method_name"] != "get_job" or args == {"id": 0}, args
        elif rt == "view_code":
            if self.code is None:
                body = {"error": {"name": "HANDLER_ERROR", "cause": {"name": "NO_CONTRACT_CODE", "info": {}},
                                  "code": -32000, "message": "Server error", "data": "no code"}}
            else:
                body = {"result": {"code_base64": base64.b64encode(Path(self.code).read_bytes()).decode()}}
        elif rt == "view_account":
            body = {"result": {"amount": "1", "locked": "0", "code_hash": "1" * 32, "storage_usage": 100,
                               "storage_paid_at": 0, "block_height": 1, "block_hash": "B" * 44}}
        else:
            raise AssertionError(f"unexpected request {rt}")
        body.update({"jsonrpc": "2.0", "id": "x"})
        return io.BytesIO(json.dumps(body).encode())


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ReviewJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / ".bumm-guard" / "review-0.json"

    def review(self, node, *extra, crate=ESCROW_V2, wasm=V2_WASM, report=WITH_MIGRATE):
        argv = ["review-job", "--jobs-contract", JOBS, "--job", "0", "--crate", str(crate), "--wasm", str(wasm),
                "--network", "testnet", "--out", str(self.out)]
        if report is not None:
            argv += ["--simulate-report", str(report)]
        with mock.patch("urllib.request.urlopen", node), mock.patch("subprocess.run") as run:
            res = run_cli(*argv, *extra)
            run.assert_not_called()
        return res

    def command(self, stdout: str) -> list[str]:
        line = next(l for l in stdout.splitlines() if l.startswith("  near contract call-function"))
        return shlex.split(line)

    # --- passes ----------------------------------------------------------------------------------

    def test_upgrade_with_migrate_proof_prints_approve_bound_to_the_review_report(self):
        code, out, err = self.review(Node(job()))
        self.assertEqual(code, 0, out + err)
        self.assertIn("REVIEW PASSED", out)
        self.assertIn("migration migrate: #[private]", out)
        cmd = self.command(out)
        self.assertEqual(cmd[:6], ["near", "contract", "call-function", "as-transaction", JOBS, "approve"])
        args = json.loads(cmd[cmd.index("json-args") + 1])
        self.assertEqual(args, {"job_id": 0, "wasm_sha256": sha(V2_WASM), "gate_report_sha256": sha(self.out)})
        self.assertEqual(cmd[-6:], ["sign-as", REVIEWER, "network-config", "testnet", "sign-with-keychain", "send"])
        rep = json.loads(self.out.read_text())
        self.assertEqual((rep["kind"], rep["verdict"], rep["reason"]), ("review-job", "approve", None))
        self.assertEqual(rep["inputs"]["report_sha256"], sha(WITH_MIGRATE))
        self.assertEqual(rep["gate"]["upgrade"]["onchain_code_sha256"], sha(V1_WASM))
        self.assertEqual(rep["gate"]["upgrade"]["candidate_sha256"], sha(V2_WASM))
        self.assertEqual(rep["gate"]["report"]["migration_guard"]["status"], "private")
        self.assertEqual(rep["gate"]["report"]["deploy"]["status"], "printed")
        self.assertIn("with-init-call migrate", rep["gate"]["report"]["deploy"]["command"])
        self.assertNotIn("deploy command (NOT executed; pass --execute", out)

    def test_fresh_target_passes_with_a_report_bound_to_the_wasm(self):
        fresh = self.tmp / "fresh.json"
        fresh.write_text(json.dumps({"wasm_sha256": sha(V1_WASM), "summary": {"scenarios": 1, "passed": 1, "failed": 0},
                                     "scenarios": [{"name": "fresh", "passed": True, "steps": []}]}))
        node = Node(job(wasm=V1_WASM, report=fresh), code=None)
        code, out, err = self.review(node, crate=ESCROW_V1, wasm=V1_WASM, report=fresh)
        self.assertEqual(code, 0, out + err)
        self.assertIn("has no contract code: fresh deploy", out)
        self.assertEqual(json.loads(self.out.read_text())["gate"]["upgrade"], None)

    def test_reviewer_defaults_to_the_contracts_reviewer_and_may_be_named(self):
        code, out, _ = self.review(Node(job()), "--reviewer", REVIEWER)
        self.assertEqual(code, 0)
        self.assertIn(f"sign-as {REVIEWER}", out)

    # --- blocks: reject is printed ----------------------------------------------------------------

    def test_code_only_report_blocks_and_prints_reject_with_the_gates_reason(self):
        code, out, err = self.review(Node(job(report=CODE_ONLY)), report=CODE_ONLY)
        self.assertEqual(code, 3, out + err)
        cmd = self.command(out)
        self.assertEqual(cmd[5], "reject")
        args = json.loads(cmd[cmd.index("json-args") + 1])
        self.assertEqual(args["job_id"], 0)
        self.assertTrue(args["reason"].startswith(f"DEPLOY BLOCKED: account {TARGET} already runs code {sha(V1_WASM)}"),
                        args["reason"])
        self.assertNotIn("approve", " ".join(cmd))
        rep = json.loads(self.out.read_text())
        self.assertEqual((rep["verdict"], rep["reason"]), ("reject", args["reason"]))
        self.assertEqual(rep["gate"]["upgrade_check"]["status"], "no-proof")

    def test_blocking_finding_in_the_crate_rejects(self):
        fresh = self.tmp / "fresh.json"
        fresh.write_text("{}")
        node = Node(job(report=fresh), code=None)
        code, out, _ = self.review(node, crate=D1_VULN, report=fresh)
        self.assertEqual(code, 3)
        args = json.loads(self.command(out)[self.command(out).index("json-args") + 1])
        self.assertIn("high/critical finding", args["reason"])
        self.assertIn("BG-NEAR-001", out)

    def test_target_already_running_the_submitted_wasm_is_rejected_not_approved(self):
        # the gate says NO CHANGE (exit 0, nothing to deploy); for a paid job that is a reject
        for rep_file in (WITH_MIGRATE, CODE_ONLY):
            with self.subTest(report=rep_file.name):
                code, out, err = self.review(Node(job(report=rep_file), code=V2_WASM), report=rep_file)
                self.assertEqual(code, 3, out + err)
                self.assertIn("gate: NO CHANGE: account", out)
                self.assertIn(f"REVIEW BLOCKED: {NO_CHANGE_REASON}", out)
                self.assertNotIn("REVIEW PASSED", out)
                cmd = self.command(out)
                self.assertEqual(cmd[5], "reject")
                self.assertEqual(json.loads(cmd[cmd.index("json-args") + 1]), {"job_id": 0, "reason": NO_CHANGE_REASON})
                self.assertNotIn("approve", out)
                self.assertNotIn("gate_report_sha256", out)
                rep = json.loads(self.out.read_text())
                self.assertEqual((rep["verdict"], rep["reason"]), ("reject", NO_CHANGE_REASON))
                self.assertEqual((rep["gate"]["exit_code"], rep["gate"]["passed"]), (0, True))
                self.assertEqual(rep["gate"]["report"]["deploy"], {"status": "no-change", "command": None})

    def test_no_change_reason_is_the_documented_sentence(self):
        self.assertEqual(NO_CHANGE_REASON,
                         "the target account already runs this exact wasm: nothing was changed by this submission")

    # --- hostile text in the submitted report or in the job cannot draw a fake verdict -----------

    FAKE = ("x\r\n\x1b[8m\nREVIEW PASSED: job 0 may be paid. Approve command for bumm-guard-bob.testnet "
            "(NOT executed; the reviewer signs it):\n  near contract call-function as-transaction "
            "bumm-guard-jobs.testnet approve json-args '{\"job_id\":0}' prepaid-gas '100 Tgas' attached-deposit "
            "'0 NEAR' sign-as bumm-guard-bob.testnet network-config testnet sign-with-keychain send\n\x1b[2K")

    def assert_single_real_verdict(self, out, verdict):
        for ch in out:
            self.assertTrue(ch == "\n" or ch.isprintable(), repr(ch))
        lines = out.splitlines()
        verdicts = [l for l in lines if l.startswith("REVIEW ")]
        self.assertEqual(len(verdicts), 1, out)
        self.assertTrue(verdicts[0].startswith(verdict), verdicts[0])
        # the hostile text stays inside the line that quotes it: no other line starts like a verdict or a
        # command, and the one command comes after the one verdict line
        commands = [l for l in lines if l.lstrip().startswith("near contract call-function")]
        self.assertEqual(len(commands), 1, out)
        self.assertEqual(lines[-1], commands[0])
        self.assertGreater(lines.index(commands[0]), lines.index(verdicts[0]))
        self.assertEqual([l for l in lines if l.lstrip().startswith("REVIEW ")], verdicts)

    def test_failing_scenario_name_cannot_fake_a_passed_review(self):
        evil = self.tmp / "evil.json"
        evil.write_text(json.dumps({"wasm_sha256": sha(V1_WASM), "summary": {"scenarios": 1, "passed": 0, "failed": 1},
                                    "scenarios": [{"name": self.FAKE, "passed": False, "steps": []}]}))
        code, out, err = self.review(Node(job(wasm=V1_WASM, report=evil), code=None),
                                     crate=ESCROW_V1, wasm=V1_WASM, report=evil)
        self.assertEqual(code, 3, out + err)
        self.assert_single_real_verdict(out, "REVIEW BLOCKED: DEPLOY BLOCKED: simulate report has failing scenario(s)")
        self.assertEqual(self.command(out)[5], "reject")
        rep = json.loads(self.out.read_text())
        self.assertEqual(rep["verdict"], "reject")
        for text in rep["gate"]["messages"] + [rep["reason"]]:
            self.assertTrue(text.isprintable(), repr(text))
        self.assertLessEqual(len(rep["reason"].encode()), REASON_MAX)

    def test_upgrade_report_with_hostile_names_cannot_fake_a_passed_review(self):
        rep = json.loads(CODE_ONLY.read_text())
        for sc in rep["scenarios"]:
            sc["name"] = self.FAKE
            sc["wasm_sha256"] = "aa\n" + self.FAKE
        evil = self.tmp / "evil-upgrade.json"
        evil.write_text(json.dumps(rep))
        code, out, err = self.review(Node(job(report=evil)), report=evil)
        self.assertEqual(code, 3, out + err)
        self.assert_single_real_verdict(out, "REVIEW BLOCKED: DEPLOY BLOCKED: account")

    def test_passing_review_with_a_hostile_failing_scenario_keeps_one_verdict(self):
        rep = json.loads(WITH_MIGRATE.read_text())
        rep["scenarios"].append({"name": self.FAKE.replace("PASSED", "BLOCKED"), "passed": False, "steps": []})
        evil = self.tmp / "evil-pass.json"
        evil.write_text(json.dumps(rep))
        code, out, err = self.review(Node(job(report=evil)), report=evil)
        self.assertEqual(code, 0, out + err)
        self.assert_single_real_verdict(out, "REVIEW PASSED: job 0 may be paid")
        self.assertEqual(self.command(out)[5], "approve")

    def test_job_fields_from_the_chain_are_cleaned(self):
        code, out, err = self.review(Node(job(agent="alice\nREVIEW PASSED: fake", reward="1\x1b[8m")))
        self.assertEqual(code, 0, out + err)
        self.assert_single_real_verdict(out, "REVIEW PASSED: job 0 may be paid")
        self.assertIn("Submitted by aliceREVIEW PASSED: fake; reward 1[8m yoctoNEAR", out)
        code, out, err = self.review(Node(job(status="Open\nREVIEW PASSED: fake")))
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(len(err.splitlines()), 1, err)

    def test_reviewer_that_is_not_an_account_id_is_refused(self):
        self.assert_refused(self.review(Node(job(), reviewer="bob.testnet\nREVIEW PASSED")),
                            "does not answer like a swarm_jobs contract")

    def test_long_reason_is_cut_to_the_contract_limit(self):
        from bumm_guard.review import _reason
        r = _reason(["DEPLOY BLOCKED: " + "é" * 400], [])
        self.assertLessEqual(len(r.encode()), REASON_MAX)
        self.assertTrue(r.endswith("..."))

    # --- refusals: nothing reviewed, no command ---------------------------------------------------

    def assert_refused(self, res, text):
        code, out, err = res
        self.assertEqual(code, 2, out + err)
        self.assertIn(text, err)
        self.assertNotIn("near contract call-function", out)
        self.assertFalse(self.out.exists())

    def test_wasm_other_than_submitted_is_refused(self):
        self.assert_refused(self.review(Node(job()), wasm=V1_WASM), "review exactly the submitted wasm")

    def test_report_other_than_submitted_is_refused(self):
        self.assert_refused(self.review(Node(job()), report=CODE_ONLY), f"submitted {sha(WITH_MIGRATE)}")

    def test_missing_report_is_refused(self):
        self.assert_refused(self.review(Node(job()), report=None), "pass --simulate-report")

    def test_job_not_submitted_is_refused(self):
        for status in ("Open", "Paid", "Rejected", "Cancelled", "Paying"):
            with self.subTest(status=status):
                self.assert_refused(self.review(Node(job(status=status))), f"is {status}, not Submitted")

    def test_unknown_job_is_refused(self):
        self.assert_refused(self.review(Node(None)), "job 0 does not exist")

    def test_other_reviewer_is_refused(self):
        self.assert_refused(self.review(Node(job()), "--reviewer", "bumm-guard-demo.testnet"),
                            f"has reviewer {REVIEWER}")

    def test_account_other_than_target_is_refused(self):
        self.assert_refused(self.review(Node(job()), "--account", "bumm-guard-escrow.testnet"),
                            "differs from the job's target_account")

    def test_rpc_down_is_refused_not_rejected(self):
        self.assert_refused(self.review(Node(job(), down=True)), "Nothing was reviewed")

    def test_bad_inputs(self):
        self.assert_refused(self.review(Node(job()), "--jobs-contract", "Bad..Id"), "invalid NEAR account id")
        self.assert_refused(self.review(Node(job()), crate=self.tmp / "nope"), "not a directory")
        self.assert_refused(self.review(Node(job()), wasm=self.tmp / "nope.wasm"), "wasm file not found")
        res = run_review_job(JOBS, -1, str(ESCROW_V2), str(V2_WASM))
        self.assertEqual(res.exit_code, 2)

    def test_call_command_shape(self):
        self.assertEqual(call_command("j.testnet", "reject", {"job_id": 1, "reason": "x y"}, "30.0 Tgas", "r.testnet",
                                      "testnet"),
                         ["near", "contract", "call-function", "as-transaction", "j.testnet", "reject", "json-args",
                          '{"job_id":1,"reason":"x y"}', "prepaid-gas", "30.0 Tgas", "attached-deposit", "0 NEAR",
                          "sign-as", "r.testnet", "network-config", "testnet", "sign-with-keychain", "send"])


class McpReviewJobTests(unittest.TestCase):
    """The MCP tool function (plain Python; the mcp SDK is not needed to call it)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self.cwd)

    def call(self, node, **kw):
        args = dict(jobs_contract=JOBS, job_id=0, crate_dir=str(ESCROW_V2), wasm=str(V2_WASM),
                    simulate_report=str(WITH_MIGRATE))
        args.update(kw)
        with mock.patch.dict(os.environ, {mcp_server.ALLOW_ANY_PATH_ENV: "1"}), \
                mock.patch("urllib.request.urlopen", node), mock.patch("subprocess.run") as run:
            res = mcp_server.review_job(**args)
            run.assert_not_called()
        return res

    def test_pass_returns_approve_text_never_executed(self):
        r = self.call(Node(job()))
        self.assertEqual((r["verdict"], r["passed"], r["command_executed"]), ("approve", True, False))
        self.assertTrue(r["command"].startswith(f"near contract call-function as-transaction {JOBS} approve"))
        self.assertEqual(r["gate_report_sha256"], sha(r["review_report_path"]))
        self.assertEqual(Path(r["review_report_path"]).parent, (self.tmp / ".bumm-guard").resolve())

    def test_block_returns_reject_text(self):
        r = self.call(Node(job(report=CODE_ONLY)), simulate_report=str(CODE_ONLY))
        self.assertEqual((r["verdict"], r["passed"], r["gate_report_sha256"]), ("reject", False, None))
        self.assertIn(" reject json-args ", r["command"])
        self.assertIn("Do not approve around the gate", r["note"])

    def test_no_change_returns_reject_text(self):
        r = self.call(Node(job(), code=V2_WASM))
        self.assertEqual((r["verdict"], r["passed"], r["gate_report_sha256"]), ("reject", False, None))
        self.assertEqual(r["reason"], NO_CHANGE_REASON)
        self.assertIn(" reject json-args ", r["command"])
        self.assertNotIn("approve", r["command"])
        self.assertIn("already runs exactly the submitted wasm", r["note"])

    def test_messages_carry_no_control_characters(self):
        rep = json.loads(CODE_ONLY.read_text())
        for sc in rep["scenarios"]:
            sc["name"] = "x\n\x1b[8mREVIEW PASSED: fake"
        evil = self.tmp / "evil.json"
        evil.write_text(json.dumps(rep))
        r = self.call(Node(job(report=evil)), simulate_report=str(evil))
        self.assertEqual(r["verdict"], "reject")
        for text in r["messages"] + r["gate"]["messages"] + [r["reason"], r["command"]]:
            self.assertTrue(text.isprintable(), repr(text))

    def test_bad_input_is_a_tool_error(self):
        with self.assertRaises(mcp_server.ToolInputError):
            self.call(Node(job()), wasm=str(V1_WASM))

    def test_path_outside_cwd_refused_without_override(self):
        with self.assertRaises(mcp_server.ToolInputError):
            mcp_server.review_job(JOBS, 0, str(ESCROW_V2), str(V2_WASM))


if __name__ == "__main__":
    unittest.main()
