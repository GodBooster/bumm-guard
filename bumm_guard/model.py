"""Finding data model.

``severity`` is lower-case ``critical|high|medium|low|info``; ``to_dict()`` is the
JSON shape used by the CLI, the gate report and the MCP tools.
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
