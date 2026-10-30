"""Shared types for the deterministic fixers.

A fixer looks at one ``Finding`` plus the parsed ``SourceFile`` it points into and
returns a ``FixOutcome``: either byte-offset ``Edit`` objects (applied by the engine) or,
for rules that need semantic judgement, a ``suggested_patch`` text and ``applied=False``.
Offsets index ``SourceFile.raw``; ``mask_source`` keeps offsets 1:1, so positions found
in the masked ``code`` are valid in ``raw``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..model import Finding
from ..rulepack.near.rust_src import SourceFile


@dataclass(frozen=True)
class Edit:
    start: int        # offset of the first replaced char
    end: int          # offset after the last replaced char (== start for a pure insert)
    text: str         # replacement


@dataclass
class FixOutcome:
    finding: Finding
    applied: bool = False                 # True only once the engine wrote the edits
    fixable: bool = False                 # edits are present (would be / were applied)
    reason: str = ""                      # why nothing was (or can be) applied, or a short note
    edits: list[Edit] = field(default_factory=list)
    suggested_patch: str | None = None    # text block for rules that are not auto-applied

    def to_dict(self) -> dict:
        d = {
            "rule_id": self.finding.rule_id,
            "file": self.finding.file,
            "line": self.finding.line,
            "severity": self.finding.severity,
            "fixable": self.fixable,
            "applied": self.applied,
            "reason": self.reason,
        }
        if self.suggested_patch is not None:
            d["suggested_patch"] = self.suggested_patch
        return d


Fixer = Callable[[SourceFile, Finding], FixOutcome]


def eol_of(text: str) -> str:
    """Line terminator used by the file (``\\r\\n`` if any, else ``\\n``)."""
    return "\r\n" if "\r\n" in text else "\n"


def line_start(raw: str, pos: int) -> int:
    """Offset of the first char of the line containing ``pos``."""
    return raw.rfind("\n", 0, pos) + 1


def stale(sf: SourceFile, finding: Finding) -> bool:
    """True if the flagged line no longer matches the finding's snippet (file changed since scan)."""
    return sf.line_text(finding.line) != finding.snippet
