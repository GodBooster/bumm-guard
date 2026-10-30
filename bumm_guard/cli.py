"""Command line interface: ``scan``, ``rules``, ``fix`` and ``gate``."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .fix import FixRun, run_fix
from .gate import NETWORKS, run_gate
from .model import SEVERITIES, Finding
from .rulepack.near import RULES
from .scanner import DEFAULT_EXCLUDES, ScanResult, scan

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


def render_json(res: ScanResult) -> str:
    return json.dumps({
        "tool": "bumm-guard",
        "version": __version__,
        "files_scanned": res.files_scanned,
        "summary": _summary(res.findings),
        "findings": [f.to_dict() for f in res.findings],
        "errors": res.errors,
    }, indent=2)


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


def render_fix_json(run: FixRun) -> str:
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
    return json.dumps(data, indent=2)


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
    out = run_gate(args.crate_dir, wasm=args.wasm, network=args.network, account=args.account,
                   dry_run=args.dry_run, execute=args.execute)
    if out.blocked and out.scan is not None:
        print(render_text(out.scan))
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
    gp.add_argument("crate_dir", metavar="crate-dir")
    gp.add_argument("--wasm", metavar="PATH", help="built contract; its sha256 goes into the report")
    gp.add_argument("--network", choices=NETWORKS, default="testnet")
    gp.add_argument("--account", metavar="ID", help="NEAR account to deploy to")
    mode = gp.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="scan and print only; do not write gate-report.json")
    mode.add_argument("--execute", action="store_true",
                      help="run the deploy command (testnet only; needs `near` on PATH). Default: print only")
    gp.set_defaults(func=_cmd_gate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
