"""Command line interface: ``bumm-guard scan <path>...`` and ``bumm-guard rules``."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
