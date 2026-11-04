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


def parse_smoke(spec: str) -> tuple[str, str, str | None]:
    """``METHOD[:ARGS_JSON][=EXPECTED_JSON]`` -> ``(method, args json, expected json or None)``.

    Args default to ``{}``. With ``=EXPECTED`` the view result must equal that JSON value (compared as
    parsed JSON, so ``{"a":1}`` and ``{ "a": 1 }`` are the same). The args JSON is read with a raw
    decoder, so an ``=`` inside an args string does not start the expectation.
    """
    m = re.match(r"^([^:=]*)(.*)$", spec, re.S)
    method, rest = m.group(1), m.group(2)
    if not _METHOD_RE.match(method):
        raise SmokeSpecError(f"invalid smoke method name: {method!r}")
    if _REFUSED.match(method):
        raise SmokeSpecError(f"refusing smoke call to {method!r}: smoke runs view methods only, "
                             "init/migrate are never called")
    args = "{}"
    if rest.startswith(":"):
        try:
            _, end = json.JSONDecoder().raw_decode(rest[1:].lstrip())
        except ValueError as e:
            raise SmokeSpecError(f"smoke args for {method} are not valid JSON: {e}") from None
        head = rest[1:].lstrip()
        args, rest = head[:end], head[end:].lstrip()
        if rest and not rest.startswith("="):
            raise SmokeSpecError(f"smoke args for {method}: unexpected text after the JSON: {rest!r}")
    if not rest:
        return method, args, None
    expected = rest[1:]
    try:
        json.loads(expected)
    except ValueError as e:
        raise SmokeSpecError(f"expected value for {method} is not valid JSON: {e}") from None
    return method, args, expected


def _decode_result(raw) -> object:
    data = bytes(raw)
    text = data.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _smoke_one(url: str, account: str, method: str, args: str, expected: str | None = None) -> dict:
    """One smoke view: ``{view, args?, expected?, ok, result | error}``. With ``expected`` (JSON text)
    the call is ``ok`` only if its result equals that value; a mismatch keeps ``result`` and adds an
    ``error`` saying what was expected."""
    entry: dict = {"view": method}
    if args != "{}":
        entry["args"] = json.loads(args)
    if expected is not None:
        entry["expected"] = json.loads(expected)
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
    if expected is not None and entry["result"] != entry["expected"]:
        entry["ok"] = False
        entry["error"] = (f"expected {json.dumps(entry['expected'], sort_keys=True)}, "
                          f"got {json.dumps(entry['result'], sort_keys=True)}")
    return entry


_NO_CODE_CAUSES = ("UNKNOWN_ACCOUNT", "NO_CONTRACT_CODE")
_NO_CODE_TEXT = ("has not been deployed", "does not exist while viewing")


def onchain_code_sha256(account: str, network: str = "testnet", rpc_url: str | None = None) -> str | None:
    """sha256 of the code deployed on ``account`` (read-only ``view_code``), ``None`` if the account
    has no code or does not exist. Raises ``RpcError`` when the answer is anything else (node
    unreachable, malformed reply): the caller must not mistake "unknown" for "empty"."""
    url = resolve_rpc_url(network, rpc_url)
    try:
        r = rpc_call(url, "query", {"request_type": "view_code", "finality": "final", "account_id": account})
    except RpcError as e:
        if e.cause in _NO_CODE_CAUSES or (e.cause is None and any(t in str(e) for t in _NO_CODE_TEXT)):
            return None
        raise
    try:
        return hashlib.sha256(base64.b64decode(r["code_base64"], validate=True)).hexdigest()
    except (KeyError, TypeError, ValueError) as e:
        raise RpcError(f"view_code on {url}: malformed reply: {e}") from None


def global_contract(account: str, network: str = "testnet", rpc_url: str | None = None) -> str | None:
    """Short description if ``account`` uses a global contract (NEP-591), else ``None``.

    One read-only ``view_account`` query. The field names ``global_contract_hash`` and
    ``global_contract_account_id`` come from nearcore's ``AccountView`` (near-primitives 0.37
    ``views.rs``); both are optional and absent for normal accounts, and both are checked (the hash
    wins). A non-existent account (``UNKNOWN_ACCOUNT``) cannot use a global contract: ``None``. Any
    other ``RpcError`` propagates, and so does a malformed reply: "unknown" is not "no".
    """
    url = resolve_rpc_url(network, rpc_url)
    try:
        r = rpc_call(url, "query", {"request_type": "view_account", "finality": "final", "account_id": account})
    except RpcError as e:
        if e.cause == "UNKNOWN_ACCOUNT" or (e.cause is None and "does not exist" in str(e)):
            return None
        raise
    if not isinstance(r, dict):
        raise RpcError(f"view_account on {url}: malformed reply")
    if r.get("global_contract_hash"):
        return f"global contract hash {r['global_contract_hash']}"
    if r.get("global_contract_account_id"):
        return f"global contract of account {r['global_contract_account_id']}"
    return None


def verify_deployed(account: str, wasm: str | Path, network: str = "testnet", smoke: list[str] | None = None,
                    rpc_url: str | None = None) -> dict:
    """The ``post_deploy`` block (docs/FORMATS.md §4). ``smoke`` entries must already parse (``parse_smoke``).

    ``matches_artifact`` is true only if the on-chain code sha256 equals the sha256 of ``wasm``. A smoke
    entry with an expectation (``METHOD=EXPECTED_JSON``) is ``ok`` only if the view returned that value.
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
        block["smoke"] = [{"view": m, "ok": False, "error": "not run: code could not be fetched"} for m, _, _ in parsed]
        return block
    block["smoke"] = [_smoke_one(url, account, m, a, e) for m, a, e in parsed]
    return block


def post_deploy_ok(block: dict) -> bool:
    return bool(block.get("matches_artifact")) and all(s.get("ok") for s in block.get("smoke", []))
