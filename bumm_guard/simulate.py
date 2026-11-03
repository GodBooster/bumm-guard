"""``bumm-guard simulate``: validate scenario files (docs/FORMATS.md section 2), run them with
the ``bumm-sim`` runner on a local near-sandbox and summarise its section 3 report.

The sandbox work happens in the Rust binary ``bumm-sim`` (``contracts/sim-runner``); this
module only validates, hashes, locates the runner, runs it and reads the report. Standard
library only.

Exit codes: 0 every scenario passed (or, with ``--validate-only``, every file is valid);
1 a scenario failed, the runner produced no usable report, or the report does not match the
inputs; 2 invalid scenario file or bad input; 4 runner binary not found.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

YOCTO_PER_NEAR = 10**24
GAS_PER_TGAS = 10**12
U64_MAX = 2**64 - 1
U128_MAX = 2**128 - 1
CONTRACT_ALIAS = "contract"
DEFAULT_OUT = ".bumm-guard/simulate-report.json"
REPORT_NAME = "simulate-report.json"
RUNNER_ENV = "BUMM_SIM"
RUNNER_REL = Path("contracts/target/release/bumm-sim")
BUILD_RUNNER_CMD = "cd contracts && cargo build --release --locked -p bumm-sim"
BUILD_WASM_HINT = ("for the N06 contract: cd tests/fixtures/near && "
                   "cargo build --release --locked --target wasm32-unknown-unknown -p event_registration")
BUILD_UPGRADE_WASM_HINT = ("for the N10 upgrade: cd tests/fixtures/near && "
                           "cargo build --release --locked --target wasm32-unknown-unknown -p event_registration_waitlist")

TOP_KEYS = ("name", "description", "accounts", "contract", "steps")
CONTRACT_KEYS = ("wasm", "init")
INIT_KEYS = ("method", "args")
STEP_KEYS = {
    "call": ("call", "by", "args", "deposit", "gas", "expect"),
    "view": ("view", "args", "expect"),
    "balance": ("balance", "expect"),
    "upgrade": ("upgrade", "migrate", "expect"),
}
MIGRATE_KEYS = ("method", "args", "gas")
EXPECT_KINDS = ("fail_contains", "equals", "contains", "at_least", "at_most")
_ALIAS_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|[-_](?=[a-z0-9]))*$")


class ScenarioError(ValueError):
    """A scenario file does not follow FORMATS section 2."""


# ----------------------------------------------------------------------------- amounts

def _parse_scaled(num: str, scale: int, what: str) -> int:
    int_part, _, frac_part = num.partition(".")
    if (not int_part and not frac_part) or not (int_part + frac_part).isascii() \
            or not all(p.isdigit() or p == "" for p in (int_part, frac_part)):
        raise ScenarioError(f"bad {what} {num!r}: expected a non-negative decimal number")
    if len(frac_part) > scale:
        raise ScenarioError(f"bad {what} {num!r}: more than {scale} decimal places")
    return int(int_part or "0") * 10**scale + int((frac_part or "0").ljust(scale, "0"))


def _strip_suffix_ci(s: str, suffix: str) -> str | None:
    return s[: -len(suffix)] if s.lower().endswith(suffix.lower()) else None


def parse_near_amount(s: str) -> int:
    """yoctoNEAR integer string (``"1000"``) or ``"<decimal> NEAR"``; returns yoctoNEAR."""
    if not isinstance(s, str):
        raise ScenarioError(f"bad NEAR amount {s!r}: must be a string like \"10 NEAR\"")
    t = s.strip()
    num = _strip_suffix_ci(t, "NEAR")
    if num is not None:
        v = _parse_scaled(num.rstrip(), 24, "NEAR amount")
    elif t.isascii() and t.isdigit():
        v = int(t)
    else:
        raise ScenarioError(f"bad NEAR amount {s!r}: expected a yoctoNEAR integer string like \"1000\" "
                            "or \"<decimal> NEAR\" like \"1.5 NEAR\"")
    if v > U128_MAX:
        raise ScenarioError(f"bad NEAR amount {s!r}: value too large")
    return v


def parse_gas(s: str) -> int:
    """Raw gas integer string or ``"<decimal> Tgas"``; returns gas units."""
    if not isinstance(s, str):
        raise ScenarioError(f"bad gas {s!r}: must be a string like \"30 Tgas\"")
    t = s.strip()
    num = _strip_suffix_ci(t, "Tgas")
    if num is not None:
        v = _parse_scaled(num.rstrip(), 12, "gas")
    elif t.isascii() and t.isdigit():
        v = int(t)
    else:
        raise ScenarioError(f"bad gas {s!r}: expected a gas integer string or \"<n> Tgas\" like \"30 Tgas\"")
    if v > U64_MAX:
        raise ScenarioError(f"bad gas {s!r}: value too large")
    return v


# ----------------------------------------------------------------------------- validation

def valid_alias(a: str) -> bool:
    return isinstance(a, str) and 1 <= len(a) <= 32 and bool(_ALIAS_RE.match(a))


def _check_keys(obj: dict, allowed: tuple, ctx: str) -> None:
    for k in obj:
        if k not in allowed:
            raise ScenarioError(f"{ctx}: unknown field {k!r} (allowed: {', '.join(allowed)})")


def _req_str(obj: dict, key: str, ctx: str) -> str:
    if key not in obj:
        raise ScenarioError(f"{ctx}: missing {key!r}")
    v = obj[key]
    if not isinstance(v, str) or not v:
        raise ScenarioError(f"{ctx}: {key!r} must be a non-empty string")
    return v


def _check_args(obj: dict, ctx: str) -> None:
    if obj.get("args") is not None and not isinstance(obj["args"], dict):
        raise ScenarioError(f"{ctx}: 'args' must be a JSON object")


def _check_gas(obj: dict, ctx: str) -> None:
    if "gas" in obj:
        if not isinstance(obj["gas"], str):
            raise ScenarioError(f"{ctx}: gas must be a string like \"30 Tgas\"")
        try:
            parse_gas(obj["gas"])
        except ScenarioError as e:
            raise ScenarioError(f"{ctx}: gas: {e}") from None


def _amount_value(v, ctx: str) -> int:
    if isinstance(v, bool):
        raise ScenarioError(f"{ctx}: amount must be a string like \"9 NEAR\" or a yoctoNEAR integer string")
    if isinstance(v, int) and v >= 0:
        return v
    if isinstance(v, str):
        try:
            return parse_near_amount(v)
        except ScenarioError as e:
            raise ScenarioError(f"{ctx}: {e}") from None
    raise ScenarioError(f"{ctx}: amount must be a string like \"9 NEAR\" or a yoctoNEAR integer string")


def _check_expect(step: dict, ctx: str) -> str:
    """Validate ``expect``; returns its kind ("ok" when absent)."""
    if "expect" not in step:
        return "ok"
    e = step["expect"]
    if e == "ok":
        return "ok"
    if not isinstance(e, dict) or len(e) != 1:
        raise ScenarioError(f"{ctx}: expect must be \"ok\" or an object with exactly one of "
                            f"{', '.join(EXPECT_KINDS)}")
    (kind, val), = e.items()
    if kind not in EXPECT_KINDS:
        raise ScenarioError(f"{ctx}: unknown expectation {kind!r} (allowed: \"ok\", {', '.join(EXPECT_KINDS)})")
    if kind == "fail_contains" and (not isinstance(val, str) or not val):
        raise ScenarioError(f"{ctx}: 'fail_contains' must be a non-empty string")
    if kind in ("at_least", "at_most"):
        _amount_value(val, ctx)
    return kind


def validate_scenario(data) -> dict:
    """Validate one parsed scenario against FORMATS section 2. Raises ``ScenarioError`` with
    a message naming the step (0-based, as in the report) and field. Returns a small summary
    ``{"name", "accounts", "wasm", "steps", "upgrades"}`` (``upgrades``: wasm path of each
    upgrade step, section 2a). Mirrors ``parse_scenario`` in
    ``contracts/sim-runner/src/lib.rs``."""
    if not isinstance(data, dict):
        raise ScenarioError("scenario must be a JSON object")
    _check_keys(data, TOP_KEYS, "scenario")
    name = _req_str(data, "name", "scenario")

    if "accounts" not in data:
        raise ScenarioError("scenario: missing 'accounts'")
    accounts = data["accounts"]
    if not isinstance(accounts, dict):
        raise ScenarioError("accounts must be an object {name: balance}")
    for alias, bal in accounts.items():
        if alias == CONTRACT_ALIAS:
            raise ScenarioError(f"accounts: {CONTRACT_ALIAS!r} is reserved for the contract account")
        if not valid_alias(alias):
            raise ScenarioError(f"accounts: bad account name {alias!r}: use 1-32 chars of a-z, 0-9, '-', '_' (no dots)")
        if not isinstance(bal, str):
            raise ScenarioError(f"accounts.{alias}: balance must be a string like \"10 NEAR\"")
        try:
            parse_near_amount(bal)
        except ScenarioError as e:
            raise ScenarioError(f"accounts.{alias}: {e}") from None

    if "contract" not in data:
        raise ScenarioError("scenario: missing 'contract'")
    contract = data["contract"]
    if not isinstance(contract, dict):
        raise ScenarioError("contract must be an object")
    _check_keys(contract, CONTRACT_KEYS, "contract")
    wasm = _req_str(contract, "wasm", "contract")
    init = contract.get("init")
    if init is not None:
        if not isinstance(init, dict):
            raise ScenarioError("contract.init must be an object {method, args}")
        _check_keys(init, INIT_KEYS, "contract.init")
        _req_str(init, "method", "contract.init")
        _check_args(init, "contract.init")

    known = sorted(accounts) + [CONTRACT_ALIAS]
    if "steps" not in data:
        raise ScenarioError("scenario: missing 'steps'")
    steps = data["steps"]
    if not isinstance(steps, list):
        raise ScenarioError("steps must be an array")
    upgrades: list[str] = []
    for i, step in enumerate(steps):
        ctx = f"step {i}"
        if not isinstance(step, dict):
            raise ScenarioError(f"{ctx}: must be a JSON object")
        kinds = [k for k in STEP_KEYS if k in step]
        if not kinds:
            raise ScenarioError(f"{ctx}: unknown step kind (expected exactly one of call, view, balance, upgrade; "
                                f"got keys: {', '.join(step)})")
        if len(kinds) > 1:
            raise ScenarioError(f"{ctx}: more than one step kind ({', '.join(kinds)})")
        kind = kinds[0]
        expect_kind = _check_expect(step, ctx)
        _check_keys(step, STEP_KEYS[kind], ctx)
        _req_str(step, kind, ctx)
        if kind == "call":
            by = _req_str(step, "by", ctx)
            if by not in known:
                raise ScenarioError(f"{ctx}: unknown account {by!r} in 'by' (known: {', '.join(known)})")
            if "deposit" in step:
                if not isinstance(step["deposit"], str):
                    raise ScenarioError(f"{ctx}: deposit must be a string like \"0\" or \"1 NEAR\"")
                try:
                    parse_near_amount(step["deposit"])
                except ScenarioError as e:
                    raise ScenarioError(f"{ctx}: deposit: {e}") from None
            _check_gas(step, ctx)
            _check_args(step, ctx)
        elif kind == "view":
            _check_args(step, ctx)
        elif kind == "upgrade":
            upgrades.append(step["upgrade"])
            migrate = step.get("migrate")
            if migrate is not None:
                mctx = f"{ctx}.migrate"
                if not isinstance(migrate, dict):
                    raise ScenarioError(f"{ctx}: migrate must be an object {{method, args, gas}}")
                _check_keys(migrate, MIGRATE_KEYS, mctx)
                _req_str(migrate, "method", mctx)
                _check_args(migrate, mctx)
                _check_gas(migrate, mctx)
            if expect_kind not in ("ok", "fail_contains"):
                raise ScenarioError(f"{ctx}: an upgrade step supports ok, fail_contains")
        else:
            if step["balance"] not in known:
                raise ScenarioError(f"{ctx}: unknown account {step['balance']!r} in 'balance' "
                                    f"(known: {', '.join(known)})")
            if expect_kind in ("fail_contains", "contains"):
                raise ScenarioError(f"{ctx}: a balance step supports ok, equals, at_least, at_most")
    return {"name": name, "accounts": len(accounts), "wasm": wasm, "steps": len(steps), "upgrades": upgrades}


# ----------------------------------------------------------------------------- hashing, paths

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def combined_sha256(file_hashes: list[str]) -> str:
    """Report-level ``scenario_sha256``: the file hash for one file; for several, sha256 of the
    sorted hex digests joined by ``\\n``. Same as ``combined_sha256`` in bumm-sim."""
    if len(file_hashes) == 1:
        return file_hashes[0]
    return sha256_bytes("\n".join(sorted(file_hashes)).encode())


def resolve_wasm(spec: str, scenario_file: Path, override: str | None = None) -> Path:
    """Same rule as bumm-sim: ``--wasm``; absolute; relative to cwd; relative to the scenario file."""
    if override:
        return Path(override)
    p = Path(spec)
    if p.is_absolute() or p.exists():
        return p
    q = scenario_file.parent / p
    return q if q.exists() else p


def find_runner(runner: str | None = None, env: dict | None = None) -> Path | None:
    """``--runner``, then ``$BUMM_SIM``, then ``contracts/target/release/bumm-sim`` under the
    current directory or the repository this package lives in. Returns None if not found."""
    env = os.environ if env is None else env
    explicit = runner or env.get(RUNNER_ENV)
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    for base in (Path.cwd(), Path(__file__).resolve().parent.parent):
        p = base / RUNNER_REL
        if p.is_file():
            return p
    return None


def report_path(out: str | None) -> Path:
    if not out:
        return Path(DEFAULT_OUT)
    p = Path(out)
    if p.is_dir() or out.endswith(("/", os.sep)):
        return p / REPORT_NAME
    return p


# ----------------------------------------------------------------------------- summary

def _short(v, limit: int = 160) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _step_label(s: dict) -> str:
    kind = s.get("kind", "?")
    if kind == "call":
        return f"call {s.get('method')} by {s.get('by')}"
    if kind == "view":
        return f"view {s.get('method')}"
    if kind == "balance":
        return f"balance {s.get('account')}"
    if kind == "upgrade":
        label = f"upgrade {Path(str(s.get('wasm'))).name}"
        return label + (f" + {s.get('method')}" if s.get("method") else " (code only)")
    return kind


def render_summary(report: dict, path: str | None = None) -> str:
    lines: list[str] = []
    for sc in report.get("scenarios", []):
        steps = sc.get("steps", [])
        ok = sum(1 for s in steps if s.get("passed"))
        lines.append(f"{'PASS' if sc.get('passed') else 'FAIL'}  {sc.get('name')}  "
                     f"({ok}/{len(steps)} steps, {sc.get('duration_s', 0)}s)")
        if sc.get("error"):
            lines.append(f"  error: {sc['error']}")
        init = sc.get("init")
        if init and not init.get("passed"):
            lines.append(f"  init {init.get('method')} failed: {_short(init.get('actual', {}).get('error', ''))}")
        for s in steps:
            if s.get("passed"):
                continue
            a = s.get("actual", {})
            if a.get("status") == "skipped":
                continue
            got = a.get("error") if a.get("status") == "failure" else a.get("value")
            lines.append(f"  step {s.get('index')} {_step_label(s)}: expected {_short(s.get('expected'))}, "
                         f"actual {a.get('status')}: {_short(got)}")
        skipped = sum(1 for s in steps if s.get("actual", {}).get("status") == "skipped")
        if skipped:
            lines.append(f"  {skipped} step(s) not executed")
    sm = report.get("summary", {})
    lines.append(f"{sm.get('scenarios', 0)} scenario(s): {sm.get('passed', 0)} passed, {sm.get('failed', 0)} failed; "
                 f"{sm.get('steps_failed', 0)} of {sm.get('steps', 0)} step(s) failed")
    lines.append(f"wasm_sha256 {report.get('wasm_sha256')}  scenario_sha256 {report.get('scenario_sha256')}")
    if report.get("upgrade_wasm_sha256"):
        lines.append(f"upgrade_wasm_sha256 {report['upgrade_wasm_sha256']}")
    if path:
        lines.append(f"report: {path}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- run

@dataclass
class SimulateOutcome:
    exit_code: int
    lines: list[str] = field(default_factory=list)   # stdout
    errors: list[str] = field(default_factory=list)  # stderr
    report: dict | None = None
    report_path: str | None = None


def load_scenarios(paths: list[str]) -> tuple[list[tuple[Path, dict, str]], list[str]]:
    """Read, parse and validate every file. Returns ([(path, data, sha256)], errors)."""
    loaded, errors = [], []
    for p in paths:
        path = Path(p)
        try:
            raw = path.read_bytes()
        except OSError as e:
            errors.append(f"bumm-guard: cannot read {p}: {e.strerror or e}")
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            errors.append(f"bumm-guard: {p}: invalid JSON: {e}")
            continue
        try:
            validate_scenario(data)
        except ScenarioError as e:
            errors.append(f"bumm-guard: {p}: {e}")
            continue
        loaded.append((path, data, sha256_bytes(raw)))
    return loaded, errors


def run_simulate(paths: list[str], wasm: str | None = None, out: str | None = None, runner: str | None = None,
                 validate_only: bool = False, env: dict | None = None,
                 upgrade_wasm: str | None = None) -> SimulateOutcome:
    loaded, errors = load_scenarios(paths)
    if errors:
        return SimulateOutcome(2, errors=errors)
    if wasm and not Path(wasm).is_file():
        return SimulateOutcome(2, errors=[f"bumm-guard: wasm not found: {wasm} ({BUILD_WASM_HINT})"])
    if upgrade_wasm and not Path(upgrade_wasm).is_file():
        return SimulateOutcome(2, errors=[f"bumm-guard: upgrade wasm not found: {upgrade_wasm} "
                                          f"({BUILD_UPGRADE_WASM_HINT})"])

    out_lines: list[str] = []
    warnings: list[str] = []
    wasm_hashes: list[str | None] = []
    upgrade_hashes: list[str] = []  # one per scenario that has upgrade steps and a shared hash
    upgrade_ambiguous = False
    for path, data, digest in loaded:
        w = resolve_wasm(data["contract"]["wasm"], path, wasm)
        exists = w.is_file()
        wasm_hashes.append(sha256_file(w) if exists else None)
        out_lines.append(f"valid  {path}  {data['name']!r}: {len(data['accounts'])} account(s), "
                         f"{len(data['steps'])} step(s), sha256 {digest[:12]}, wasm {w}"
                         + ("" if exists else " (missing)"))
        if not exists:
            warnings.append(f"bumm-guard: {path}: wasm not found: {w} ({BUILD_WASM_HINT})")
        specs = [s["upgrade"] for s in data["steps"] if "upgrade" in s]
        hashes = set()
        for u in dict.fromkeys(resolve_wasm(spec, path, upgrade_wasm) for spec in specs):
            if u.is_file():
                hashes.add(sha256_file(u))
                out_lines.append(f"       upgrade wasm {u}")
            else:
                out_lines.append(f"       upgrade wasm {u} (missing)")
                warnings.append(f"bumm-guard: {path}: upgrade wasm not found: {u} ({BUILD_UPGRADE_WASM_HINT})")
        if len(hashes) == 1:
            upgrade_hashes.append(hashes.pop())
        elif hashes:
            upgrade_ambiguous = True
    scenario_sha = combined_sha256([d for _, _, d in loaded])
    out_lines.append(f"scenario_sha256 {scenario_sha}")

    if validate_only:
        out_lines.append(f"{len(loaded)} scenario file(s) valid (validate-only, nothing executed)")
        return SimulateOutcome(0, lines=out_lines, errors=warnings)
    if warnings:
        return SimulateOutcome(2, lines=out_lines, errors=warnings)

    exe = find_runner(runner, env)
    if exe is None:
        where = runner or (env if env is not None else os.environ).get(RUNNER_ENV) or str(RUNNER_REL)
        return SimulateOutcome(4, lines=out_lines, errors=[
            f"bumm-guard: scenario runner `bumm-sim` not found ({where}). Build it with:",
            f"  {BUILD_RUNNER_CMD}",
            "or pass --runner PATH / set $BUMM_SIM. It runs on Linux "
            "(near-sandbox ships no macOS x86_64 binary); --validate-only works without it."])

    rpath = report_path(out)
    rpath.parent.mkdir(parents=True, exist_ok=True)
    if rpath.exists():
        rpath.unlink()  # never summarise a stale report from an earlier run
    cmd = [str(exe), "run"]
    for path, _, _ in loaded:
        cmd += ["--scenario", str(path)]
    cmd += ["--out", str(rpath)]
    if wasm:
        cmd += ["--wasm", wasm]
    if upgrade_wasm:
        cmd += ["--upgrade-wasm", upgrade_wasm]
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError as e:
        return SimulateOutcome(4, lines=out_lines, errors=[f"bumm-guard: cannot execute {exe}: {e}",
                                                           f"  build it with: {BUILD_RUNNER_CMD}"])
    runner_err = [l for l in proc.stderr.splitlines() if l.strip()]
    try:
        report = json.loads(rpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        code = 2 if proc.returncode == 2 else 1
        return SimulateOutcome(code, lines=out_lines, errors=runner_err + [
            f"bumm-guard: runner exited with status {proc.returncode} and wrote no readable report at {rpath}: {e}"])

    res = SimulateOutcome(0, lines=out_lines, errors=runner_err, report=report, report_path=str(rpath))
    res.lines.append(render_summary(report, str(rpath)))
    # The hashes bind the report to these inputs (FORMATS section 3); refuse a mismatch.
    if report.get("scenario_sha256") != scenario_sha:
        res.errors.append(f"bumm-guard: report scenario_sha256 {report.get('scenario_sha256')} != {scenario_sha}")
        res.exit_code = 1
    distinct = set(wasm_hashes)
    expected_wasm = wasm_hashes[0] if len(distinct) == 1 else None
    if expected_wasm and report.get("wasm_sha256") != expected_wasm:
        res.errors.append(f"bumm-guard: report wasm_sha256 {report.get('wasm_sha256')} != {expected_wasm}")
        res.exit_code = 1
    # Same rule as bumm-sim: the upgrade wasm shared by every scenario that has an upgrade step.
    expected_upgrade = upgrade_hashes[0] if len(set(upgrade_hashes)) == 1 and not upgrade_ambiguous else None
    if report.get("upgrade_wasm_sha256") != expected_upgrade and (expected_upgrade or not upgrade_hashes):
        res.errors.append(f"bumm-guard: report upgrade_wasm_sha256 {report.get('upgrade_wasm_sha256')} "
                          f"!= {expected_upgrade}")
        res.exit_code = 1
    summary = report.get("summary", {})
    all_passed = summary.get("failed", 1) == 0 and summary.get("scenarios", 0) == len(loaded)
    if not all_passed or proc.returncode != 0:
        res.exit_code = 1
    return res
