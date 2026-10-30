"""Deterministic fixes ("hard rules") for BUMM Guard findings.

One fixer per rule (``FIXERS``), each applying the minimal patch the detector spec describes:

  BG-NEAR-001  insert ``#[private]`` above the callback        (auto-applied)
  BG-NEAR-003  ``signer_account_id`` -> ``predecessor_account_id`` in the guard (auto-applied)
  BG-NEAR-002  ``suggested_patch`` text only                   (needs manual rollback)

Only high/critical findings are fixed (002 also gets a suggestion at medium). ``plan_fixes`` scans, runs the fixers and returns the new text per file without touching
disk; ``run_fix`` optionally writes it (iterating a few passes, since one finding is
reported per method) and re-scans. Files below a ``target/`` directory are never modified.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

from ..model import Finding
from ..rulepack.near.rust_src import SourceFile
from ..scanner import DEFAULT_EXCLUDES, ScanResult, scan
from . import callback_ignores_failure, non_private_callback, signer_auth
from .base import Edit, Fixer, FixOutcome

BLOCKING = ("critical", "high")
MAX_PASSES = 5
SUGGEST_ONLY = ("BG-NEAR-002",)   # medium findings (#[callback_unwrap]) also get a suggested patch

FIXERS: dict[str, Fixer] = {
    "BG-NEAR-001": non_private_callback.fix,
    "BG-NEAR-002": callback_ignores_failure.fix,
    "BG-NEAR-003": signer_auth.fix,
}


@dataclass
class FixPlan:
    outcomes: list[FixOutcome] = field(default_factory=list)
    new_text: dict[str, str] = field(default_factory=dict)    # file -> patched text (changed files only)
    old_text: dict[str, str] = field(default_factory=dict)    # file -> text the plan was made against
    scan: ScanResult | None = None


@dataclass
class FixRun:
    mode: str                                   # "dry-run" | "apply"
    outcomes: list[FixOutcome]
    diffs: dict[str, str]                       # file -> unified diff (original -> patched)
    changed: list[str]
    remaining: ScanResult | None                # re-scan after --apply
    errors: list[str] = field(default_factory=list)


def is_protected(path: str) -> bool:
    """Never modify build output: any path component named ``target``."""
    return "target" in Path(path).parts


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace", newline="") as f:   # keep CRLF untouched
        return f.read()


def apply_edits(text: str, edits: list[Edit]) -> str:
    """Apply non-overlapping offset edits (later offsets first so earlier ones stay valid)."""
    ordered = sorted(edits, key=lambda e: (e.start, e.end), reverse=True)
    last = len(text) + 1
    for e in ordered:
        if e.end > last:                         # overlaps an edit already applied: skip, next pass retries
            continue
        text = text[:e.start] + e.text + text[e.end:]
        last = e.start
    return text


def unified(old: str, new: str, path: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                        f"a/{path}", f"b/{path}"))


def plan_fixes(paths: list[str], excludes: tuple[str, ...] = DEFAULT_EXCLUDES) -> FixPlan:
    """Scan ``paths`` and compute (but do not write) the patches for every blocking finding."""
    res = scan(paths, excludes)
    plan = FixPlan(scan=res)
    by_file: dict[str, list[Finding]] = {}
    for f in res.findings:
        by_file.setdefault(f.file, []).append(f)
    for file, findings in by_file.items():
        sf = None
        edits: list[Edit] = []
        for f in findings:
            fixer = FIXERS.get(f.rule_id)
            if fixer is None or not (f.severity in BLOCKING or (f.rule_id in SUGGEST_ONLY and f.severity == "medium")):
                continue
            if is_protected(file):
                plan.outcomes.append(FixOutcome(f, reason="file is under target/: never modified"))
                continue
            if sf is None:
                text = _read(file)
                sf = SourceFile(file, text)
            outcome = fixer(sf, f)
            plan.outcomes.append(outcome)
            edits.extend(outcome.edits)
        if edits and sf is not None:
            new = apply_edits(sf.raw, edits)
            if new != sf.raw:
                plan.old_text[file], plan.new_text[file] = sf.raw, new
    return plan


def run_fix(paths: list[str], apply: bool = False, excludes: tuple[str, ...] = DEFAULT_EXCLUDES) -> FixRun:
    """Dry run (diffs only) or ``apply`` (write, repeat until stable, re-scan)."""
    plan = plan_fixes(paths, excludes)
    if not apply:
        diffs = {f: unified(plan.old_text[f], t, f) for f, t in plan.new_text.items()}
        return FixRun("dry-run", plan.outcomes, diffs, sorted(diffs), None, list(plan.scan.errors))
    originals: dict[str, str] = {}
    applied: list[FixOutcome] = []
    for _ in range(MAX_PASSES):
        if not plan.new_text:
            break
        for f, t in plan.new_text.items():
            originals.setdefault(f, plan.old_text[f])
            with open(f, "w", encoding="utf-8", newline="") as fh:
                fh.write(t)
        for o in plan.outcomes:
            if o.fixable and o.edits:
                o.applied = True
                applied.append(o)
        plan = plan_fixes(paths, excludes)
    # unapplied outcomes come from the last plan only: line numbers in earlier ones are stale
    unique = applied + [o for o in plan.outcomes if not o.applied]
    final = {f: _read(f) for f in originals}
    diffs = {f: unified(originals[f], final[f], f) for f in originals if final[f] != originals[f]}
    return FixRun("apply", unique, diffs, sorted(diffs), scan(paths, excludes), list(plan.scan.errors))
