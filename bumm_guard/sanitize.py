"""Display-safe text for strings the tool did not write itself.

A simulate report, a scenario file and a ``get_job`` reply are supplied by someone else (in a guarded
job: by the agent whose work is under review). Their strings (scenario names, hashes, step kinds and
methods, statuses) end up in the gate's and the review's output lines, in the reject ``reason`` and in
the JSON reports. Printed raw, a scenario name with a line break and an escape sequence could hide the
real verdict or draw a fake ``REVIEW PASSED`` block above it. ``clean`` is the one place that makes
such a string safe to print on one line: every character of the Unicode categories Cc (control: ESC,
CR, LF, tab, NUL, DEL, C1), Cf (format: bidi overrides, zero-width), Cs (lone surrogates), Zl and Zp
(line and paragraph separators) is removed, and the rest is cut to ``TEXT_MAX`` characters with ``...``.
"""

from __future__ import annotations

import unicodedata

TEXT_MAX = 200
_DROP = frozenset(("Cc", "Cf", "Cs", "Zl", "Zp"))


def strip_control(text: str) -> str:
    """``text`` without control, format, surrogate and line/paragraph separator characters (no length cut)."""
    return "".join(ch for ch in text if unicodedata.category(ch) not in _DROP)


def is_plain(value, limit: int = TEXT_MAX) -> bool:
    """A string of at most ``limit`` characters that ``strip_control`` leaves unchanged."""
    return isinstance(value, str) and len(value) <= limit and strip_control(value) == value


def clean(value, limit: int = TEXT_MAX) -> str:
    """One printable line of at most ``limit`` characters (plus ``...`` when cut). Non-strings go
    through ``str`` first."""
    text = strip_control(value if isinstance(value, str) else str(value))
    return text if len(text) <= limit else text[:limit] + "..."


def clean_str(value, limit: int = TEXT_MAX):
    """``clean`` for strings; any other value (``None``, a number) is returned unchanged."""
    return clean(value, limit) if isinstance(value, str) else value
