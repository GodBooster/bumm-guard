"""Deploy gate: scan a crate, refuse to hand out a deploy command while blocking findings exist.

``run_gate`` returns a ``GateOutcome`` (exit code + lines to print); the CLI does the printing.
Exit codes: 0 clean, 2 refused/bad input, 3 DEPLOY BLOCKED (high/critical finding, a simulate
report that does not match the wasm / has failed scenarios, or an upgrade without proof), 4 ``--execute`` but ``near`` not on PATH,
5 ``near`` ran and failed (deploy mode) or post-deploy verification failed (``--verify-deployed`` mode:
code hash mismatch, a failed smoke view, or RPC unreachable).

Nothing is executed by default: the exact ``near contract deploy`` command (near-cli-rs 0.30
syntax) is only printed. ``--execute`` runs it, testnet only. When the gate blocks, no deploy
is ever started and a stale ``gate-report.json`` from an earlier clean run is removed so it
cannot be mistaken for a pass.

With ``--account`` and ``--wasm`` the gate first reads the account's code (read-only ``view_code``).
No code: a fresh deploy, judged as before. Other code: an upgrade of a live contract with state,
allowed only if ``--simulate-report`` holds a passing scenario with an ``upgrade`` step
(docs/FORMATS.md §2a) from exactly that on-chain code to exactly this wasm; the printed command then
repeats that scenario's migration call (``with-init-call``) or has none (``without-init-call``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .rulepack.near import RULES
from .scanner import ScanResult, discover, scan
from .storage import RPC_URLS, RpcError, estimate_storage, simulate_state_bytes  # noqa: F401  (RPC_URLS re-exported)
from .verify import SmokeSpecError, onchain_code_sha256, parse_smoke, post_deploy_ok, verify_deployed

BLOCKING = ("critical", "high")
REPORT_DIR = ".bumm-guard"
REPORT_NAME = "gate-report.json"
NETWORKS = ("testnet", "mainnet")
# NEAR account ids: dot-separated parts of [a-z0-9] joined by single - or _ ; 2..64 chars
_ACCOUNT_RE = re.compile(r"^(?:[a-z\d]+[-_])*[a-z\d]+(?:\.(?:[a-z\d]+[-_])*[a-z\d]+)*$")
WASM_PLACEHOLDER = "<path/to/contract.wasm>"
ACCOUNT_PLACEHOLDER = "<account-id>"


@dataclass
class GateOutcome:
    exit_code: int
    scan: ScanResult | None = None
    lines: list[str] = field(default_factory=list)       # stdout, printed after the findings
    errors: list[str] = field(default_factory=list)      # stderr
    blocked: bool = False
    report_path: str | None = None
    command: list[str] | None = None
    upgrade: dict | None = None                          # the gate report's ``upgrade`` block


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def valid_account(account: str) -> bool:
    return 2 <= len(account) <= 64 and bool(_ACCOUNT_RE.match(account))


def deploy_command(account: str, wasm: str, network: str, migrate: dict | None = None) -> list[str]:
    """near-cli-rs 0.30 argv; a leading ``-`` in the path is neutralised so it is not read as a flag.

    ``migrate`` (``{method, args, gas}``) makes it one batch transaction: deploy + that call, the
    same shape as the simulated upgrade step (if the call fails, the deploy is rolled back too).
    """
    path = "./" + wasm if wasm.startswith("-") else wasm
    init = ["without-init-call"]
    if migrate:
        init = ["with-init-call", migrate["method"], "json-args",
                json.dumps(migrate.get("args") or {}, separators=(",", ":"), sort_keys=True),
                "prepaid-gas", migrate.get("gas") or "100 Tgas", "attached-deposit", "0 NEAR"]
    return ["near", "contract", "deploy", account, "use-file", path, *init,
            "network-config", network, "sign-with-keychain", "send"]


def build_report(crate: Path, res: ScanResult, wasm: str | None, network: str, account: str | None,
                 abi: str | None = None) -> dict:
    hashes = {}
    for disp, _root in discover([str(crate)]):
        hashes[Path(os.path.relpath(disp, crate)).as_posix()] = sha256_file(disp)
    by_sev: dict[str, int] = {}
    for f in res.findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
    wasm_info = None
    if wasm:
        exists = Path(wasm).is_file()
        wasm_info = {"path": wasm, "sha256": sha256_file(wasm) if exists else None, "exists": exists}
    return {
        "tool": "bumm-guard",
        "version": __version__,
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "crate_dir": str(crate),
        "rule_ids_checked": [r.id for r in RULES],
        "findings_count": len(res.findings),
        "findings_by_severity": dict(sorted(by_sev.items())),
        "blocking_findings": 0,
        "network": network,
        "account": account,
        "rs_sha256": dict(sorted(hashes.items())),
        "wasm": wasm_info,
        "artifact": {"wasm_sha256": wasm_info["sha256"] if wasm_info else None,
                     "abi_sha256": sha256_file(abi) if abi else None},
        "simulate": None,
        "upgrade": None,
        "storage_estimate": None,
        "post_deploy": None,
    }


def load_simulate_report(path: str, wasm_sha256: str) -> tuple[dict | None, list[str], int]:
    """Check a docs/FORMATS.md §3 report against the wasm. Returns ``(simulate block, error lines, exit code)``.

    Exit 2 = unreadable report, 3 = blocks the deploy (other wasm, failed scenarios, no scenarios).
    """
    try:
        rep = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, [f"bumm-guard: cannot read simulate report {path}: {e}"], 2
    if not isinstance(rep, dict) or not isinstance(rep.get("scenarios"), list) or not isinstance(rep.get("summary"), dict):
        return None, [f"bumm-guard: {path} is not a simulate report (docs/FORMATS.md §3)"], 2
    if rep.get("wasm_sha256") != wasm_sha256:
        return None, ["DEPLOY BLOCKED: simulate report is for a different wasm "
                      f"(report {rep.get('wasm_sha256')!r}, artifact {wasm_sha256!r}). Re-run `bumm-guard simulate` "
                      "against this wasm."], 3
    failing = [str(sc.get("name", "?")) for sc in rep["scenarios"] if isinstance(sc, dict) and not sc.get("passed")]
    failed = rep["summary"].get("failed")
    if failing or not isinstance(failed, int) or failed != 0:
        names = ", ".join(failing) or "(see report)"
        return None, [f"DEPLOY BLOCKED: simulate report has failing scenario(s): {names}"], 3
    if not rep["scenarios"]:
        return None, ["DEPLOY BLOCKED: simulate report contains no scenarios"], 3
    return {"report": path, "passed": True, "scenarios": len(rep["scenarios"])}, [], 0


def _upgrade_steps(sc: dict) -> list[dict]:
    return [st for st in sc.get("steps") or [] if isinstance(st, dict) and st.get("kind") == "upgrade"]


def find_upgrade_proof(rep: dict, onchain: str, candidate: str) -> tuple[dict | None, list[str]]:
    """The first passing scenario of a §3 report whose upgrade step goes from ``onchain`` (the
    scenario's ``wasm_sha256``) to ``candidate`` (``upgrade_wasm_sha256`` and the step's
    ``code_sha256``). Returns ``(upgrade block, notes)``; notes explain near misses."""
    notes, proofs = [], []
    for sc in rep.get("scenarios") or []:
        if not isinstance(sc, dict) or not _upgrade_steps(sc):
            continue
        name = str(sc.get("name", "?"))
        frm, to = sc.get("wasm_sha256"), sc.get("upgrade_wasm_sha256")
        if (frm, to) != (onchain, candidate):
            notes.append(f"upgrade scenario {name!r} proves {frm} -> {to}, not {onchain} -> {candidate}")
            continue
        step = _upgrade_steps(sc)[0]
        if (step.get("actual") or {}).get("code_sha256", candidate) != candidate:
            notes.append(f"upgrade scenario {name!r}: deployed code_sha256 differs from the candidate")
            continue
        if not sc.get("passed"):
            notes.append(f"upgrade scenario {name!r} for this pair did not pass")
            continue
        if step.get("method") and not isinstance(step.get("migrate"), dict):
            notes.append(f"upgrade scenario {name!r} has a migration call but the report does not record its "
                         "args (bumm-sim older than this gate): re-run `bumm-guard simulate`")
            continue
        m = step.get("migrate")
        proofs.append({"onchain_code_sha256": onchain, "candidate_sha256": candidate, "scenario": name,
                       "migrate": {"method": m["method"], "args": m.get("args") or {}, "gas": m.get("gas")}
                       if m else None,
                       "passed": True})
    if len({json.dumps(p["migrate"], sort_keys=True) for p in proofs}) > 1:
        notes.append(f"several passing upgrade scenarios with different migrations; using {proofs[0]['scenario']!r}")
    return (proofs[0] if proofs else None), notes


def _blocked(out: GateOutcome, report: Path, dry_run: bool, lines: list[str]) -> GateOutcome:
    """Exit 3; keeps the lines printed so far and removes a stale report."""
    out.exit_code, out.blocked = 3, True
    out.lines.extend(lines)
    if not dry_run and report.is_file():
        report.unlink()
        out.lines.append(f"removed stale {report}")
    return out


def _refused(res: ScanResult, report: Path, dry_run: bool, errors: list[str]) -> GateOutcome:
    """Exit 2 (unusable input); removes a stale report."""
    out = GateOutcome(2, scan=res, errors=errors)
    if not dry_run and report.is_file():
        report.unlink()
        out.lines.append(f"removed stale {report}")
    return out


def run_gate(crate_dir: str, wasm: str | None = None, network: str = "testnet", account: str | None = None,
             dry_run: bool = False, execute: bool = False, abi: str | None = None,
             simulate_report: str | None = None, estimate_storage_flag: bool = False,
             rpc_url: str | None = None) -> GateOutcome:
    """Scan, decide, optionally write the report and (testnet only) run the deploy.

    ``simulate_report`` binds a §3 report to ``wasm``; ``estimate_storage_flag`` adds the read-only
    storage estimate (one RPC call, plus one with ``account``). ``account`` together with an existing
    ``wasm`` adds one read-only ``view_code`` call: code on the account makes this an upgrade, which
    needs a passing upgrade scenario for exactly (on-chain code -> ``wasm``) in ``simulate_report``.
    """
    crate = Path(crate_dir)
    if not crate.is_dir():
        return GateOutcome(2, errors=[f"bumm-guard: not a directory: {crate_dir}"])
    if account is not None and not valid_account(account):
        return GateOutcome(2, errors=[f"bumm-guard: invalid NEAR account id: {account!r}"])
    if execute:
        if network != "testnet":
            return GateOutcome(2, errors=[
                "bumm-guard: --execute is only allowed with --network testnet; refusing to deploy to "
                f"{network}. Print-only mode (no --execute) shows the command to run yourself."])
        if not account or not wasm:
            return GateOutcome(2, errors=["bumm-guard: --execute needs both --account and --wasm"])
        if not Path(wasm).is_file():
            return GateOutcome(2, errors=[f"bumm-guard: wasm file not found: {wasm}"])

    if abi is not None and not Path(abi).is_file():
        return GateOutcome(2, errors=[f"bumm-guard: abi file not found: {abi}"])
    if (simulate_report or estimate_storage_flag) and not (wasm and Path(wasm).is_file()):
        return GateOutcome(2, errors=["bumm-guard: --simulate-report and --estimate-storage need an existing --wasm "
                                      "(the report is bound to the wasm hash / its size is measured)"])

    res = scan([str(crate)])
    blocking = [f for f in res.findings if f.severity in BLOCKING]
    report = crate / REPORT_DIR / REPORT_NAME
    if blocking:
        out = GateOutcome(3, scan=res, blocked=True)
        if not dry_run and report.is_file():
            report.unlink()
            out.lines.append(f"removed stale {report}")
        out.lines.append(f"DEPLOY BLOCKED: {len(blocking)} high/critical finding(s) in {crate_dir}. "
                         "Fix them (see `bumm-guard fix`) and run the gate again.")
        return out

    out = GateOutcome(0, scan=res)
    data = build_report(crate, res, wasm, network, account, abi)
    sim_block, sim_report = None, None
    onchain, upgrade, rep = None, None, None
    if simulate_report:
        try:
            rep = json.loads(Path(simulate_report).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return _refused(res, report, dry_run, [f"bumm-guard: cannot read simulate report {simulate_report}: {e}"])
    upgrade_names = [str(sc.get("name", "?")) for sc in (rep or {}).get("scenarios") or []
                     if isinstance(sc, dict) and _upgrade_steps(sc)] if isinstance(rep, dict) else []
    if account and wasm and Path(wasm).is_file():
        candidate = data["artifact"]["wasm_sha256"]
        try:
            onchain = onchain_code_sha256(account, network, rpc_url)
        except RpcError as e:
            return _blocked(out, report, dry_run, [
                f"DEPLOY BLOCKED: cannot read the code on {account} ({network}): {e}. Without it the gate cannot "
                "tell a fresh deploy from an upgrade; retry, or pass --rpc-url."])
        if onchain is None:
            out.lines.append(f"account {account} ({network}) has no contract code: fresh deploy")
            if upgrade_names:
                out.lines.append(f"warning: simulate report has upgrade scenario(s) ({', '.join(upgrade_names)}) but "
                                 f"{account} has no code: this is a fresh deploy, the upgrade evidence is not used")
        elif onchain == candidate:
            out.lines.append(f"account {account} ({network}) already runs this exact wasm ({onchain}): "
                             "deploying it again changes no code")
        else:
            upgrade, notes = find_upgrade_proof(rep, onchain, candidate) if isinstance(rep, dict) else (None, [])
            if upgrade is None:
                how = ("pass --simulate-report with a passing upgrade scenario (docs/FORMATS.md §2a): contract.wasm = "
                       "the code now on the account, then `bumm-guard simulate <scenario> --upgrade-wasm "
                       f"{wasm}`" if not simulate_report else
                       "the simulate report has no passing upgrade scenario for this pair")
                return _blocked(out, report, dry_run, [
                    f"DEPLOY BLOCKED: account {account} already runs code {onchain}; this is an upgrade and no "
                    f"passing upgrade scenario proves {onchain} -> {candidate}", f"  {how}"]
                    + [f"  {n}" for n in notes])
            out.upgrade = data["upgrade"] = upgrade
            m = upgrade["migrate"]
            out.lines.append(f"upgrade of {account}: {onchain} -> {candidate}, proved by passing scenario "
                             f"{upgrade['scenario']!r}; "
                             + (f"migration {m['method']}({json.dumps(m['args'])}) in the same transaction"
                                if m else "code only (no migration call)"))
            out.lines.extend(f"note: {n}" for n in notes)
    elif account:
        out.lines.append(f"warning: no --wasm: the code on {account} was not checked; an upgrade of a live "
                         "contract needs --wasm and a --simulate-report with a passing upgrade scenario")
    if upgrade:
        scen = rep.get("scenarios") or []
        failing = [str(sc.get("name", "?")) for sc in scen if isinstance(sc, dict) and not sc.get("passed")]
        data["simulate"] = {"report": simulate_report, "passed": not failing, "scenarios": len(scen)}
        if failing:
            out.lines.append(f"note: other scenario(s) in the report did not pass: {', '.join(failing)}")
        sim_report = rep
    elif simulate_report:
        sim_block, errs, code = load_simulate_report(simulate_report, data["artifact"]["wasm_sha256"])
        if code == 2:
            return _refused(res, report, dry_run, errs)
        if code:
            return _blocked(out, report, dry_run, errs)
        sim_report = rep
        data["simulate"] = sim_block
        out.lines.append(f"simulate report ok: {sim_block['scenarios']} scenario(s) passed for this wasm")
    else:
        out.lines.append("warning: no scenario report: behaviour not verified")
    if estimate_storage_flag:
        est = estimate_storage(wasm, network, rpc_url, account, simulate_state_bytes(sim_report))
        data["storage_estimate"] = est
        out.lines.append(f"storage: wasm {est['code_bytes']} bytes locks about {est['locked_near_for_code']} NEAR "
                         f"({est['source']}); a locked balance, not a fee")
        if est.get("state_bytes_after_init") is None:
            out.lines.append("storage: state size after init unknown (simulate report has no storage_usage)")
        acct = est.get("account")
        if acct:
            out.lines.append(f"storage: account {acct['id']}: " + (
                acct["error"] if "error" in acct else
                f"storage_usage {acct['storage_usage']} bytes, balance {acct['amount_near']} NEAR"))
    if wasm and not data["wasm"]["exists"]:
        out.errors.append(f"bumm-guard: warning: wasm not found: {wasm} (sha256 not recorded)")
    if dry_run:
        out.lines.append("dry-run: gate-report.json not written")
    else:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        out.report_path = str(report)
        out.lines.append(f"gate passed: report written to {report}")
    cmd = deploy_command(account or ACCOUNT_PLACEHOLDER, wasm or WASM_PLACEHOLDER, network,
                         upgrade["migrate"] if upgrade else None)
    out.command = cmd
    if not execute:
        out.lines.append("deploy command (NOT executed; pass --execute with --network testnet to run it):")
        out.lines.append("  " + shlex.join(cmd))
        return out
    near = shutil.which("near")
    if near is None:
        out.exit_code = 4
        out.errors.append("bumm-guard: `near` (near-cli-rs) not found on PATH; install it or run the command above yourself.")
        out.lines.append("deploy command:")
        out.lines.append("  " + shlex.join(cmd))
        return out
    out.lines.append("executing: " + shlex.join(cmd))
    rc = subprocess.run([near, *cmd[1:]], check=False).returncode
    if rc != 0:
        out.exit_code = 5
        out.errors.append(f"bumm-guard: `near` exited with status {rc}")
    return out


def run_verify_deployed(account: str, wasm: str, network: str = "testnet", smoke: list[str] | None = None,
                        crate_dir: str | None = None, rpc_url: str | None = None,
                        dry_run: bool = False) -> GateOutcome:
    """``gate --verify-deployed``: compare on-chain code with ``wasm``, run view-only smoke calls.

    Exit 0 if the hash matches and every smoke view succeeded, 5 otherwise, 2 for bad input. The
    ``post_deploy`` block is merged into ``<crate_dir or .>/.bumm-guard/gate-report.json`` (created if absent).
    Never calls init or a change method and never holds a key.
    """
    if not valid_account(account):
        return GateOutcome(2, errors=[f"bumm-guard: invalid NEAR account id: {account!r}"])
    if not Path(wasm).is_file():
        return GateOutcome(2, errors=[f"bumm-guard: wasm file not found: {wasm}"])
    base = Path(crate_dir) if crate_dir else Path(".")
    if not base.is_dir():
        return GateOutcome(2, errors=[f"bumm-guard: not a directory: {base}"])
    try:
        for spec in smoke or []:
            parse_smoke(spec)
    except SmokeSpecError as e:
        return GateOutcome(2, errors=[f"bumm-guard: {e}"])
    block = verify_deployed(account, wasm, network, smoke, rpc_url)
    ok = post_deploy_ok(block)
    out = GateOutcome(0 if ok else 5)
    local = sha256_file(wasm)
    out.lines.append(f"post-deploy {account} ({network}): on-chain code sha256 {block['code_hash_on_chain']}")
    out.lines.append(f"post-deploy: local wasm sha256 {local}: "
                     + ("MATCH" if block["matches_artifact"] else "MISMATCH (deployed code is not this artifact)"))
    if "error" in block:
        out.errors.append(f"bumm-guard: could not fetch deployed code: {block['error']}")
    for sm in block["smoke"]:
        detail = json.dumps(sm["result"]) if sm["ok"] else sm.get("error", "failed")
        out.lines.append(f"smoke {sm['view']}: {'ok' if sm['ok'] else 'FAILED'}: {detail}")
    out.lines.append("post-deploy verification " + ("passed" if ok else "FAILED"))
    report = base / REPORT_DIR / REPORT_NAME
    if dry_run:
        out.lines.append("dry-run: gate-report.json not written")
        return out
    data: dict = {}
    if report.is_file():
        try:
            loaded = json.loads(report.read_text(encoding="utf-8"))
            data = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            data = {}
    if not data:
        data = {"tool": "bumm-guard", "version": __version__,
                "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "network": network, "account": account,
                "wasm": {"path": wasm, "sha256": local, "exists": True},
                "artifact": {"wasm_sha256": local, "abi_sha256": None},
                "simulate": None, "storage_estimate": None}
    else:
        prev = (data.get("artifact") or {}).get("wasm_sha256")
        if prev and prev != local:
            out.errors.append("bumm-guard: warning: existing gate-report.json is for a different wasm "
                              f"({prev}); post_deploy was compared with {wasm}")
    data["post_deploy"] = block
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    out.report_path = str(report)
    out.lines.append(f"post_deploy written to {report}")
    return out
