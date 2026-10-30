"""File discovery, crate grouping and rule execution."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .model import Finding
from .rulepack.near import RULES
from .rulepack.near.project import Project
from .rulepack.near.rust_src import SourceFile

DEFAULT_EXCLUDES = ("target", ".git", "node_modules")


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    files_scanned: int = 0
    errors: list[str] = field(default_factory=list)


def discover(paths: list[str], excludes: tuple[str, ...] = DEFAULT_EXCLUDES) -> list[tuple[str, str]]:
    """Return ``(display_path, scan_root)`` for every ``*.rs`` file found.

    Files given explicitly are always included; directories are walked
    recursively, skipping directories named in ``excludes`` (default ``target``).
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for arg in paths:
        p = Path(arg)
        if p.is_file():
            root = str(p.parent)
            cands = [(arg, root)]
        elif p.is_dir():
            cands = []
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = sorted(d for d in dirnames if d not in excludes)
                for fn in sorted(filenames):
                    if fn.endswith(".rs"):
                        cands.append((os.path.join(dirpath, fn), str(p)))
        else:
            raise FileNotFoundError(arg)
        for disp, root in cands:
            real = os.path.realpath(disp)
            if real not in seen:
                seen.add(real)
                found.append((disp, root))
    return found


def _crate_key(display: str, scan_root: str) -> str:
    """Directory of the nearest Cargo.toml above the file, else the scan root."""
    d = Path(os.path.realpath(display)).parent
    for cand in [d, *d.parents]:
        if (cand / "Cargo.toml").is_file():
            return str(cand)
    return os.path.realpath(scan_root)


def scan(paths: list[str], excludes: tuple[str, ...] = DEFAULT_EXCLUDES) -> ScanResult:
    result = ScanResult()
    groups: dict[str, list[SourceFile]] = {}
    for disp, root in discover(paths, excludes):
        try:
            sf = SourceFile.from_path(disp, disp)
        except OSError as e:  # unreadable file: report, keep going
            result.errors.append(f"{disp}: {e}")
            continue
        groups.setdefault(_crate_key(disp, root), []).append(sf)
        result.files_scanned += 1
    for files in groups.values():
        project = Project(files)
        for rule in RULES:
            result.findings.extend(rule.check(project))
    result.findings.sort(key=lambda f: (f.file, f.line, f.rule_id))
    return result
