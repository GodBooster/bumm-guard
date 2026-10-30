"""A crate-level view over parsed source files, shared by the detectors.

The callback index answers "which exported methods are scheduled as
callbacks anywhere in this crate?" so that BG-NEAR-001 and BG-NEAR-002 agree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .rust_src import Method, SourceFile, match_close

# `env::promise_result(i)`, `promise_result_checked`, `PromiseResult`, `is_promise_success`
PROMISE_READ_RE = re.compile(r"\bpromise_result(?:_checked)?\s*\(|\bis_promise_success\b|\bPromiseResult\b")
CALLBACK_PARAM_RE = re.compile(r"#\[\s*callback_(?:result|unwrap|vec)\b")

_EXT_RE = re.compile(r"\b(?:(\w+)\s*::\s*)?ext(?:_self)?\s*\(")
_CHAIN_STEP_RE = re.compile(r"\s*\.\s*(\w+)\s*(?:::\s*<[^>]*>)?\s*\(")
_PRED = r"(?:\w+::)*predecessor_account_id\s*\(\s*\)"
_CUR = r"(?:\w+::)*current_account_id\s*\(\s*\)"
_SELFCHECK_RES = (
    re.compile(_PRED + r"\s*(?:==|!=|,)\s*&?\s*" + _CUR),
    re.compile(_CUR + r"\s*(?:==|!=|,)\s*&?\s*" + _PRED),
    re.compile(r"\bassert_self\s*\("),
)


@dataclass
class Chain:
    """One ``.then(Self::ext(..)[.with_*(..)]*.callee(args))`` occurrence."""
    source: Method          # method containing the chain (the "initiator")
    then_pos: int           # absolute position of `.then(`
    callee: str
    args: str               # text of the callee's argument list

    @property
    def pre(self) -> str:
        """Initiator body text before the `.then(`."""
        return self.source.file.code[self.source.body_start:self.then_pos]


def has_self_check(method: Method) -> bool:
    """True if the body asserts ``predecessor == current_account_id`` in any form.

    Recognised: direct comparison / ``assert_eq!`` in either order, ``assert_self()``,
    and a local alias (``let caller = env::predecessor_account_id();`` ...) that is
    compared with ``current_account_id()`` in the same statement.
    """
    body = method.body
    if any(r.search(body) for r in _SELFCHECK_RES):
        return True
    aliases = re.findall(r"\blet\s+(?:mut\s+)?(\w+)\s*(?::[^=;]+)?=\s*[^;]*predecessor_account_id\s*\(\s*\)", body)
    if aliases:
        for stmt in re.split(r"[;{}]", body):
            if "current_account_id" not in stmt:
                continue
            if any(re.search(r"\b" + re.escape(a) + r"\b", stmt) for a in aliases) and re.search(
                r"==|!=|assert_eq!|assert_ne!|require_eq", stmt
            ):
                return True
    return False


@dataclass
class Project:
    """All files of one crate (or one scan root when no Cargo.toml is found)."""
    files: list[SourceFile]
    methods: list[Method] = field(init=False, default_factory=list)
    chains: list[Chain] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.methods = [m for f in self.files for m in f.methods]
        self.contract_types = {m.impl.self_type for m in self.methods if m.impl.exported and m.impl.self_type}
        self.chains = [c for m in self.methods if m.has_body for c in self._chains_in(m)]
        self.callback_sources: dict[str, list[Chain]] = {}
        for c in self.chains:
            self.callback_sources.setdefault(c.callee, []).append(c)

    # -- callback index ----------------------------------------------------

    def _prefix_ok(self, prefix: str | None) -> bool:
        """Is `<prefix>::ext(..)` a call to *this* contract (not another one)?

        Accepts ``Self``, the contract's own type name, or an ``ext_contract``
        alias that mentions "self" (``ext_self``) or "callback". Foreign
        ``ext_ft::ext(..).ft_transfer()`` must not turn a local method that
        happens to share the name into a "callback".
        """
        if prefix is None:
            return False
        low = prefix.lower()
        return prefix == "Self" or prefix in self.contract_types or "self" in low or "callback" in low

    def _chains_in(self, m: Method) -> list[Chain]:
        code = m.file.code
        out: list[Chain] = []
        for t in re.finditer(r"\.\s*then\s*\(", code[m.body_start:m.body_end + 1]):
            tpos = m.body_start + t.start()
            op = m.body_start + t.end() - 1
            cl = match_close(code, op, "(", ")")
            if cl < 0:
                continue
            inner_start = op + 1
            inner = code[inner_start:cl]
            em = _EXT_RE.search(inner)
            if not em or not self._prefix_ok(em.group(1)):
                continue
            k = match_close(inner, em.end() - 1, "(", ")")
            if k < 0:
                continue
            k += 1
            while True:
                sm = _CHAIN_STEP_RE.match(inner, k)
                if not sm:
                    break
                pc = match_close(inner, sm.end() - 1, "(", ")")
                if pc < 0:
                    break
                if sm.group(1).startswith("with_"):
                    k = pc + 1
                    continue
                out.append(Chain(m, tpos, sm.group(1), inner[sm.end():pc]))
                break
        return out

    def callback_names(self) -> set[str]:
        return set(self.callback_sources)

    def is_callback(self, m: Method) -> tuple[bool, str]:
        """(is it a callback?, human-readable reason)."""
        if m.name in self.callback_sources:
            c = self.callback_sources[m.name][0]
            return True, "scheduled via .then() in `%s` (%s:%d)" % (
                c.source.name, c.source.file.path, c.source.line)
        if CALLBACK_PARAM_RE.search(m.params):
            return True, "takes a #[callback_result]/#[callback_unwrap]/#[callback_vec] parameter"
        if PROMISE_READ_RE.search(m.body):
            return True, "reads the promise result in its body"
        return False, ""

    def entry_methods(self) -> list[Method]:
        return [m for m in self.methods if m.is_entry and m.has_body]
