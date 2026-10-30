"""BG-NEAR-002 `callback-ignores-failure`.

An initiator method mutates ``self`` state, then schedules a cross-contract call whose
callback never looks at the promise result, so the state change is never rolled back when
the call fails (receipts are not atomic).

Pair (S, C): S is a method with a ``&mut self`` receiver whose body has
``.then(Self::ext(..)[.with_*(..)]*.C(args))`` and C is an exported method of the same crate.
  A. S mutates state before that ``.then(``: ``self.<path> (-=|+=|*=|/=) ..``, ``self.<path> = ..``,
     or ``self.<path>.(insert|remove|push|pop|set|swap_remove|clear|extend|get_mut|entry)(``.
  B. C does not handle failure: C has no ``#[callback_result]`` and does not read the promise
     result (``promise_result(_checked)``, ``PromiseResult``, ``is_promise_success``); or its
     ``#[callback_result]`` binding is unused / starts with ``_``; or it uses
     ``#[callback_unwrap]`` / ``#[callback_vec]`` (panics on failure, no rollback; severity medium).
Exclusions: C calls a ``self.``/``Self::`` method whose name contains resolve|rollback|revert|
refund|restore; S has no ``&mut self``. Downgrade to info ("pending marker") if no identifier of
the mutating statement appears in the arguments passed to C.

Choices for fewer false positives / clarity:
  * The finding is reported at C (the place the fix goes); S is named in the message.
  * Mutation is looked for only *before* the specific ``.then(`` leading to C.
  * Mutation identifiers: field path segments and identifiers in the statement; method names
    (``x.clone()``) are ignored so they cannot create an accidental overlap.
  * Old ``ext_self::cb(args, &acc, 0, gas)`` syntax is not recognised (near-sdk 5.x only).
"""

from __future__ import annotations

import re

from ...model import Finding
from .base import RuleInfo
from .project import PROMISE_READ_RE, Chain, Project

RULE_ID = "BG-NEAR-002"
RULE_NAME = "callback-ignores-failure"

_MUT_RE = re.compile(
    r"\bself\s*\.\s*[\w.]+?\s*(?:-=|\+=|\*=|/=)"
    r"|\bself\s*\.\s*[\w.]+?\s*=(?![=>])"
    r"|\bself\s*\.\s*[\w.]+?(?:\(\s*\))?\s*\.\s*(?:insert|remove|push|pop|set|swap_remove|clear|extend|get_mut|entry)\s*\("
)
_CUSTOM_HANDLER_RE = re.compile(
    r"\b(?:self\s*\.|Self\s*::)\s*\w*(?:resolve|rollback|revert|refund|restore)\w*\s*\(", re.I
)
_RESULT_PARAM_RE = re.compile(r"#\[\s*callback_result[^\]]*\]\s*(?:mut\s+)?(\w+)\s*:")
_UNWRAP_PARAM_RE = re.compile(r"#\[\s*callback_(?:unwrap|vec)\b")
_NOISE = {"self", "mut", "let", "as", "u8", "u16", "u32", "u64", "u128", "i32", "i64", "i128", "usize", "bool"}


def _statement(pre: str, m: re.Match) -> str:
    end = pre.find(";", m.start())
    return pre[m.start(): len(pre) if end < 0 else end]


def _idents(text: str) -> set[str]:
    out = set()
    for m in re.finditer(r"[A-Za-z_]\w*", text):
        tail = text[m.end():].lstrip()
        if tail.startswith("(") or tail.startswith("!"):  # method / function / macro name
            continue
        if m.group(0) not in _NOISE:
            out.add(m.group(0))
    return out


def _callback_failure_mode(c) -> str | None:
    """None if C handles failure, else 'ignores' | 'unused-result' | 'unwrap'."""
    body = c.body
    if PROMISE_READ_RE.search(body):
        return None
    params = _RESULT_PARAM_RE.findall(c.params)
    if any(not p.startswith("_") and re.search(r"\b" + re.escape(p) + r"\b", body) for p in params):
        return None
    if _CUSTOM_HANDLER_RE.search(body):
        return None
    if _UNWRAP_PARAM_RE.search(c.params):
        return "unwrap"
    return "unused-result" if params else "ignores"


def check(project: Project) -> list[Finding]:
    findings: list[Finding] = []
    exported_by_name: dict[str, list] = {}
    for m in project.methods:
        if m.is_entry and m.has_body:
            exported_by_name.setdefault(m.name, []).append(m)
    seen: set[tuple[int, int, int]] = set()
    for chain in project.chains:
        s: Chain = chain
        if s.source.receiver != "mut":
            continue
        pre = s.pre
        muts = [m for m in _MUT_RE.finditer(pre)]
        if not muts:
            continue
        for c in exported_by_name.get(s.callee, []):
            mode = _callback_failure_mode(c)
            if mode is None:
                continue
            key = (id(c), id(s.source), s.then_pos)
            if key in seen:
                continue
            seen.add(key)
            arg_ids = _idents(s.args)
            overlap = any(_idents(_statement(pre, m)) & arg_ids for m in muts)
            first = re.sub(r"\s+", " ", _statement(pre, muts[0]).strip())
            if not overlap:
                sev = "info"
            elif mode == "unwrap":
                sev = "medium"
            else:
                sev = "high"
            how = {
                "ignores": "never reads the promise result",
                "unused-result": "declares #[callback_result] but never uses the binding",
                "unwrap": "uses #[callback_unwrap]/#[callback_vec], which panics on failure so nothing can be rolled back",
            }[mode]
            findings.append(Finding(
                rule_id=RULE_ID,
                rule_name=RULE_NAME,
                severity=sev,
                file=c.file.path,
                line=c.line,
                message=(
                    f"`{s.source.name}` ({s.source.file.path}:{s.source.line}) mutates state (`{first}`) before "
                    f"scheduling callback `{c.name}`, but `{c.name}` {how}. If the cross-contract call fails "
                    "the state change is never rolled back."
                    + ("" if sev != "info" else " No identifier of the mutation is passed to the callback "
                       "(looks like a pending marker), so severity is informational.")
                ),
                fix_hint=(
                    f"Add `#[callback_result] r: Result<T, PromiseError>` to `{c.name}` and, on `r.is_err()`, undo the "
                    f"mutation made in `{s.source.name}` (pass the needed values through the callback arguments). "
                    "Do not unwrap/expect/require! before the rollback."
                ),
                snippet=c.file.line_text(c.line),
            ))
    return findings


RULE = RuleInfo(
    id=RULE_ID,
    name=RULE_NAME,
    default_severity="high",
    summary="State is changed before a cross-contract call but the callback never handles failure (no rollback).",
    check=check,
)
