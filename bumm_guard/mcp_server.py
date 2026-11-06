"""MCP server (stdio) exposing BUMM Guard to coding agents.

Entry point: ``bumm-guard-mcp``. Needs the optional extra: ``pip install 'bumm-guard[mcp]'``.
The tool functions below are plain Python (``list_rules``, ``scan_contract``, ``fix_contract``,
``gate_deploy``, ``estimate_storage``, ``verify_deployed``, ``review_job``) and need only the standard library; the ``mcp`` SDK is imported lazily in
``build_server``/``main``.

Safety properties:
  * Every path argument must resolve (symlinks followed) inside the server's current working
    directory, unless the environment variable ``BUMM_GUARD_ALLOW_ANY_PATH=1`` is set.
  * ``fix_contract`` is a dry run unless ``apply`` is true.
  * ``gate_deploy`` never runs a deploy: there is no ``execute`` parameter. It only returns the
    command text. With ``account`` and ``wasm`` it makes one read-only ``view_code`` query; code on
    the account makes the deploy an upgrade, which needs a passing upgrade scenario in
    ``simulate_report``.
  * ``estimate_storage`` and ``verify_deployed`` are read-only: they make public-RPC queries
    (``EXPERIMENTAL_protocol_config``, ``view_code``, view calls), write nothing, and hold no key.
    ``verify_deployed`` refuses init/migrate smoke methods.
  * ``review_job`` reads a ``swarm_jobs`` job with read-only view calls, runs the gate in dry-run mode,
    writes only its review report and returns the approve/reject command as TEXT: it has no execute,
    sign or send path.
Nothing is ever written to stdout except MCP protocol messages.
"""

from __future__ import annotations

import functools
import os
import shlex
import sys
from pathlib import Path

from . import __version__
from .cli import fix_to_dict, scan_to_dict
from .fix import run_fix
from .gate import BLOCKING, NETWORKS, run_gate, valid_account
from .review import NO_CHANGE_REASON, run_review_job
from .rulepack.near import RULES
from .scanner import DEFAULT_EXCLUDES, scan
from .storage import estimate_storage as _estimate_storage
from .verify import SmokeSpecError, parse_smoke, post_deploy_ok
from .verify import verify_deployed as _verify_deployed

ALLOW_ANY_PATH_ENV = "BUMM_GUARD_ALLOW_ANY_PATH"


class ToolInputError(ValueError):
    """Bad or refused tool input; surfaced to the agent as a tool error."""


def _check_path(path: str, what: str = "path") -> str:
    """Return ``path`` unchanged if it is allowed, else raise ``ToolInputError``."""
    if not isinstance(path, str) or not path.strip():
        raise ToolInputError(f"{what} must be a non-empty string")
    if "\0" in path:
        raise ToolInputError(f"{what} contains a NUL byte")
    if os.environ.get(ALLOW_ANY_PATH_ENV) == "1":
        return path
    cwd = os.path.realpath(os.getcwd())
    real = os.path.realpath(os.path.join(cwd, os.path.expanduser(path)))
    if real != cwd and not real.startswith(cwd + os.sep):
        raise ToolInputError(
            f"{what} {path!r} resolves outside the server working directory ({cwd}); refused. "
            f"Use a path inside it, or start the server with {ALLOW_ANY_PATH_ENV}=1.")
    return path


def _excludes(exclude: list[str] | None) -> tuple[str, ...]:
    if exclude is not None and (not isinstance(exclude, list) or not all(isinstance(x, str) for x in exclude)):
        raise ToolInputError("exclude must be a list of directory names")
    return tuple(DEFAULT_EXCLUDES) + tuple(exclude or ())


def list_rules() -> dict:
    return {"tool": "bumm-guard", "version": __version__,
            "rules": [{"id": r.id, "name": r.name, "severity": r.default_severity, "summary": r.summary}
                      for r in RULES]}


def scan_contract(path: str, exclude: list[str] | None = None) -> dict:
    _check_path(path)
    try:
        res = scan([path], _excludes(exclude))
    except FileNotFoundError:
        raise ToolInputError(f"no such file or directory: {path}") from None
    return scan_to_dict(res)


def fix_contract(path: str, apply: bool = False, exclude: list[str] | None = None) -> dict:
    _check_path(path)
    try:
        run = run_fix([path], apply=bool(apply), excludes=_excludes(exclude))
    except FileNotFoundError:
        raise ToolInputError(f"no such file or directory: {path}") from None
    return fix_to_dict(run)


def gate_deploy(crate_dir: str, wasm: str | None = None, account: str | None = None,
                network: str = "testnet", simulate_report: str | None = None) -> dict:
    _check_path(crate_dir, "crate_dir")
    if wasm is not None:
        _check_path(wasm, "wasm")
    if simulate_report is not None:
        _check_path(simulate_report, "simulate_report")
    if network not in NETWORKS:
        raise ToolInputError(f"network must be one of {list(NETWORKS)}")
    out = run_gate(crate_dir, wasm=wasm, network=network, account=account, dry_run=False, execute=False,
                   simulate_report=simulate_report)
    if out.exit_code == 2 or out.scan is None:
        raise ToolInputError("; ".join(out.errors) or "gate refused the input")
    findings = [f.to_dict() for f in out.scan.findings]
    blocking = sum(1 for f in out.scan.findings if f.severity in BLOCKING)
    return {
        "tool": "bumm-guard",
        "version": __version__,
        "blocked": out.blocked,
        "passed": not out.blocked,
        "network": network,
        "blocking_findings": blocking,
        "findings": findings,
        "report_path": os.path.abspath(out.report_path) if out.report_path else None,
        "deploy_command": shlex.join(out.command) if out.command else None,
        "deploy_executed": False,
        "upgrade": out.upgrade,
        "upgrade_check": out.upgrade_check,
        "deploy": out.deploy,
        "messages": out.lines + out.errors,
        "note": ("DEPLOY BLOCKED: fix the findings (fix_contract), run the crate tests, then call gate_deploy again. "
                 "Do not deploy around the gate." if out.blocked and blocking else
                 "DEPLOY BLOCKED: see messages (an upgrade without a passing upgrade scenario, a migration method "
                 "callable by anyone or without a recognised owner check, migrate args that name sandbox aliases, "
                 "or a simulate report that does not match). Do not deploy around the gate." if out.blocked else
                 "NO CHANGE: the account already runs this exact wasm; there is nothing to deploy and no command "
                 "was produced." if out.deploy and out.deploy.get("status") == "no-change" else
                 "Gate passed. The deploy command above was NOT executed and this tool cannot execute it: "
                 "show it to the human, who runs it. Placeholders (<account-id>, <path/to/contract.wasm>) must be "
                 "replaced by the human."
                 + (" Network is mainnet: only the human decides whether to deploy." if network == "mainnet" else "")),
    }


def _check_wasm(wasm: str, network: str) -> None:
    _check_path(wasm, "wasm")
    if network not in NETWORKS:
        raise ToolInputError(f"network must be one of {list(NETWORKS)}")
    if not os.path.isfile(wasm):
        raise ToolInputError(f"wasm file not found: {wasm}")


def estimate_storage(wasm: str, network: str = "testnet") -> dict:
    """Storage-staking estimate for the wasm (one read-only RPC call; falls back to the documented default)."""
    _check_wasm(wasm, network)
    return {"tool": "bumm-guard", "version": __version__, "network": network,
            "storage_estimate": _estimate_storage(wasm, network)}


def verify_deployed(account: str, wasm: str, network: str = "testnet", smoke: list[str] | None = None) -> dict:
    """Compare the code deployed at ``account`` with ``wasm`` and run view-only smoke calls (read-only)."""
    _check_wasm(wasm, network)
    if smoke is not None and (not isinstance(smoke, list) or not all(isinstance(x, str) for x in smoke)):
        raise ToolInputError("smoke must be a list of 'METHOD[:ARGS_JSON][=EXPECTED_JSON]' strings")
    if not isinstance(account, str) or not valid_account(account):
        raise ToolInputError(f"invalid NEAR account id: {account!r}")
    try:
        for spec in smoke or []:
            parse_smoke(spec)
    except SmokeSpecError as e:
        raise ToolInputError(str(e)) from None
    block = _verify_deployed(account, wasm, network, smoke)
    return {"tool": "bumm-guard", "version": __version__, "network": network, "verified": post_deploy_ok(block),
            "post_deploy": block, "report_written": False,
            "note": "Read-only check; nothing was deployed, signed or written. Use `bumm-guard gate --verify-deployed` "
                    "to also record post_deploy in gate-report.json."}


def review_job(jobs_contract: str, job_id: int, crate_dir: str, wasm: str, simulate_report: str | None = None,
               account: str | None = None, network: str = "testnet", reviewer: str | None = None) -> dict:
    """BUMM Guard review of a swarm_jobs submission; returns the approve/reject command as text (never runs it)."""
    _check_path(crate_dir, "crate_dir")
    _check_wasm(wasm, network)
    if simulate_report is not None:
        _check_path(simulate_report, "simulate_report")
    out = run_review_job(jobs_contract, job_id, crate_dir, wasm, simulate_report=simulate_report, account=account,
                         network=network, reviewer=reviewer)
    if out.exit_code == 2:
        raise ToolInputError("; ".join(out.errors) or "review refused the input")
    return {
        "tool": "bumm-guard", "version": __version__, "network": network,
        "jobs_contract": jobs_contract, "job_id": job_id, "job": out.job,
        "verdict": out.verdict, "passed": out.verdict == "approve", "reason": out.reason,
        "review_report_path": os.path.abspath(out.report_path) if out.report_path else None,
        "gate_report_sha256": out.report_sha256 if out.verdict == "approve" else None,
        "command": shlex.join(out.command) if out.command else None,
        "command_executed": False,
        "gate": out.gate,
        "messages": out.lines + out.errors,
        "note": ("Review passed. The approve command above was NOT executed and this tool cannot execute it: show it "
                 "to the human reviewer, who signs it with their own key." if out.verdict == "approve" else
                 "Review blocked: the target account already runs exactly the submitted wasm, so this submission "
                 "changed nothing. The reject command above was NOT executed: show it to the human reviewer. Do not "
                 "approve it." if out.reason == NO_CHANGE_REASON else
                 "Review blocked. The reject command above was NOT executed: show it to the human reviewer. Do not "
                 "approve around the gate."),
    }


def build_server():
    """Create the MCP server object (imports the optional ``mcp`` SDK)."""
    try:
        from mcp.server.mcpserver import MCPServer                   # mcp 2.x
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError:
        from mcp.server.fastmcp import FastMCP as MCPServer          # mcp 1.x
        from mcp.server.fastmcp.exceptions import ToolError

    server = MCPServer("bumm-guard")

    def readable_errors(fn):
        """Turn ToolInputError into the SDK's ToolError so the agent sees the message, not a generic failure."""
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ToolInputError as e:
                raise ToolError(str(e)) from None
        return wrapper

    @server.tool(name="list_rules", description=(
        "List the BUMM Guard rules for NEAR (near-sdk-rs 5.x) contracts. Returns {rules: [{id, name, "
        "severity, summary}]}. Read-only, no arguments."))
    @readable_errors
    def list_rules_tool() -> dict:
        return list_rules()

    @server.tool(name="scan_contract", description=(
        "Statically scan NEAR Rust source for known defect classes (BG-NEAR-001 non-private callback, "
        "BG-NEAR-002 callback ignores failure, BG-NEAR-003 signer-based auth). `path` is a .rs file or a "
        "directory (recursive; skips target/, .git, node_modules), relative to the server working directory. "
        "`exclude` adds directory names to skip. Returns the same JSON as `bumm-guard scan --format json`: "
        "{files_scanned, summary, findings[{rule_id, severity, file, line, message, fix_hint, snippet}], errors}. "
        "severity high/critical findings block deploy. Read-only; never modifies files."))
    @readable_errors
    def scan_contract_tool(path: str, exclude: list[str] | None = None) -> dict:
        return scan_contract(path, exclude)

    @server.tool(name="fix_contract", description=(
        "Compute (default) or apply deterministic fixes for high/critical findings: BG-NEAR-001 inserts "
        "#[private]; BG-NEAR-003 replaces signer_account_id with predecessor_account_id; BG-NEAR-002 only gets a "
        "suggested_patch text and is never auto-applied. DEFAULT IS DRY-RUN (apply=false): returns unified diffs "
        "per file in `diffs` and writes nothing. Set apply=true ONLY after a human has reviewed the diff and "
        "agreed: it then writes the files in place and returns `remaining` (re-scan result). Files under "
        "target/ are never modified. Review the diff; a fix is not proof the contract is safe."))
    @readable_errors
    def fix_contract_tool(path: str, apply: bool = False, exclude: list[str] | None = None) -> dict:
        return fix_contract(path, apply, exclude)

    @server.tool(name="gate_deploy", description=(
        "Pre-deploy gate for a NEAR contract crate directory: scans it and reports blocked=true if any "
        "high/critical finding exists, else passed=true, writes <crate_dir>/.bumm-guard/gate-report.json "
        "(findings counts, sha256 of every .rs file and of `wasm`) and returns `deploy_command` as TEXT. "
        "THIS TOOL NEVER EXECUTES A DEPLOY and has no execute parameter; the human runs the command. "
        "A blocked gate must not be bypassed. Use network=mainnet only if the human asked for it; mainnet "
        "deployment is always the human's decision. `account` is a NEAR account id (optional, a placeholder "
        "is used if absent). `wasm` is the built contract path (optional). `simulate_report` is a "
        "simulate-report.json from `bumm-guard simulate` (optional; must be for this wasm). With both `account` and "
        "`wasm` the gate reads the account's code (one read-only RPC view_code on `network`): if the account "
        "already runs other code this is an UPGRADE of a live contract with state, and the gate blocks unless "
        "`simulate_report` has a passing upgrade scenario from exactly that on-chain code to exactly `wasm`; "
        "then `deploy_command` repeats that scenario's migration call and `upgrade` describes the proof (incl. "
        "`post_upgrade_steps`). If the account already runs exactly `wasm`: passed, `deploy.status` is 'no-change' "
        "and `deploy_command` is null (nothing to deploy)."))
    @readable_errors
    def gate_deploy_tool(crate_dir: str, wasm: str | None = None, account: str | None = None,
                         network: str = "testnet", simulate_report: str | None = None) -> dict:
        return gate_deploy(crate_dir, wasm, account, network, simulate_report)

    @server.tool(name="estimate_storage", description=(
        "Estimate the storage-staking balance that deploying `wasm` locks on its account: code_bytes (file size), "
        "storage_amount_per_byte_yocto (from the public RPC EXPERIMENTAL_protocol_config for `network`, falling back "
        "to the documented 1e19 yocto/byte with source 'default (RPC unavailable)'), and locked_near_for_code. "
        "state_bytes_after_init is null unless a simulate report measured it. Storage staking is a locked balance, "
        "not a fee. READ-ONLY: one public RPC query, no key, no transaction, nothing written. Gas is not estimated."))
    @readable_errors
    def estimate_storage_tool(wasm: str, network: str = "testnet") -> dict:
        return estimate_storage(wasm, network)

    @server.tool(name="verify_deployed", description=(
        "After the HUMAN has deployed: fetch the code at `account` (RPC view_code), sha256 it and compare with the "
        "local `wasm` (matches_artifact), and run each `smoke` entry ('METHOD[:ARGS_JSON][=EXPECTED_JSON]', args "
        "default {}; with =EXPECTED the view result must equal that JSON, else ok=false) as a VIEW call, recording "
        "ok/result or the error. READ-ONLY: never deploys, never signs, never calls init, "
        "migrate or any change method (such smoke names are refused). A failing smoke is reported, not fixed."))
    @readable_errors
    def verify_deployed_tool(account: str, wasm: str, network: str = "testnet",
                             smoke: list[str] | None = None) -> dict:
        return verify_deployed(account, wasm, network, smoke)

    @server.tool(name="review_job", description=(
        "BUMM Guard review of an agent's submission to a swarm_jobs contract (guarded jobs: the reward is paid only "
        "after review). Reads job `job_id` of `jobs_contract` with READ-ONLY RPC view calls (get_job, get_reviewer), "
        "requires status Submitted, sha256(`wasm`) == the job's wasm_sha256 and sha256(`simulate_report`) == its "
        "report_sha256, then runs the deploy gate (scan, report binding, upgrade proof if the target account "
        "already has code, migration guard) for the job's target_account (or `account`). Writes the review report "
        "(.bumm-guard/review-<job>.json) and returns `verdict` approve|reject and `command`: the near-cli "
        "`approve` call (with gate_report_sha256 = sha256 of that report) or the `reject` call with the gate's "
        "reason, as TEXT. A target account that already runs exactly `wasm` is a reject too (nothing was changed "
        "by the submission). THIS TOOL NEVER SIGNS OR SENDS anything and has no execute parameter; the human "
        "reviewer runs the command. Bad input or a job that is not Submitted is a tool error."))
    @readable_errors
    def review_job_tool(jobs_contract: str, job_id: int, crate_dir: str, wasm: str,
                        simulate_report: str | None = None, account: str | None = None,
                        network: str = "testnet", reviewer: str | None = None) -> dict:
        return review_job(jobs_contract, job_id, crate_dir, wasm, simulate_report, account, network, reviewer)

    return server


def main() -> None:
    try:
        server = build_server()
    except ImportError:
        print("bumm-guard-mcp: the 'mcp' package is not installed. Install it with: pip install 'bumm-guard[mcp]'",
              file=sys.stderr)
        sys.exit(2)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
