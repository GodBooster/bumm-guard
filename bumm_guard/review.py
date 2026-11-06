"""``review-job``: BUMM Guard review of an agent's submission to a ``swarm_jobs`` contract.

Reads the job with read-only RPC view calls (``get_job``, ``get_reviewer``), checks that the job is
``Submitted`` and that the local files are exactly the submitted ones (sha256 of ``--wasm`` equals the
job's ``wasm_sha256``, sha256 of ``--simulate-report`` equals its ``report_sha256``), then runs the
deploy gate (scan, simulate-report binding, upgrade proof when the target account already has code,
migration guard) on that wasm for the job's ``target_account`` (or ``--account``).

It writes the review report (docs/FORMATS.md §7) to ``.bumm-guard/review-<job>.json`` and prints ONE
command for the reviewer: ``approve`` with ``gate_report_sha256`` = sha256 of that file when the gate
passed, ``reject`` with the gate's reason when it blocked. A gate outcome of NO CHANGE (the target
account already runs exactly the submitted wasm) is a ``reject`` too: the submission changed nothing,
so there is nothing to pay for. It never signs and never sends; the reviewer runs the command with
their own key. Exit codes: 0 passed (approve printed), 3 blocked or no change (reject printed), 2 bad
input or nothing to review (no command printed).
"""

from __future__ import annotations

import base64
import json
import shlex
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .gate import NETWORKS, REPORT_DIR, run_gate, sha256_file, valid_account
from .storage import RpcError, resolve_rpc_url, rpc_call

REASON_MAX = 500          # the contract's MAX_REASON (bytes)
APPROVE_GAS = "100 Tgas"
REJECT_GAS = "30 Tgas"
SHA256_HEX = set("0123456789abcdef")
NO_CHANGE_REASON = "the target account already runs this exact wasm: nothing was changed by this submission"


@dataclass
class ReviewOutcome:
    exit_code: int
    verdict: str | None = None          # "approve" | "reject" | None (no review was possible)
    lines: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    command: list[str] | None = None
    reason: str | None = None
    report_path: str | None = None
    report_sha256: str | None = None    # = gate_report_sha256 in the approve command
    job: dict | None = None
    gate: dict | None = None


def view_call(account: str, method: str, args: dict, network: str = "testnet", rpc_url: str | None = None):
    """One read-only ``call_function`` query; returns the decoded JSON result. Raises ``RpcError``."""
    url = resolve_rpc_url(network, rpc_url)
    r = rpc_call(url, "query", {"request_type": "call_function", "finality": "final", "account_id": account,
                                "method_name": method,
                                "args_base64": base64.b64encode(json.dumps(args).encode()).decode("ascii")})
    try:
        return json.loads(bytes(r["result"]).decode("utf-8"))
    except (KeyError, TypeError, ValueError) as e:
        raise RpcError(f"{method} on {account}: malformed reply: {e}") from None


def _is_sha256(v) -> bool:
    return isinstance(v, str) and len(v) == 64 and set(v) <= SHA256_HEX


def call_command(contract: str, method: str, args: dict, gas: str, signer: str, network: str) -> list[str]:
    """near-cli-rs 0.30 argv for a change call signed with the keychain (printed, never run here)."""
    return ["near", "contract", "call-function", "as-transaction", contract, method, "json-args",
            json.dumps(args, separators=(",", ":"), ensure_ascii=False), "prepaid-gas", gas,
            "attached-deposit", "0 NEAR", "sign-as", signer, "network-config", network,
            "sign-with-keychain", "send"]


def _reason(lines: list[str], errors: list[str]) -> str:
    """The gate's own refusal text, cut to the contract's limit."""
    picked = [l for l in lines if l.startswith("DEPLOY BLOCKED")] or errors or ["gate blocked"]
    text = " ".join(picked[0].split())
    raw = text.encode("utf-8")
    if len(raw) > REASON_MAX:
        text = raw[:REASON_MAX - 3].decode("utf-8", errors="ignore") + "..."
    return text


def run_review_job(jobs_contract: str, job_id: int, crate_dir: str, wasm: str, simulate_report: str | None = None,
                   account: str | None = None, network: str = "testnet", reviewer: str | None = None,
                   rpc_url: str | None = None, out: str | None = None) -> ReviewOutcome:
    """Review job ``job_id`` of ``jobs_contract``; see the module docstring. Read-only on chain; writes
    only the review report (``out``, default ``.bumm-guard/review-<job_id>.json``)."""
    def bad(msg: str) -> ReviewOutcome:
        return ReviewOutcome(2, errors=[f"bumm-guard: {msg}"])

    if network not in NETWORKS:
        return bad(f"network must be one of {list(NETWORKS)}")
    for what, acc in (("--jobs-contract", jobs_contract), ("--account", account), ("--reviewer", reviewer)):
        if acc is not None and not valid_account(acc):
            return bad(f"invalid NEAR account id for {what}: {acc!r}")
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 0:
        return bad(f"--job must be a non-negative integer, got {job_id!r}")
    if not Path(crate_dir).is_dir():
        return bad(f"not a directory: {crate_dir}")
    if not Path(wasm).is_file():
        return bad(f"wasm file not found: {wasm}")
    if simulate_report is not None and not Path(simulate_report).is_file():
        return bad(f"simulate report not found: {simulate_report}")

    try:
        job = view_call(jobs_contract, "get_job", {"id": job_id}, network, rpc_url)
        onchain_reviewer = view_call(jobs_contract, "get_reviewer", {}, network, rpc_url)
    except RpcError as e:
        return bad(f"cannot read job {job_id} from {jobs_contract} ({network}): {e}. Nothing was reviewed.")
    if job is None:
        return bad(f"job {job_id} does not exist on {jobs_contract} ({network})")
    if not isinstance(job, dict) or not isinstance(onchain_reviewer, str):
        return bad(f"{jobs_contract} does not answer like a swarm_jobs contract (get_job / get_reviewer)")
    res = ReviewOutcome(2, job=job)
    if reviewer is not None and reviewer != onchain_reviewer:
        res.errors.append(f"bumm-guard: {jobs_contract} has reviewer {onchain_reviewer}; approve/reject signed by "
                          f"{reviewer} would be refused by the contract")
        return res
    signer = reviewer or onchain_reviewer
    status = job.get("status")
    if status != "Submitted":
        res.errors.append(f"bumm-guard: job {job_id} is {status}, not Submitted: nothing to review")
        return res
    sub_wasm, sub_report = job.get("wasm_sha256"), job.get("report_sha256")
    if not (_is_sha256(sub_wasm) and _is_sha256(sub_report)):
        res.errors.append(f"bumm-guard: job {job_id} has no valid submitted hashes: {sub_wasm!r}, {sub_report!r}")
        return res
    wasm_sha = sha256_file(wasm)
    res.lines.append(f"job {job_id} on {jobs_contract} ({network}): Submitted by {job.get('agent')}; "
                     f"reward {job.get('reward')} yoctoNEAR; target {job.get('target_account')}")
    if wasm_sha != sub_wasm:
        res.errors.append(f"bumm-guard: --wasm {wasm} has sha256 {wasm_sha}, but job {job_id} submitted {sub_wasm}: "
                          "review exactly the submitted wasm (nothing was reviewed)")
        return res
    res.lines.append(f"wasm sha256 {wasm_sha} = submitted wasm_sha256")
    if simulate_report is None:
        res.errors.append(f"bumm-guard: job {job_id} names report sha256 {sub_report}; pass --simulate-report with "
                          "that file (nothing was reviewed)")
        return res
    report_sha = sha256_file(simulate_report)
    if report_sha != sub_report:
        res.errors.append(f"bumm-guard: --simulate-report {simulate_report} has sha256 {report_sha}, but job {job_id} "
                          f"submitted {sub_report} (nothing was reviewed)")
        return res
    res.lines.append(f"report sha256 {report_sha} = submitted report_sha256")
    target = job.get("target_account")
    if account is not None and target is not None and account != target:
        res.errors.append(f"bumm-guard: --account {account} differs from the job's target_account {target}")
        return res
    target = account or target
    if target is not None and not valid_account(str(target)):
        res.errors.append(f"bumm-guard: job {job_id} has an invalid target_account {target!r}")
        return res

    g = run_gate(crate_dir, wasm=wasm, network=network, account=target, dry_run=True,
                 simulate_report=simulate_report, rpc_url=rpc_url)
    if g.exit_code == 2:
        res.errors.extend(g.errors or ["bumm-guard: the gate refused the input"])
        return res
    gate_lines = [l for l in g.lines if l != "dry-run: gate-report.json not written"]
    gate_lines = ["deploy command for the target account's owner (NOT executed; not part of this job):"
                  if l.startswith("deploy command (NOT executed") else l for l in gate_lines]
    if g.blocked and g.scan is not None and g.scan.findings:
        gate_lines = [f"{f.file}:{f.line}: [{f.severity.upper()}] {f.rule_id} {f.rule_name}"
                      for f in g.scan.findings] + gate_lines
    gate_passed = g.exit_code == 0 and not g.blocked
    # The gate passes a target that already runs this wasm (nothing to deploy, exit 0). For a paid job
    # that is not a pass: the submission changed nothing, so the verdict is reject.
    no_change = gate_passed and (g.deploy or {}).get("status") == "no-change"
    passed = gate_passed and not no_change
    reason = None if passed else NO_CHANGE_REASON if no_change else _reason(g.lines, g.errors)
    res.gate = {"exit_code": g.exit_code, "passed": gate_passed, "messages": gate_lines + g.errors,
                "report": g.report, "upgrade": g.upgrade, "upgrade_check": g.upgrade_check}
    report = {
        "tool": "bumm-guard", "version": __version__, "kind": "review-job",
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "network": network, "jobs_contract": jobs_contract, "job_id": job_id, "reviewer": signer,
        "job": job, "target_account": target,
        "inputs": {"crate_dir": crate_dir, "wasm": wasm, "wasm_sha256": wasm_sha,
                   "simulate_report": simulate_report, "report_sha256": report_sha},
        "checks": {"status_submitted": True, "wasm_sha256_matches": True, "report_sha256_matches": True},
        "gate": res.gate,
        "verdict": "approve" if passed else "reject",
        "reason": reason,
    }
    path = Path(out) if out else Path(REPORT_DIR) / f"review-{job_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    res.report_path, res.report_sha256 = str(path), sha256_file(path)
    res.lines.extend("gate: " + l for l in gate_lines)
    res.lines.append(f"review report written to {path} (sha256 {res.report_sha256})")
    if passed:
        res.exit_code, res.verdict = 0, "approve"
        res.command = call_command(jobs_contract, "approve", {"job_id": job_id, "wasm_sha256": wasm_sha,
                                                              "gate_report_sha256": res.report_sha256},
                                   APPROVE_GAS, signer, network)
        res.lines.append(f"REVIEW PASSED: job {job_id} may be paid. Approve command for {signer} "
                         "(NOT executed; the reviewer signs it):")
    else:
        res.exit_code, res.verdict, res.reason = 3, "reject", reason
        res.command = call_command(jobs_contract, "reject", {"job_id": job_id, "reason": reason},
                                   REJECT_GAS, signer, network)
        res.lines.append(f"REVIEW BLOCKED: {reason}")
        res.lines.append(f"Reject command for {signer} (NOT executed; the reviewer signs it; the agent may resubmit):")
    res.lines.append("  " + shlex.join(res.command))
    return res

