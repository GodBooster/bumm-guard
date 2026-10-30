"""Fixer for BG-NEAR-003 `signer-auth`: ``signer_account_id`` -> ``predecessor_account_id``.

Only the guard occurrence on the flagged line is rewritten (``env::signer_account_id()`` and
``near_sdk::env::signer_account_id()`` alike; the path prefix is kept). REVIEW findings
(severity low: signer used as an identity/key) are never patched.
"""

from __future__ import annotations

from ..model import Finding
from ..rulepack.near.rust_src import SourceFile
from ..rulepack.near.signer_auth import _CALL_RE, _classify
from .base import Edit, FixOutcome, stale

_OLD, _NEW = "signer_account_id", "predecessor_account_id"


def fix(sf: SourceFile, finding: Finding) -> FixOutcome:
    out = FixOutcome(finding)
    if finding.severity != "high":
        out.reason = "REVIEW finding (identity/key use): no automatic patch"
        return out
    if stale(sf, finding):
        out.reason = "source changed since the scan; re-run the scan"
        return out
    lo = sf._starts[finding.line - 1]
    hi = sf.code.find("\n", lo)
    hi = len(sf.code) if hi < 0 else hi
    for c in _CALL_RE.finditer(sf.code, lo, hi):
        if _classify(sf.code, c.start(), c.end()) != "guard":
            continue
        at = c.start() + c.group(0).index(_OLD)
        out.edits.append(Edit(at, at + len(_OLD), _NEW))
        out.fixable = True
        out.reason = "replace signer_account_id with predecessor_account_id in the guard"
        return out
    out.reason = "guard expression not found on the reported line"
    return out
