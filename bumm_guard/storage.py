"""Read-only NEAR RPC helpers and the storage-staking estimate for the deploy gate.

Everything that talks to the network goes through ``rpc_call`` (stdlib ``urllib``, 10 s timeout,
read-only methods only: ``EXPERIMENTAL_protocol_config`` and ``query``). The RPC URL table lives
here and nowhere else. No key is ever used and nothing is signed or sent.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib.parse import urlparse

RPC_URLS = {
    "testnet": "https://rpc.testnet.near.org",
    "mainnet": "https://rpc.mainnet.near.org",
}
RPC_TIMEOUT_S = 10
# Documented protocol value: 1 NEAR per 100 kB, i.e. 1e19 yoctoNEAR per byte.
DEFAULT_PER_BYTE = 10**19
YOCTO_PER_NEAR = 10**24
STORAGE_NOTE = ("storage staking is a locked balance, not a fee; state bytes are measured by simulate "
                "when available")


class RpcError(Exception):
    """Transport failure or an error object returned by the RPC node."""


def resolve_rpc_url(network: str, rpc_url: str | None = None) -> str:
    url = rpc_url or RPC_URLS.get(network)
    if not url:
        raise RpcError(f"unknown network: {network!r}")
    if urlparse(url).scheme not in ("http", "https"):
        raise RpcError(f"RPC url must be http(s): {url!r}")
    return url


def rpc_call(url: str, method: str, params, timeout: float = RPC_TIMEOUT_S):
    """One JSON-RPC POST; returns ``result`` or raises ``RpcError``."""
    if urlparse(url).scheme not in ("http", "https"):
        raise RpcError(f"RPC url must be http(s): {url!r}")
    body = json.dumps({"jsonrpc": "2.0", "id": "bumm-guard", "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": "bumm-guard"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
        raise RpcError(f"{method} on {url} failed: {e}") from None
    if not isinstance(payload, dict):
        raise RpcError(f"{method} on {url}: unexpected response")
    if payload.get("error"):
        err = payload["error"]
        msg = err.get("data") or err.get("message") or err if isinstance(err, dict) else err
        raise RpcError(f"{method} on {url}: {msg}")
    result = payload.get("result")
    if isinstance(result, dict) and result.get("error"):
        raise RpcError(f"{method} on {url}: {result['error']}")
    return result


def fetch_per_byte(network: str, rpc_url: str | None = None) -> tuple[int, str]:
    """``(yocto per byte, source text)``; falls back to the documented default when the RPC fails."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        url = resolve_rpc_url(network, rpc_url)
        result = rpc_call(url, "EXPERIMENTAL_protocol_config", {"finality": "final"})
        per_byte = int(result["runtime_config"]["storage_amount_per_byte"])
        if per_byte <= 0:
            raise ValueError("non-positive storage_amount_per_byte")
        return per_byte, f"EXPERIMENTAL_protocol_config via RPC {network}, {today}"
    except (RpcError, KeyError, TypeError, ValueError):
        return DEFAULT_PER_BYTE, "default (RPC unavailable)"


def format_near(yocto: int, places: int = 4) -> str:
    q = Decimal(1).scaleb(-places)
    return str((Decimal(yocto) / YOCTO_PER_NEAR).quantize(q, rounding=ROUND_HALF_UP))


def locked_near(num_bytes: int, per_byte: int) -> str:
    """``num_bytes * per_byte`` yoctoNEAR as a NEAR string with 4 decimals (exact integer arithmetic)."""
    return format_near(num_bytes * per_byte)


def simulate_state_bytes(report: dict | None) -> int | None:
    """``storage_usage`` from a simulate report (top level, else the largest per-scenario value). Never guessed."""
    if not isinstance(report, dict):
        return None
    top = report.get("storage_usage")
    if isinstance(top, int) and not isinstance(top, bool) and top >= 0:
        return top
    vals = [s["storage_usage"] for s in report.get("scenarios", []) if isinstance(s, dict)
            and isinstance(s.get("storage_usage"), int) and not isinstance(s.get("storage_usage"), bool)]
    return max(vals) if vals else None


def account_info(account: str, network: str, rpc_url: str | None = None) -> dict:
    """Current ``storage_usage`` and balance of ``account`` (``view_account``); an ``error`` key on failure."""
    try:
        url = resolve_rpc_url(network, rpc_url)
        r = rpc_call(url, "query", {"request_type": "view_account", "finality": "final", "account_id": account})
        amount = int(r["amount"])
        return {"id": account, "storage_usage": int(r["storage_usage"]),
                "amount_yocto": str(amount), "amount_near": format_near(amount)}
    except (RpcError, KeyError, TypeError, ValueError) as e:
        return {"id": account, "error": str(e)}


def estimate_storage(wasm: str | Path, network: str = "testnet", rpc_url: str | None = None,
                     account: str | None = None, state_bytes: int | None = None) -> dict:
    """The ``storage_estimate`` block of the gate report (docs/FORMATS.md §4). Report only, never a decision.

    ``account`` adds an ``account`` key (current usage and balance) so a human can compare it with the lock.
    """
    code_bytes = Path(wasm).stat().st_size
    per_byte, source = fetch_per_byte(network, rpc_url)
    est = {
        "code_bytes": code_bytes,
        "state_bytes_after_init": state_bytes,
        "storage_amount_per_byte_yocto": str(per_byte),
        "source": source,
        "locked_near_for_code": locked_near(code_bytes, per_byte),
        "note": STORAGE_NOTE,
    }
    if account is not None:
        est["account"] = account_info(account, network, rpc_url)
    return est
