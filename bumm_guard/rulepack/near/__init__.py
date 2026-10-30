"""NEAR (near-sdk-rs 5.x) rule-pack registry."""

from __future__ import annotations

from . import callback_ignores_failure, non_private_callback, signer_auth
from .base import RuleInfo

RULES: list[RuleInfo] = [
    non_private_callback.RULE,
    callback_ignores_failure.RULE,
    signer_auth.RULE,
]


def get_rules() -> list[RuleInfo]:
    return list(RULES)


def get_rule(rule_id_or_name: str) -> RuleInfo | None:
    key = rule_id_or_name.lower()
    for r in RULES:
        if key in (r.id.lower(), r.name.lower()):
            return r
    return None
