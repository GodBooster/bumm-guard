"""Fixer for BG-NEAR-001 `non-private-callback`: insert ``#[private]`` above the callback.

The attribute goes on its own line directly above ``pub fn <callback>`` (i.e. after the
existing attributes), with the indentation of that line. If the attributes share the line
with ``pub fn`` (``#[payable] pub fn f``) it is inserted inline before ``pub``.
"""

from __future__ import annotations

from ..model import Finding
from ..rulepack.near.rust_src import SourceFile, attrs_forward
from .base import Edit, FixOutcome, eol_of, line_start, stale


def fix(sf: SourceFile, finding: Finding) -> FixOutcome:
    out = FixOutcome(finding)
    if stale(sf, finding):
        out.reason = "source changed since the scan; re-run the scan"
        return out
    method = next((m for m in sf.methods if m.line == finding.line and m.is_entry), None)
    if method is None:
        out.reason = "callback method not found at the reported line"
        return out
    if method.has_attr("private"):
        out.reason = "already has #[private]"
        return out
    _, head = attrs_forward(sf.code, method.item_start)   # first token after the attributes: `pub`/`fn`
    ls = line_start(sf.raw, head)
    prefix = sf.raw[ls:head]
    if prefix.strip():
        edit = Edit(head, head, "#[private] ")
    else:
        edit = Edit(ls, ls, f"{prefix}#[private]{eol_of(sf.raw)}")
    out.edits.append(edit)
    out.fixable = True
    out.reason = f"insert #[private] above `{method.name}`"
    return out
