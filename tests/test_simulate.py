"""``bumm-guard simulate``: scenario validation, hashing, summary, and the CLI path with a fake
``bumm-sim`` (a small Python script that writes a canned FORMATS section 3 report)."""

import contextlib
import copy
import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from bumm_guard import cli, simulate
from bumm_guard.simulate import ScenarioError, parse_gas, parse_near_amount, validate_scenario

ROOT = Path(__file__).resolve().parent.parent
N06 = ROOT / "scenarios" / "n06"
UPGRADE = ROOT / "scenarios" / "upgrade"

EXAMPLE = {
    "name": "N06 capacity 2, third refused, cancel frees a seat",
    "accounts": {"alice": "10 NEAR", "bob": "10 NEAR", "carol": "10 NEAR"},
    "contract": {"wasm": "c.wasm", "init": {"method": "new", "args": {"capacity": 2}}},
    "steps": [
        {"call": "register", "by": "alice", "args": {}, "deposit": "0", "gas": "30 Tgas", "expect": "ok"},
        {"call": "register", "by": "bob", "expect": "ok"},
        {"call": "register", "by": "carol", "expect": {"fail_contains": "Event is full"}},
        {"view": "get_registered_count", "expect": {"equals": 2}},
        {"call": "cancel", "by": "alice", "expect": "ok"},
        {"call": "register", "by": "carol", "expect": "ok"},
        {"view": "get_registered", "expect": {"contains": "carol"}},
        {"balance": "bob", "expect": {"at_least": "9 NEAR"}},
    ],
}


def with_step(step):
    d = copy.deepcopy(EXAMPLE)
    d["steps"] = [step]
    return d


def run_cli(*argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    old = dict(os.environ)
    try:
        if env is not None:
            os.environ.clear()
            os.environ.update(env)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
    finally:
        os.environ.clear()
        os.environ.update(old)
    return code, out.getvalue(), err.getvalue()


class AmountTests(unittest.TestCase):
    def test_near(self):
        self.assertEqual(parse_near_amount("1 NEAR"), 10**24)
        self.assertEqual(parse_near_amount("1.5 NEAR"), 15 * 10**23)
        self.assertEqual(parse_near_amount(" 2near "), 2 * 10**24)
        self.assertEqual(parse_near_amount("0.000000000000000000000001 NEAR"), 1)
        self.assertEqual(parse_near_amount("1000"), 1000)
        for bad in ("", "NEAR", "-1", "1e3", "1.5", "1,5 NEAR", "1 ETH", "0.0000000000000000000000001 NEAR",
                    "999999999999999999 NEAR"):
            with self.assertRaises(ScenarioError, msg=bad):
                parse_near_amount(bad)

    def test_gas(self):
        self.assertEqual(parse_gas("30 Tgas"), 30 * 10**12)
        self.assertEqual(parse_gas("2.5 Tgas"), 25 * 10**11)
        self.assertEqual(parse_gas("1000"), 1000)
        for bad in ("30 Ggas", "lots", "99999999999 Tgas"):
            with self.assertRaises(ScenarioError, msg=bad):
                parse_gas(bad)


class ValidationTests(unittest.TestCase):
    def test_formats_example_valid(self):
        info = validate_scenario(EXAMPLE)
        self.assertEqual(info["steps"], 8)

    def test_repo_n06_scenarios_valid(self):
        files = sorted(N06.glob("*.json"))
        self.assertEqual(len(files), 3)
        for f in files:
            data = json.loads(f.read_text())
            validate_scenario(data)
            self.assertEqual(data["contract"]["wasm"],
                             "tests/fixtures/near/target/wasm32-unknown-unknown/release/event_registration.wasm")
            self.assertEqual(data["contract"]["init"], {"method": "new", "args": {"capacity": 2}})
        texts = " ".join(f.read_text() for f in files)
        for needle in ("Event is full", "is already registered", "is not registered"):
            self.assertIn(needle, texts)

    def assertInvalid(self, data, needle):
        with self.assertRaises(ScenarioError) as cm:
            validate_scenario(data)
        self.assertIn(needle, str(cm.exception))

    def test_step_errors(self):
        cases = [
            ({"transfer": "alice"}, "step 0: unknown step kind"),
            ({"call": "register", "by": "dave"}, "unknown account 'dave' in 'by'"),
            ({"balance": "dave"}, "unknown account 'dave' in 'balance'"),
            ({"call": "register", "by": "alice", "deposit": "1 ETH"}, "deposit: bad NEAR amount"),
            ({"call": "register", "by": "alice", "deposit": 1}, "deposit must be a string"),
            ({"call": "register", "by": "alice", "gas": "lots"}, "gas: bad gas"),
            ({"call": "register", "by": "alice", "expcet": "ok"}, "unknown field 'expcet'"),
            ({"call": "register", "view": "x", "by": "alice"}, "more than one step kind"),
            ({"view": "x", "expect": {"greater": 1}}, "unknown expectation 'greater'"),
            ({"view": "x", "expect": "fine"}, "expect must be"),
            ({"view": "x", "expect": {"at_least": "lots"}}, "bad NEAR amount"),
            ({"view": "x", "args": [1]}, "'args' must be a JSON object"),
            ({"balance": "alice", "expect": {"contains": "x"}}, "balance step supports"),
            ({"call": "register"}, "missing 'by'"),
            ("register", "step 0: must be a JSON object"),
        ]
        for step, needle in cases:
            with self.subTest(step=step):
                self.assertInvalid(with_step(step), needle)

    def test_repo_upgrade_scenarios_valid(self):
        with_migrate = json.loads((UPGRADE / "n06_to_n10_with_migrate.json").read_text())
        code_only = json.loads((UPGRADE / "n06_to_n10_code_only.json").read_text())
        wasm = "tests/fixtures/near/target/wasm32-unknown-unknown/release/event_registration_waitlist.wasm"
        for data in (with_migrate, code_only):
            info = validate_scenario(data)
            self.assertEqual(info["upgrades"], [wasm])
        self.assertEqual(with_migrate["steps"][3]["migrate"], {"method": "migrate", "args": {}})
        self.assertNotIn("migrate", code_only["steps"][3])
        self.assertEqual(validate_scenario(EXAMPLE)["upgrades"], [])

    def test_upgrade_step(self):
        validate_scenario(with_step({"upgrade": "n.wasm"}))
        validate_scenario(with_step({"upgrade": "n.wasm", "migrate": None, "expect": "ok"}))
        validate_scenario(with_step({"upgrade": "n.wasm", "migrate": {"method": "migrate", "args": {"a": 1},
                                                                       "gas": "200 Tgas"},
                                     "expect": {"fail_contains": "No old state"}}))
        cases = [
            ({"upgrade": ""}, "'upgrade' must be a non-empty string"),
            ({"upgrade": 1}, "'upgrade' must be a non-empty string"),
            ({"upgrade": "n.wasm", "by": "alice"}, "unknown field 'by'"),
            ({"upgrade": "n.wasm", "call": "x", "by": "alice"}, "more than one step kind"),
            ({"upgrade": "n.wasm", "migrate": "migrate"}, "migrate must be an object"),
            ({"upgrade": "n.wasm", "migrate": {}}, "step 0.migrate: missing 'method'"),
            ({"upgrade": "n.wasm", "migrate": {"method": "m", "deposit": "1"}}, "step 0.migrate: unknown field 'deposit'"),
            ({"upgrade": "n.wasm", "migrate": {"method": "m", "args": [1]}}, "step 0.migrate: 'args' must be a JSON object"),
            ({"upgrade": "n.wasm", "migrate": {"method": "m", "gas": "lots"}}, "step 0.migrate: gas: bad gas"),
            ({"upgrade": "n.wasm", "migrate": {"method": "m", "gas": 5}}, "step 0.migrate: gas must be a string"),
            ({"upgrade": "n.wasm", "expect": {"equals": 1}}, "upgrade step supports ok, fail_contains"),
            ({"upgrade": "n.wasm", "expect": {"at_least": "1 NEAR"}}, "upgrade step supports ok, fail_contains"),
            ({"transfer": "alice"}, "call, view, balance, upgrade"),
        ]
        for step, needle in cases:
            with self.subTest(step=step):
                self.assertInvalid(with_step(step), needle)

    def test_contract_caller_and_default_expect(self):
        validate_scenario(with_step({"call": "new", "by": "contract"}))
        validate_scenario(with_step({"balance": "contract", "expect": {"at_most": "100 NEAR"}}))

    def test_top_level_errors(self):
        d = copy.deepcopy(EXAMPLE)
        d["accounts"]["alice"] = "lots"
        self.assertInvalid(d, "accounts.alice: bad NEAR amount")
        d = copy.deepcopy(EXAMPLE)
        d["accounts"] = {"contract": "1 NEAR"}
        self.assertInvalid(d, "reserved")
        d = copy.deepcopy(EXAMPLE)
        d["accounts"] = {"Bob.x": "1 NEAR"}
        self.assertInvalid(d, "bad account name")
        d = copy.deepcopy(EXAMPLE)
        d["loop"] = 3
        self.assertInvalid(d, "unknown field 'loop'")
        d = copy.deepcopy(EXAMPLE)
        del d["contract"]["wasm"]
        self.assertInvalid(d, "contract: missing 'wasm'")
        self.assertInvalid([], "must be a JSON object")


class HashAndSummaryTests(unittest.TestCase):
    def test_combined_hash(self):
        self.assertEqual(simulate.combined_sha256(["aa"]), "aa")
        want = hashlib.sha256(b"aa\nbb").hexdigest()  # same rule as bumm-sim combined_sha256
        self.assertEqual(simulate.combined_sha256(["bb", "aa"]), want)
        self.assertEqual(simulate.combined_sha256(["aa", "bb"]), want)

    def test_file_hash_is_raw_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_bytes(b'{"x": 1}\r\n')
            self.assertEqual(simulate.sha256_file(p), hashlib.sha256(b'{"x": 1}\r\n').hexdigest())

    def test_report_path(self):
        self.assertEqual(simulate.report_path(None), Path(".bumm-guard/simulate-report.json"))
        self.assertEqual(simulate.report_path("out/r.json"), Path("out/r.json"))
        self.assertEqual(simulate.report_path("out/"), Path("out/simulate-report.json"))

    def test_render_summary(self):
        report = {
            "wasm_sha256": "w" * 64, "scenario_sha256": "s" * 64,
            "scenarios": [
                {"name": "ok one", "passed": True, "duration_s": 12.4,
                 "steps": [{"index": 0, "kind": "call", "method": "register", "by": "alice", "passed": True,
                            "expected": "ok", "actual": {"status": "success", "value": None}}]},
                {"name": "bad one", "passed": False, "duration_s": 3.0,
                 "steps": [{"index": 0, "kind": "call", "method": "register", "by": "carol", "passed": False,
                            "expected": {"fail_contains": "Event is full"},
                            "actual": {"status": "success", "value": None, "gas_burnt_tgas": 2.1}},
                           {"index": 1, "kind": "view", "method": "get_registered_count", "passed": False,
                            "expected": {"equals": 2}, "actual": {"status": "success", "value": 3}},
                           {"index": 2, "kind": "balance", "account": "bob", "passed": False,
                            "expected": {"at_least": "9 NEAR"}, "actual": {"status": "skipped", "error": "x"}}]},
            ],
            "summary": {"scenarios": 2, "passed": 1, "failed": 1, "steps": 4, "steps_failed": 3},
        }
        text = simulate.render_summary(report, "r.json")
        self.assertIn("PASS  ok one  (1/1 steps, 12.4s)", text)
        self.assertIn("FAIL  bad one  (0/3 steps, 3.0s)", text)
        self.assertIn('step 0 call register by carol: expected {"fail_contains": "Event is full"}, actual success: null',
                      text)
        self.assertIn("step 1 view get_registered_count: expected {\"equals\": 2}, actual success: 3", text)
        self.assertIn("1 step(s) not executed", text)
        self.assertIn("2 scenario(s): 1 passed, 1 failed; 3 of 4 step(s) failed", text)
        self.assertIn("report: r.json", text)
        self.assertNotIn("upgrade_wasm_sha256", text)

    def test_render_summary_upgrade(self):
        report = {
            "wasm_sha256": "w" * 64, "scenario_sha256": "s" * 64, "upgrade_wasm_sha256": "u" * 64,
            "scenarios": [{"name": "code only", "passed": False, "duration_s": 9.0, "steps": [
                {"index": 3, "kind": "upgrade", "by": "contract", "wasm": "t/new.wasm", "passed": False,
                 "expected": "ok", "actual": {"status": "failure", "error": "boom", "code_sha256": "u" * 64}},
                {"index": 4, "kind": "upgrade", "by": "contract", "wasm": "t/new.wasm", "method": "migrate",
                 "passed": False, "expected": "ok", "actual": {"status": "failure", "error": "No old state"}}]}],
            "summary": {"scenarios": 1, "passed": 0, "failed": 1, "steps": 2, "steps_failed": 2},
        }
        text = simulate.render_summary(report)
        self.assertIn('step 3 upgrade new.wasm (code only): expected ok, actual failure: boom', text)
        self.assertIn("step 4 upgrade new.wasm + migrate: expected", text)
        self.assertIn("upgrade_wasm_sha256 " + "u" * 64, text)


FAKE_RUNNER = textwrap.dedent('''\
    import hashlib, json, os, sys
    args = sys.argv[1:]
    assert args[0] == "run", args
    files, out, wasm, upgrade_wasm = [], None, None, None
    i = 1
    while i < len(args):
        if args[i] == "--scenario": files.append(args[i + 1])
        elif args[i] == "--out": out = args[i + 1]
        elif args[i] == "--wasm": wasm = args[i + 1]
        elif args[i] == "--upgrade-wasm": upgrade_wasm = args[i + 1]
        i += 2
    mode = os.environ.get("FAKE_MODE", "pass")
    if mode == "noreport":
        sys.stderr.write("bumm-sim: boom\\n"); sys.exit(1)
    hashes, scenarios, up_hashes = [], [], []
    for f in files:
        raw = open(f, "rb").read()
        h = hashlib.sha256(raw).hexdigest(); hashes.append(h)
        data = json.loads(raw)
        w = wasm or data["contract"]["wasm"]
        if not os.path.exists(w): w = os.path.join(os.path.dirname(f), w)
        wh = hashlib.sha256(open(w, "rb").read()).hexdigest()
        failed = mode == "fail"
        uh = None
        for s in data["steps"]:
            if "upgrade" in s:
                u = upgrade_wasm or s["upgrade"]
                if not os.path.exists(u): u = os.path.join(os.path.dirname(f), u)
                uh = hashlib.sha256(open(u, "rb").read()).hexdigest()
        if uh: up_hashes.append(uh)
        if mode == "badupgrade" and uh: uh = "0" * 64
        scenarios.append({"name": data["name"], "file": f, "scenario_sha256": h, "wasm": w, "wasm_sha256": wh,
            "upgrade_wasm_sha256": uh,
            "passed": not failed, "duration_s": 1.5, "accounts": {},
            "steps": [{"index": 0, "kind": "call", "method": "register", "by": "carol", "passed": not failed,
                       "expected": {"fail_contains": "Event is full"},
                       "actual": {"status": "success" if failed else "failure",
                                  "error": None if failed else "Smart contract panicked: Event is full",
                                  "gas_burnt_tgas": 2.9}}]})
    combined = hashes[0] if len(hashes) == 1 else hashlib.sha256("\\n".join(sorted(hashes)).encode()).hexdigest()
    if mode == "badhash": combined = "0" * 64
    n_failed = sum(1 for s in scenarios if not s["passed"])
    shared_up = up_hashes[0] if len(set(up_hashes)) == 1 else None
    if mode == "badupgrade": shared_up = "0" * 64
    report = {"tool": "bumm-guard", "version": "0.4.0", "runner": "fake", "wasm_sha256": wh,
              "upgrade_wasm_sha256": shared_up,
              "scenario_sha256": combined, "sandbox": {"near_workspaces": "0.24.0", "near_sandbox": "0.4.0"},
              "scenarios": scenarios,
              "summary": {"scenarios": len(scenarios), "passed": len(scenarios) - n_failed, "failed": n_failed,
                          "steps": len(scenarios), "steps_failed": n_failed}}
    open(out, "w").write(json.dumps(report))
    open(out + ".argv", "w").write(json.dumps(args))
    sys.exit(1 if n_failed else 0)
    ''')


class FakeRunnerCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.runner = self.dir / "bumm-sim"
        self.runner.write_text(f"#!{sys.executable}\n" + FAKE_RUNNER)
        self.runner.chmod(self.runner.stat().st_mode | stat.S_IEXEC)
        (self.dir / "c.wasm").write_bytes(b"\0asm\x01\0\0\0")
        self.scen = []
        for i in range(2):
            p = self.dir / f"s{i}.json"
            d = copy.deepcopy(EXAMPLE)
            d["name"] = f"scenario {i}"
            p.write_text(json.dumps(d))  # wasm "c.wasm" resolves next to the scenario file
            self.scen.append(str(p))
        self.out = str(self.dir / "out" / "simulate-report.json")

    def tearDown(self):
        self.tmp.cleanup()

    def env(self, **extra):
        e = {k: v for k, v in os.environ.items() if k != "BUMM_SIM"}
        e.update(extra)
        return e

    def test_pass(self):
        code, out, err = run_cli("simulate", *self.scen, "--runner", str(self.runner), "--out", self.out,
                                 env=self.env())
        self.assertEqual(code, 0, err)
        self.assertIn("PASS  scenario 0  (1/1 steps, 1.5s)", out)
        self.assertIn("2 scenario(s): 2 passed, 0 failed; 0 of 2 step(s) failed", out)
        self.assertIn(f"report: {self.out}", out)
        argv = json.loads(Path(self.out + ".argv").read_text())
        self.assertEqual(argv, ["run", "--scenario", self.scen[0], "--scenario", self.scen[1], "--out", self.out])

    def test_env_runner_and_wasm_override(self):
        wasm = str(self.dir / "c.wasm")
        code, out, err = run_cli("simulate", self.scen[0], "--wasm", wasm, "--out", self.out,
                                 env=self.env(BUMM_SIM=str(self.runner)))
        self.assertEqual(code, 0, err)
        argv = json.loads(Path(self.out + ".argv").read_text())
        self.assertEqual(argv[-2:], ["--wasm", wasm])

    def test_failed_scenario_exit_1(self):
        code, out, _ = run_cli("simulate", self.scen[0], "--runner", str(self.runner), "--out", self.out,
                               env=self.env(FAKE_MODE="fail"))
        self.assertEqual(code, 1)
        self.assertIn("FAIL  scenario 0", out)
        self.assertIn("step 0 call register by carol", out)

    def test_hash_mismatch_exit_1(self):
        code, _, err = run_cli("simulate", self.scen[0], "--runner", str(self.runner), "--out", self.out,
                               env=self.env(FAKE_MODE="badhash"))
        self.assertEqual(code, 1)
        self.assertIn("report scenario_sha256", err)

    def test_runner_without_report(self):
        code, _, err = run_cli("simulate", self.scen[0], "--runner", str(self.runner), "--out", self.out,
                               env=self.env(FAKE_MODE="noreport"))
        self.assertEqual(code, 1)
        self.assertIn("bumm-sim: boom", err)
        self.assertIn("wrote no readable report", err)

    def test_missing_runner_exit_4(self):
        code, _, err = run_cli("simulate", self.scen[0], "--runner", str(self.dir / "nope"), "--out", self.out,
                               env=self.env())
        self.assertEqual(code, 4)
        self.assertIn("cd contracts && cargo build --release --locked -p bumm-sim", err)

    def test_invalid_scenario_exit_2_runner_not_called(self):
        bad = self.dir / "bad.json"
        bad.write_text(json.dumps(with_step({"call": "register", "by": "dave"})))
        code, _, err = run_cli("simulate", str(bad), "--runner", str(self.runner), "--out", self.out, env=self.env())
        self.assertEqual(code, 2)
        self.assertIn("unknown account 'dave'", err)
        self.assertFalse(Path(self.out).exists())

    def test_invalid_json_exit_2(self):
        bad = self.dir / "bad.json"
        bad.write_text("{nope")
        code, _, err = run_cli("simulate", "--validate-only", str(bad), env=self.env())
        self.assertEqual(code, 2)
        self.assertIn("invalid JSON", err)

    def test_validate_only_needs_no_runner(self):
        code, out, err = run_cli("simulate", "--validate-only", *self.scen, "--runner", str(self.dir / "nope"),
                                 env=self.env())
        self.assertEqual(code, 0, err)
        self.assertIn("2 scenario file(s) valid", out)
        want = simulate.combined_sha256([simulate.sha256_file(p) for p in self.scen])
        self.assertIn(f"scenario_sha256 {want}", out)

    def write_upgrade_scenario(self, migrate=True):
        (self.dir / "n.wasm").write_bytes(b"\0asm\x01\0\0\0new")
        d = copy.deepcopy(EXAMPLE)
        d["name"] = "upgrade"
        step = {"upgrade": "n.wasm", "expect": "ok"}
        if migrate:
            step["migrate"] = {"method": "migrate"}
        d["steps"].insert(3, step)
        p = self.dir / "up.json"
        p.write_text(json.dumps(d))
        return str(p)

    def test_upgrade_pass_and_hash_check(self):
        up = self.write_upgrade_scenario()
        code, out, err = run_cli("simulate", up, self.scen[0], "--runner", str(self.runner), "--out", self.out,
                                 env=self.env())
        self.assertEqual(code, 0, err)
        self.assertIn("upgrade wasm " + str(self.dir / "n.wasm"), out)
        want = simulate.sha256_file(self.dir / "n.wasm")
        self.assertIn(f"upgrade_wasm_sha256 {want}", out)
        argv = json.loads(Path(self.out + ".argv").read_text())
        self.assertNotIn("--upgrade-wasm", argv)

    def test_upgrade_wasm_override(self):
        up = self.write_upgrade_scenario(migrate=False)
        cand = self.dir / "candidate.wasm"
        cand.write_bytes(b"\0asm\x01\0\0\0candidate")
        code, out, err = run_cli("simulate", up, "--upgrade-wasm", str(cand), "--runner", str(self.runner),
                                 "--out", self.out, env=self.env())
        self.assertEqual(code, 0, err)
        argv = json.loads(Path(self.out + ".argv").read_text())
        self.assertEqual(argv[-2:], ["--upgrade-wasm", str(cand)])
        self.assertIn(f"upgrade_wasm_sha256 {simulate.sha256_file(cand)}", out)
        self.assertEqual(json.loads(Path(self.out).read_text())["wasm_sha256"],
                         simulate.sha256_file(self.dir / "c.wasm"), "--upgrade-wasm leaves contract.wasm alone")

    def test_upgrade_hash_mismatch_exit_1(self):
        up = self.write_upgrade_scenario()
        code, _, err = run_cli("simulate", up, "--runner", str(self.runner), "--out", self.out,
                               env=self.env(FAKE_MODE="badupgrade"))
        self.assertEqual(code, 1)
        self.assertIn("report upgrade_wasm_sha256", err)

    def test_report_without_upgrade_must_have_null(self):
        code, _, err = run_cli("simulate", self.scen[0], "--runner", str(self.runner), "--out", self.out,
                               env=self.env(FAKE_MODE="badupgrade"))
        self.assertEqual(code, 1)
        self.assertIn("report upgrade_wasm_sha256 " + "0" * 64 + " != None", err)

    def test_missing_upgrade_wasm_exit_2(self):
        up = self.write_upgrade_scenario()
        (self.dir / "n.wasm").unlink()
        code, out, err = run_cli("simulate", up, "--runner", str(self.runner), "--out", self.out, env=self.env())
        self.assertEqual(code, 2)
        self.assertIn("upgrade wasm not found", err)
        self.assertIn("-p event_registration_waitlist", err)
        self.assertFalse(Path(self.out).exists())
        code, _, err = run_cli("simulate", up, "--upgrade-wasm", str(self.dir / "nope.wasm"),
                               "--runner", str(self.runner), "--out", self.out, env=self.env())
        self.assertEqual(code, 2)
        self.assertIn("upgrade wasm not found", err)

    def test_validate_only_reports_missing_upgrade_wasm(self):
        up = self.write_upgrade_scenario()
        (self.dir / "n.wasm").unlink()
        code, out, err = run_cli("simulate", "--validate-only", up, env=self.env())
        self.assertEqual(code, 0, err)
        self.assertIn("(missing)", out)
        self.assertIn("upgrade wasm not found", err)

    def test_missing_wasm_exit_2(self):
        (self.dir / "c.wasm").unlink()
        code, _, err = run_cli("simulate", self.scen[0], "--runner", str(self.runner), "--out", self.out,
                               env=self.env())
        self.assertEqual(code, 2)
        self.assertIn("wasm not found", err)
        self.assertIn("cargo build --release --locked --target wasm32-unknown-unknown -p event_registration", err)


if __name__ == "__main__":
    unittest.main()
