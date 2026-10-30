"""Deploy gate: scan a crate, refuse to hand out a deploy command while blocking findings exist.

``run_gate`` returns a ``GateOutcome`` (exit code + lines to print); the CLI does the printing.
Exit codes: 0 clean, 2 refused/bad input, 3 DEPLOY BLOCKED (high/critical finding),
4 ``--execute`` but ``near`` not on PATH, 5 ``near`` ran and failed.

Nothing is executed by default: the exact ``near contract deploy`` command (near-cli-rs 0.30
syntax) is only printed. ``--execute`` runs it, testnet only. When the gate blocks, no deploy
is ever started and a stale ``gate-report.json`` from an earlier clean run is removed so it
cannot be mistaken for a pass.
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


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def valid_account(account: str) -> bool:
    return 2 <= len(account) <= 64 and bool(_ACCOUNT_RE.match(account))


def deploy_command(account: str, wasm: str, network: str) -> list[str]:
    """near-cli-rs 0.30 argv; a leading ``-`` in the path is neutralised so it is not read as a flag."""
    path = "./" + wasm if wasm.startswith("-") else wasm
    return ["near", "contract", "deploy", account, "use-file", path, "without-init-call",
            "network-config", network, "sign-with-keychain", "send"]


def build_report(crate: Path, res: ScanResult, wasm: str | None, network: str, account: str | None) -> dict:
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
    }


def run_gate(crate_dir: str, wasm: str | None = None, network: str = "testnet", account: str | None = None,
             dry_run: bool = False, execute: bool = False) -> GateOutcome:
    """Scan, decide, optionally write the report and (testnet only) run the deploy."""
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
    data = build_report(crate, res, wasm, network, account)
    if wasm and not data["wasm"]["exists"]:
        out.errors.append(f"bumm-guard: warning: wasm not found: {wasm} (sha256 not recorded)")
    if dry_run:
        out.lines.append("dry-run: gate-report.json not written")
    else:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        out.report_path = str(report)
        out.lines.append(f"gate passed: report written to {report}")
    cmd = deploy_command(account or ACCOUNT_PLACEHOLDER, wasm or WASM_PLACEHOLDER, network)
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
