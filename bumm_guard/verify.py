"""Post-deploy verification: compare the code on chain with the local wasm and run view-only smoke calls.

Read-only by construction: it uses the RPC ``query`` method (``view_code``, ``call_function``), which
cannot change state, never holds a key, and refuses smoke targets that look like init/migration methods.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

from .storage import RpcError, resolve_rpc_url, rpc_call

POST_DEPLOY_NOTE = "init is never re-called; a contract that fails the smoke view is reported, not fixed"
_METHOD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REFUSED = re.compile(r"^(new|init\w*|migrate\w*)$", re.IGNORECASE)


class SmokeSpecError(ValueError):
    """A ``--smoke`` argument that is malformed or targets a non-view method."""


def parse_smoke(spec: str) -> tuple[str, str]:
    """``METHOD[:ARGS_JSON]`` -> ``(method, canonical args json)``; args default to ``{}``."""
    method, sep, args = spec.partition(":")
    if not _METHOD_RE.match(method):
        raise SmokeSpecError(f"invalid smoke method name: {method!r}")
    if _REFUSED.match(method):
        raise SmokeSpecError(f"refusing smoke call to {method!r}: smoke runs view methods only, "
                             "init/migrate are never called")
    if not sep:
        return method, "{}"
    try:
        json.loads(args)
    except ValueError as e:
        raise SmokeSpecError(f"smoke args for {method} are not valid JSON: {e}") from None
    return method, args


def _decode_result(raw) -> object:
    data = bytes(raw)
    text = data.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _smoke_one(url: str, account: str, method: str, args: str) -> dict:
    entry: dict = {"view": method}
    if args != "{}":
        entry["args"] = json.loads(args)
    try:
        r = rpc_call(url, "query", {"request_type": "call_function", "finality": "final", "account_id": account,
                                    "method_name": method,
                                    "args_base64": base64.b64encode(args.encode("utf-8")).decode("ascii")})
        entry["ok"] = True
        entry["result"] = _decode_result(r["result"])
    except (RpcError, KeyError, TypeError, ValueError) as e:
        entry["ok"] = False
        entry["error"] = str(e)
    return entry


def verify_deployed(account: str, wasm: str | Path, network: str = "testnet", smoke: list[str] | None = None,
                    rpc_url: str | None = None) -> dict:
    """The ``post_deploy`` block (docs/FORMATS.md §4). ``smoke`` entries must already parse (``parse_smoke``).

    ``matches_artifact`` is true only if the on-chain code sha256 equals the sha256 of ``wasm``.
    A transport failure or a missing contract gives ``code_hash_on_chain: null``, ``matches_artifact: false``
    and an ``error`` key (an extra key present only on failure).
    """
    local = hashlib.sha256(Path(wasm).read_bytes()).hexdigest()
    parsed = [parse_smoke(s) for s in (smoke or [])]
    block: dict = {"account": account, "code_hash_on_chain": None, "matches_artifact": False, "smoke": [],
                   "note": POST_DEPLOY_NOTE}
    try:
        url = resolve_rpc_url(network, rpc_url)
        r = rpc_call(url, "query", {"request_type": "view_code", "finality": "final", "account_id": account})
        code = base64.b64decode(r["code_base64"], validate=True)
        block["code_hash_on_chain"] = hashlib.sha256(code).hexdigest()
        block["matches_artifact"] = block["code_hash_on_chain"] == local
    except (RpcError, KeyError, TypeError, ValueError) as e:
        block["error"] = str(e)
        block["smoke"] = [{"view": m, "ok": False, "error": "not run: code could not be fetched"} for m, _ in parsed]
        return block
    block["smoke"] = [_smoke_one(url, account, m, a) for m, a in parsed]
    return block


def post_deploy_ok(block: dict) -> bool:
    return bool(block.get("matches_artifact")) and all(s.get("ok") for s in block.get("smoke", []))
