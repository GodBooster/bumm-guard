"""BG-NEAR-001 `non-private-callback`.

A callback runs as a separate receipt and is an ordinary public method unless it
carries ``#[private]``. Anyone (e.g. a contract the attacker deployed) can call it
with a forged "successful" result.

Fires when ALL hold:
  1. method is in a ``#[near]`` / ``#[near_bindgen]`` impl, is ``pub`` (or lives in a
     trait impl), has a body and a ``&self`` / ``&mut self`` receiver;
  2. it is a callback: (a) its name is the target of ``.then(Self::ext(..)[.with_*()].name(..))``
     anywhere in the crate, or (b) a parameter carries ``#[callback_result|callback_unwrap|
     callback_vec]``, or (c) the body reads the promise result;
  3. no ``#[private]`` attribute (also ``#[near_sdk::private]``).
Exclusions: explicit ``predecessor == current_account_id`` check (any order, ``assert_eq!``,
local alias) or ``assert_self()``; ``#[init]``; ``#[cfg(test)]`` items; bodiless declarations.
Severity: high if ``&mut self`` or the body moves value (``Promise::new``, ``.transfer(``,
``.function_call(``, ``::ext(`` together with ``with_attached_deposit``); otherwise info.

Ambiguity choices (fewer false positives):
  * ``self`` by value receivers are ignored (spec lists only ``&self``/``&mut self``).
  * Only ``Self::ext`` / own-type / ``*self*``-named ``ext_contract`` aliases count as
    callback scheduling, so calls to *other* contracts never mark local methods.
  * ``pub(crate)`` methods are not exported by near-sdk, hence not reported.
"""

from __future__ import annotations

import re

from ...model import Finding
from .base import RuleInfo
from .project import Project, has_self_check

RULE_ID = "BG-NEAR-001"
RULE_NAME = "non-private-callback"

_MOVES_VALUE_RE = re.compile(r"\bPromise::new\b|\.\s*transfer\s*\(|\.\s*function_call\s*\(")


def check(project: Project) -> list[Finding]:
    findings: list[Finding] = []
    for m in project.entry_methods():
        if m.receiver not in ("ref", "mut"):
            continue
        if m.has_attr("private") or m.has_attr("init"):
            continue
        is_cb, why = project.is_callback(m)
        if not is_cb:
            continue
        if has_self_check(m):
            continue
        body = m.body
        high = (
            m.receiver == "mut"
            or bool(_MOVES_VALUE_RE.search(body))
            or ("::ext" in body and "with_attached_deposit" in body)
        )
        recv = "&mut self" if m.receiver == "mut" else "&self"
        findings.append(Finding(
            rule_id=RULE_ID,
            rule_name=RULE_NAME,
            severity="high" if high else "info",
            file=m.file.path,
            line=m.line,
            message=(
                f"Callback `{m.name}` ({recv}) is publicly callable: it is a callback ({why}) "
                "but has no #[private]. Any account can invoke it with attacker-chosen arguments "
                "and a forged promise result."
                + ("" if high else " It only reads state, so impact is informational.")
            ),
            fix_hint=(
                f"Add `#[private]` directly above `pub fn {m.name}` (after existing attributes), "
                "or assert env::predecessor_account_id() == env::current_account_id()."
                + ("" if high else " Optional: read-only callback.")
            ),
            snippet=m.file.line_text(m.line),
        ))
    return findings


RULE = RuleInfo(
    id=RULE_ID,
    name=RULE_NAME,
    default_severity="high",
    summary="Callback method in a #[near] impl is public: missing #[private].",
    check=check,
)
