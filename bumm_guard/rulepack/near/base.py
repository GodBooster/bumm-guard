"""Rule metadata shared by the detector modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...model import Finding
from .project import Project


@dataclass(frozen=True)
class RuleInfo:
    id: str
    name: str
    default_severity: str
    summary: str
    check: Callable[[Project], list[Finding]]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "default_severity": self.default_severity,
            "summary": self.summary,
        }
