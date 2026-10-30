"""MCP server (stdio) exposing BUMM Guard to coding agents.

Entry point: ``bumm-guard-mcp``. Needs the optional extra: ``pip install 'bumm-guard[mcp]'``.
The tool functions below are plain Python (``list_rules``, ``scan_contract``, ``fix_contract``,
``gate_deploy``) and need only the standard library; the ``mcp`` SDK is imported lazily in
``build_server``/``main``.

Safety properties:
  * Every path argument must resolve (symlinks followed) inside the server's current working
    directory, unless the environment variable ``BUMM_GUARD_ALLOW_ANY_PATH=1`` is set.
  * ``fix_contract`` is a dry run unless ``apply`` is true.
  * ``gate_deploy`` never runs a deploy: there is no ``execute`` parameter. It only returns the
    command text.
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
from .gate import BLOCKING, NETWORKS, run_gate
from .rulepack.near import RULES
from .scanner import DEFAULT_EXCLUDES, scan

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
                network: str = "testnet") -> dict:
    _check_path(crate_dir, "crate_dir")
    if wasm is not None:
        _check_path(wasm, "wasm")
    if network not in NETWORKS:
        raise ToolInputError(f"network must be one of {list(NETWORKS)}")
    out = run_gate(crate_dir, wasm=wasm, network=network, account=account, dry_run=False, execute=False)
    if out.exit_code == 2 or out.scan is None:
        raise ToolInputError("; ".join(out.errors) or "gate refused the input")
    findings = [f.to_dict() for f in out.scan.findings]
    return {
        "tool": "bumm-guard",
        "version": __version__,
        "blocked": out.blocked,
        "passed": not out.blocked,
        "network": network,
        "blocking_findings": sum(1 for f in out.scan.findings if f.severity in BLOCKING),
        "findings": findings,
        "report_path": os.path.abspath(out.report_path) if out.report_path else None,
        "deploy_command": shlex.join(out.command) if out.command else None,
        "deploy_executed": False,
        "messages": out.lines + out.errors,
        "note": ("DEPLOY BLOCKED: fix the findings (fix_contract), run the crate tests, then call gate_deploy again. "
                 "Do not deploy around the gate." if out.blocked else
                 "Gate passed. The deploy command above was NOT executed and this tool cannot execute it: "
                 "show it to the human, who runs it. Placeholders (<account-id>, <path/to/contract.wasm>) must be "
                 "replaced by the human."
                 + (" Network is mainnet: only the human decides whether to deploy." if network == "mainnet" else "")),
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
        "is used if absent). `wasm` is the built contract path (optional)."))
    @readable_errors
    def gate_deploy_tool(crate_dir: str, wasm: str | None = None, account: str | None = None,
                         network: str = "testnet") -> dict:
        return gate_deploy(crate_dir, wasm, account, network)

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
