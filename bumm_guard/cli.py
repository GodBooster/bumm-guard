"""Command line interface: ``scan``, ``rules``, ``fix``, ``gate`` and ``simulate``."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .fix import FixRun, run_fix
from .gate import NETWORKS, run_gate, run_verify_deployed
from .model import SEVERITIES, Finding
from .rulepack.near import RULES
from .scanner import DEFAULT_EXCLUDES, ScanResult, scan
from .simulate import DEFAULT_OUT, run_simulate

BLOCKING = ("critical", "high")


def _summary(findings: list[Finding]) -> dict:
    by_sev: dict[str, int] = {}
    by_rule: dict[str, int] = {}
    for f in findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
        by_rule[f.rule_id] = by_rule.get(f.rule_id, 0) + 1
    return {
        "total": len(findings),
        "by_severity": {s: by_sev[s] for s in SEVERITIES if s in by_sev},
        "by_rule": dict(sorted(by_rule.items())),
    }


def scan_to_dict(res: ScanResult) -> dict:
    """The payload of ``scan --format json`` (also returned by the MCP ``scan_contract`` tool)."""
    return {
        "tool": "bumm-guard",
        "version": __version__,
        "files_scanned": res.files_scanned,
        "summary": _summary(res.findings),
        "findings": [f.to_dict() for f in res.findings],
        "errors": res.errors,
    }


def render_json(res: ScanResult) -> str:
    return json.dumps(scan_to_dict(res), indent=2)


def render_text(res: ScanResult) -> str:
    lines: list[str] = []
    for f in res.findings:
        lines.append(f"{f.file}:{f.line}: [{f.severity.upper()}] {f.rule_id} {f.rule_name}: {f.message}")
        if f.snippet:
            lines.append(f"    | {f.snippet}")
        lines.append(f"    fix: {f.fix_hint}")
    for e in res.errors:
        lines.append(f"error: {e}")
    s = _summary(res.findings)
    lines.append(f"{s['total']} finding(s) in {res.files_scanned} file(s)"
                 + (": " + ", ".join(f"{k}={v}" for k, v in s["by_severity"].items()) if s["total"] else ""))
    return "\n".join(lines)


def _cmd_scan(args: argparse.Namespace) -> int:
    excludes = tuple(DEFAULT_EXCLUDES) + tuple(args.exclude or ())
    try:
        res = scan(args.paths, excludes)
    except FileNotFoundError as e:
        print(f"bumm-guard: no such file or directory: {e}", file=sys.stderr)
        return 2
    print(render_json(res) if args.format == "json" else render_text(res))
    return 1 if any(f.severity in BLOCKING for f in res.findings) else 0


def fix_to_dict(run: FixRun) -> dict:
    """The payload of ``fix --format json`` (also returned by the MCP ``fix_contract`` tool)."""
    data = {
        "tool": "bumm-guard",
        "version": __version__,
        "mode": run.mode,
        "fixes": [o.to_dict() for o in run.outcomes],
        "files_changed": run.changed,
        "diffs": run.diffs,
        "errors": run.errors,
    }
    if run.remaining is not None:
        data["remaining"] = {"summary": _summary(run.remaining.findings),
                             "findings": [f.to_dict() for f in run.remaining.findings]}
    return data


def render_fix_json(run: FixRun) -> str:
    return json.dumps(fix_to_dict(run), indent=2)


def render_fix_text(run: FixRun) -> str:
    lines: list[str] = []
    for diff in run.diffs.values():
        lines.append(diff.rstrip("\n"))
    for o in run.outcomes:
        f = o.finding
        where = f"{f.file}:{f.line}"
        if o.suggested_patch is not None:
            lines.append(f"{where}: {f.rule_id} not applied ({o.reason}). Suggested patch:")
            lines.extend("    " + l for l in o.suggested_patch.splitlines())
        elif o.applied:
            lines.append(f"{where}: {f.rule_id} applied: {o.reason}")
        elif o.fixable:
            lines.append(f"{where}: {f.rule_id} fixable (dry-run, use --apply): {o.reason}")
        else:
            lines.append(f"{where}: {f.rule_id} not fixed: {o.reason}")
    for e in run.errors:
        lines.append(f"error: {e}")
    if not run.outcomes:
        lines.append("nothing to fix")
    if run.remaining is not None:
        s = _summary(run.remaining.findings)
        lines.append(f"{len(run.changed)} file(s) changed; {s['total']} finding(s) remain after re-scan"
                     + (": " + ", ".join(f"{k}={v}" for k, v in s["by_severity"].items()) if s["total"] else ""))
        if run.remaining.findings:
            lines.append(render_text(run.remaining))
    elif run.outcomes:
        lines.append(f"{len(run.changed)} file(s) would change (dry-run, nothing written)")
    return "\n".join(lines)


def _cmd_fix(args: argparse.Namespace) -> int:
    excludes = tuple(DEFAULT_EXCLUDES) + tuple(args.exclude or ())
    try:
        run = run_fix(args.paths, apply=args.apply, excludes=excludes)
    except FileNotFoundError as e:
        print(f"bumm-guard: no such file or directory: {e}", file=sys.stderr)
        return 2
    print(render_fix_json(run) if args.format == "json" else render_fix_text(run))
    if run.remaining is not None:
        left = run.remaining.findings
    else:  # dry run: nothing was written, so every blocking finding is still there
        left = [o.finding for o in run.outcomes]
    return 1 if any(f.severity in BLOCKING for f in left) else 0


def _cmd_gate(args: argparse.Namespace) -> int:
    if args.verify_deployed is not None:
        clash = [f for f, v in (("--execute", args.execute), ("--abi", args.abi), ("--account", args.account),
                                ("--simulate-report", args.simulate_report),
                                ("--estimate-storage", args.estimate_storage)) if v]
        if clash or not args.wasm:
            print("bumm-guard: --verify-deployed needs --wasm and cannot be combined with "
                  + ", ".join(clash or ["(nothing else)"]), file=sys.stderr)
            return 2
        out = run_verify_deployed(args.verify_deployed, args.wasm, args.network, args.smoke,
                                  crate_dir=args.crate_dir, rpc_url=args.rpc_url, dry_run=args.dry_run)
    else:
        if args.crate_dir is None or args.smoke:
            print("bumm-guard: gate needs <crate-dir> (and --smoke only works with --verify-deployed)",
                  file=sys.stderr)
            return 2
        out = run_gate(args.crate_dir, wasm=args.wasm, network=args.network, account=args.account,
                       dry_run=args.dry_run, execute=args.execute, abi=args.abi,
                       simulate_report=args.simulate_report, estimate_storage_flag=args.estimate_storage,
                       rpc_url=args.rpc_url)
    if out.blocked and out.scan is not None:
        print(render_text(out.scan))
    for line in out.lines:
        print(line)
    for line in out.errors:
        print(line, file=sys.stderr)
    return out.exit_code


def _cmd_simulate(args: argparse.Namespace) -> int:
    out = run_simulate(args.scenarios, wasm=args.wasm, out=args.out, runner=args.runner,
                       validate_only=args.validate_only, upgrade_wasm=args.upgrade_wasm)
    for line in out.lines:
        print(line)
    for line in out.errors:
        print(line, file=sys.stderr)
    return out.exit_code


def _cmd_rules(args: argparse.Namespace) -> int:
    if args.format == "json":
        print(json.dumps([r.to_dict() for r in RULES], indent=2))
    else:
        for r in RULES:
            print(f"{r.id}  {r.name:<26} {r.default_severity:<7} {r.summary}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bumm-guard", description="Static analysis for NEAR (near-sdk-rs 5.x) contracts.")
    p.add_argument("--version", action="version", version=f"bumm-guard {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    sp = sub.add_parser("scan", help="scan .rs files or directories (recursive, skips target/)")
    sp.add_argument("paths", nargs="+", metavar="path")
    sp.add_argument("--format", choices=("json", "text"), default="text")
    sp.add_argument("--exclude", action="append", metavar="DIRNAME",
                    help="extra directory name to skip (repeatable); default skips: " + ", ".join(DEFAULT_EXCLUDES))
    sp.set_defaults(func=_cmd_scan)
    rp = sub.add_parser("rules", help="list available rules")
    rp.add_argument("--format", choices=("json", "text"), default="text")
    rp.set_defaults(func=_cmd_rules)
    fp = sub.add_parser("fix", help="apply deterministic fixes (BG-NEAR-001, -003); 002 gets a suggested patch only")
    fp.add_argument("paths", nargs="+", metavar="path")
    fp.add_argument("--apply", action="store_true", help="write the patches, then re-scan (default: print diffs only)")
    fp.add_argument("--format", choices=("json", "text"), default="text")
    fp.add_argument("--exclude", action="append", metavar="DIRNAME", help="extra directory name to skip (repeatable)")
    fp.set_defaults(func=_cmd_fix)
    gp = sub.add_parser("gate", help="scan a crate and print the deploy command only if no high/critical finding exists")
    gp.add_argument("crate_dir", metavar="crate-dir", nargs="?",
                    help="crate to scan (optional with --verify-deployed: where .bumm-guard/ lives, default .)")
    gp.add_argument("--wasm", metavar="PATH", help="built contract; its sha256 goes into the report")
    gp.add_argument("--abi", metavar="PATH", help="cargo near abi output; its sha256 goes into the report")
    gp.add_argument("--simulate-report", metavar="PATH",
                    help="simulate-report.json (docs/FORMATS.md §3); must match --wasm and have 0 failed scenarios, else exit 3")
    gp.add_argument("--estimate-storage", action="store_true",
                    help="add the storage-staking estimate (read-only RPC call to --network; needs --wasm)")
    gp.add_argument("--rpc-url", metavar="URL", help="override the public RPC endpoint for --network")
    gp.add_argument("--verify-deployed", metavar="ACCOUNT",
                    help="post-deploy check: compare view_code of ACCOUNT with --wasm and run --smoke views; exit 0 ok, 5 not")
    gp.add_argument("--smoke", action="append", metavar="METHOD[:ARGS_JSON]",
                    help="view method to call after --verify-deployed (repeatable; args default {}; init/migrate refused)")
    gp.add_argument("--network", choices=NETWORKS, default="testnet")
    gp.add_argument("--account", metavar="ID", help="NEAR account to deploy to")
    mode = gp.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="scan and print only; do not write gate-report.json")
    mode.add_argument("--execute", action="store_true",
                      help="run the deploy command (testnet only; needs `near` on PATH). Default: print only")
    gp.set_defaults(func=_cmd_gate)
    smp = sub.add_parser("simulate", help="run scenario files (docs/FORMATS.md §2) on a local near-sandbox via bumm-sim")
    smp.add_argument("scenarios", nargs="+", metavar="scenario.json")
    smp.add_argument("--wasm", metavar="PATH", help="use this wasm instead of each scenario's contract.wasm")
    smp.add_argument("--upgrade-wasm", metavar="PATH",
                     help="use this wasm in every upgrade step (§2a) instead of the path in the scenario")
    smp.add_argument("--out", metavar="PATH", help=f"report path or directory (default: {DEFAULT_OUT})")
    smp.add_argument("--runner", metavar="PATH", help="bumm-sim binary (default: $BUMM_SIM, then contracts/target/release/bumm-sim)")
    smp.add_argument("--validate-only", action="store_true", help="check the files against the format; run nothing")
    smp.set_defaults(func=_cmd_simulate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
