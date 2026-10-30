"""Finding data model.

Field names follow the BUMM backend where sensible (``severity`` is lower-case
``critical|high|medium|low|info``; ``Finding.to_bumm_vulnerability`` yields a dict
with the exact keys of ``app.pipeline.state.Vulnerability``: severity, category,
title, description, code_location, suggested_fix, source).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

SEVERITIES = ("critical", "high", "medium", "low", "info")


@dataclass(frozen=True)
class Finding:
    rule_id: str          # e.g. "BG-NEAR-001"
    severity: str         # one of SEVERITIES
    file: str             # path as given on the command line
    line: int             # 1-based
    message: str
    fix_hint: str
    snippet: str          # the offending source line, stripped
    rule_name: str = ""   # e.g. "non-private-callback"

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def code_location(self) -> str:
        return f"{self.file}:{self.line}"

    def to_bumm_vulnerability(self) -> dict:
        """Map to the BUMM ``Vulnerability`` schema (for backend integration)."""
        return {
            "severity": self.severity,
            "category": self.rule_name.replace("-", "_") or self.rule_id,
            "title": f"{self.rule_id} {self.rule_name}".strip(),
            "description": self.message,
            "code_location": self.code_location,
            "suggested_fix": self.fix_hint,
            "source": "custom_check",
        }
