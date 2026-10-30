"""Fixer for BG-NEAR-002 `callback-ignores-failure`: suggestion only, never auto-applied.

Undoing a state change needs semantic knowledge (which values to restore, whether the
callback returns something), so this fixer renders a ``suggested_patch``: a unified-style
hunk over the callback's signature with an added ``#[callback_result] r: Result<(), PromiseError>``
parameter and an ``if r.is_err() { /* TODO: revert .. */ return; }`` skeleton. The file is
left untouched (``applied: false, reason: "needs manual rollback"``).

Variants: a declared-but-unused ``#[callback_result]`` binding only gets the guard;
``#[callback_unwrap] v: T`` becomes ``#[callback_result] v: Result<T, PromiseError>`` plus
``let v = v.unwrap();`` after the guard (a second callback parameter would read promise
result 1, not 0); ``#[callback_vec]`` gets a guard TODO only.
"""

from __future__ import annotations

import difflib
import re

from ..model import Finding
from ..rulepack.near.rust_src import SourceFile, match_close
from .base import FixOutcome, eol_of, line_start, stale

REASON = "needs manual rollback"
_MUT_MSG_RE = re.compile(r"mutates state \(`(.*?)`\) before scheduling")
_RESULT_RE = re.compile(r"#\[\s*callback_result[^\]]*\]\s*(?:mut\s+)?(\w+)\s*:")
_UNWRAP_RE = re.compile(r"#\[\s*callback_unwrap[^\]]*\]\s*(?:mut\s+)?(\w+)\s*:\s*([^,]+?)\s*(?:,|$)")


def _free_name(method) -> str:
    """`r`, unless that identifier is already taken in the callback."""
    for cand in ("r", "cb_result", "promise_result"):
        if not re.search(r"\b" + cand + r"\b", method.params + method.body):
            return cand
    return "failure_check"


def fix(sf: SourceFile, finding: Finding) -> FixOutcome:
    out = FixOutcome(finding, reason=REASON)
    method = next((m for m in sf.methods if m.line == finding.line and m.has_body), None)
    if method is None or stale(sf, finding):
        out.reason = REASON + " (callback not found at the reported line; re-run the scan)"
        return out
    mut = _MUT_MSG_RE.search(finding.message)
    mutation = mut.group(1) if mut else "the state change made before the cross-contract call"
    raw, code = sf.raw, sf.code
    popen = code.find("(", method.name_pos)
    pclose = match_close(code, popen, "(", ")")
    brace = method.body_start
    params = raw[popen + 1:pclose]

    notes: list[str] = []
    cond = None                      # custom failure test (callback_vec); default `<name>.is_err()`
    after_guard: list[str] = []      # lines placed after the guard
    existing = _RESULT_RE.search(params)
    unwrap = _UNWRAP_RE.search(params)
    if existing and not existing.group(1).startswith("_"):
        name, new_params = existing.group(1), params
        notes.append(f"`{name}` is already declared but never checked")
    elif existing:                   # `_r` binding: rename in place
        name = _free_name(method)
        new_params = params[:existing.start(1)] + name + params[existing.end(1):]
    elif unwrap:
        name = unwrap.group(1)
        new_params = (params[:unwrap.start()] + f"#[callback_result] {name}: Result<{unwrap.group(2)}, PromiseError>"
                      + params[unwrap.start() + len(unwrap.group(0)) - (1 if unwrap.group(0).endswith(",") else 0):])
        after_guard = [f"let {name} = {name}.unwrap();"]
        notes.append("#[callback_unwrap] panics on failure, so it is replaced by #[callback_result]")
    elif re.search(r"#\[\s*callback_vec\b", params):
        name, new_params = "r", params
        cond = "/* TODO: any of the promises failed (env::promise_result_checked) */ false"
        notes.append("#[callback_vec] panics if any promise failed; check the results manually")
    else:
        name = _free_name(method)
        new_params = params.rstrip().rstrip(",") + f", #[callback_result] {name}: Result<(), PromiseError>"

    hdr_start = line_start(raw, method.name_pos)
    eol = eol_of(raw)
    eol_pos = raw.find("\n", brace)
    eol_pos = len(raw) if eol_pos < 0 else eol_pos
    rest = raw[brace + 1:eol_pos].rstrip("\r").strip()
    inner = re.match(r"[ \t]*", raw[hdr_start:]).group(0) + "    "
    ret = "return Default::default() /* TODO: failure value */;" if "->" in code[pclose:brace] else "return;"
    skeleton = [
        f"{inner}if {cond or name + '.is_err()'} {{",
        f"{inner}    // TODO: revert `{mutation}`",
        f"{inner}    // (pass the original values through this callback's arguments if needed)",
        f"{inner}    {ret}",
        f"{inner}}}",
    ] + [inner + l for l in after_guard]
    old = raw[hdr_start:eol_pos].rstrip("\r")
    new = (raw[hdr_start:popen + 1] + new_params + raw[pclose:brace + 1]
           + eol + eol.join(skeleton) + ((eol + inner + rest) if rest else ""))
    hunks = list(difflib.unified_diff(old.split("\n"), new.split(eol), f"a/{sf.path}", f"b/{sf.path}",
                                      lineterm="", n=0))[2:]   # drop the ---/+++ header
    base = sf.line_of(hdr_start) - 1                               # hunk numbers are relative to the region
    hunks = [re.sub(r"^@@ -(\d+)(,\d+)? \+(\d+)(,\d+)? @@",
                    lambda m: f"@@ -{int(m[1]) + base}{m[2] or ''} +{int(m[3]) + base}{m[4] or ''} @@", h)
             if h.startswith("@@") else h for h in hunks]
    out.suggested_patch = "\n".join(
        [f"// BG-NEAR-002 suggested patch for `{method.name}` ({sf.path}:{method.line}), NOT applied: {REASON}.",
         "// The rollback must restore exactly what the initiator changed; review the TODO."]
        + [f"// Note: {n}." for n in notes] + hunks)
    return out
