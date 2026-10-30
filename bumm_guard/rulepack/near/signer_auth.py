"""BG-NEAR-003 `signer-auth`.

``env::signer_account_id()`` is the account that signed the *original* transaction, not the
direct caller. Authorising by it lets a malicious contract act on behalf of an owner who was
tricked into calling it. Use ``predecessor_account_id()`` instead.

Scope: methods of ``#[near]`` / ``#[near_bindgen]`` impls that are ``pub`` (or in a trait impl)
and have a body, containing a call ``signer_account_id()`` (any ``env::`` prefix or bare).
  * HIGH: the call sits in a guard: inside ``require!``/``assert!``/``assert_eq!``/``assert_ne!``/
    ``debug_assert*!`` arguments, directly next to ``==``/``!=``, or in the condition of an ``if``.
  * LOW (REVIEW, warning only, no auto-patch): used as an identity/key
    (``let a = env::signer_account_id(); self.map.insert(a, ..)``).
Exclusions: occurrences only inside logging (``log!``, ``log_str``, ``format!``, ``println!`` ...),
``#[cfg(test)]``, method-call form ``.signer_account_id(x)`` (test context builders), and methods
carrying a ``signer-ok`` comment on the lines above the method or inside it.

Choices: one finding per method (the first HIGH occurrence, else the first REVIEW occurrence);
an alias bound by ``let`` stays REVIEW even if later compared (spec: REVIEW = "used as key or
identity"), so only a literal in-guard use is eligible for an automatic patch.
"""

from __future__ import annotations

import re

from ...model import Finding
from .base import RuleInfo
from .project import Project
from .rust_src import enclosing_calls

RULE_ID = "BG-NEAR-003"
RULE_NAME = "signer-auth"

_CALL_RE = re.compile(r"(?<![\w.])(?:[A-Za-z_]\w*::)*signer_account_id\s*\(\s*\)")
_GUARD_MACROS = {"require", "assert", "assert_eq", "assert_ne", "debug_assert", "debug_assert_eq", "debug_assert_ne",
                 "require_eq", "require_ne"}
_LOG_MACROS = {"log", "format", "println", "eprintln", "print", "eprint", "dbg", "write", "writeln",
               "log_str", "log_utf8", "log_utf16"}


def _last(name: str) -> str:
    return name.rstrip("!").split("::")[-1]


def _classify(code: str, start: int, end: int) -> str | None:
    """'guard', 'review' or None (logging only) for the call at code[start:end]."""
    calls = enclosing_calls(code, start)
    names = [_last(n) for n in calls]
    if any(n in _LOG_MACROS for n in names[:1]):
        return None
    if any(n in _GUARD_MACROS for n in names):
        return "guard"
    before = code[max(0, start - 60):start].rstrip()
    after = code[end:end + 20].lstrip()
    before = re.sub(r"(?:near_sdk::)?(?:&\s*)?$", "", before).rstrip()
    if before.endswith(("==", "!=")) or after.startswith(("==", "!=")):
        return "guard"
    # in the condition of an `if`: look back to the start of the statement
    stmt_start = max(code.rfind(";", 0, start), code.rfind("{", 0, start), code.rfind("}", 0, start))
    head = code[stmt_start + 1:start]
    if re.search(r"(?<![\w])if\b", head):
        return "guard"
    return "review"


def check(project: Project) -> list[Finding]:
    findings: list[Finding] = []
    for m in project.entry_methods():
        code = m.file.code
        occurrences = []
        for c in _CALL_RE.finditer(code, m.body_start, m.body_end + 1):
            kind = _classify(code, c.start(), c.end())
            if kind:
                occurrences.append((kind, c.start()))
        if not occurrences:
            continue
        span_comments = m.file.leading_comments(m.item_start) + "\n" + m.file.raw[m.item_start:m.body_end + 1]
        if "signer-ok" in span_comments:
            continue
        guards = [o for o in occurrences if o[0] == "guard"]
        kind, pos = guards[0] if guards else occurrences[0]
        line = m.file.line_of(pos)
        if kind == "guard":
            findings.append(Finding(
                rule_id=RULE_ID, rule_name=RULE_NAME, severity="high",
                file=m.file.path, line=line,
                message=(
                    f"`{m.name}` authorises with env::signer_account_id(), the original transaction signer, "
                    "not the direct caller. If the authorised account is tricked into calling a malicious "
                    "contract, that contract can call this method and pass the check."
                ),
                fix_hint="Replace `signer_account_id` with `predecessor_account_id` in the guard "
                         "(or add `// signer-ok` if the signer semantic is intended).",
                snippet=m.file.line_text(line),
            ))
        else:
            findings.append(Finding(
                rule_id=RULE_ID, rule_name=RULE_NAME, severity="low",
                file=m.file.path, line=line,
                message=(
                    f"REVIEW: `{m.name}` uses env::signer_account_id() as an identity/key. Legitimate in some "
                    "cases (e.g. a per-user record), but never use it for authorisation; no automatic patch."
                ),
                fix_hint="Confirm the signer (not the caller) is really intended; otherwise use "
                         "predecessor_account_id. Add `// signer-ok` to silence.",
                snippet=m.file.line_text(line),
            ))
    return findings


RULE = RuleInfo(
    id=RULE_ID,
    name=RULE_NAME,
    default_severity="high",
    summary="Authorisation via env::signer_account_id() instead of predecessor_account_id().",
    check=check,
)
