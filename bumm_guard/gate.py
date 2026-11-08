"""Deploy gate: scan a crate, refuse to hand out a deploy command while blocking findings exist.

``run_gate`` returns a ``GateOutcome`` (exit code + lines to print); the CLI does the printing.
Exit codes: 0 clean, 2 refused/bad input, 3 DEPLOY BLOCKED (high/critical finding, a simulate
report that does not match the wasm / has failed scenarios, or an upgrade without proof), 4 ``--execute`` but ``near`` not on PATH,
5 ``near`` ran and failed (deploy mode) or post-deploy verification failed (``--verify-deployed`` mode:
code hash mismatch, a failed smoke view, a smoke view whose result differs from its ``=EXPECTED_JSON``,
or RPC unreachable).

Nothing is executed by default: the exact ``near contract deploy`` command (near-cli-rs 0.30
syntax) is only printed. ``--execute`` runs it, testnet only. When the gate blocks, no deploy
is ever started and a stale ``gate-report.json`` from an earlier clean run is removed so it
cannot be mistaken for a pass.

With ``--account`` and ``--wasm`` the gate first reads the account's code (read-only ``view_code``).
No code: a fresh deploy, judged as before, unless the account uses a global contract (NEP-591; one
read-only ``view_account``), which blocks: its upgrades are not supported. Other code: an upgrade of a live contract with state,
allowed only if ``--simulate-report`` holds a passing scenario with an ``upgrade`` step
(docs/FORMATS.md §2a) from exactly that on-chain code to exactly this wasm, that records the deployed
code hash, whose upgrade transaction itself succeeded (a rolled-back upgrade proves nothing) and
that has at least one step after the upgrade; the steps after the upgrade are listed in the output and
in the report (``upgrade.post_upgrade_steps``), with a warning when none of them is a ``call``. The
printed command then repeats that scenario's migration call (``with-init-call``) or has none
(``without-init-call``); migrate args that name a scenario alias (``"alice"``, resolved only inside the
sandbox) block. When the proof has a migration call, ``migration_guard`` also checks in the crate's
Rust sources that the migration method is not callable by anyone (``#[private]`` or a recognised
``predecessor_account_id()`` guard in its body); an unprotected one, or one whose predecessor
comparison has no recognised rejection branch (``owner-check-unknown``), blocks, because after the
upgrade any account could call it and rewrite the state. The same code as the candidate on the
account: ``NO CHANGE``, exit 0, no command (``deploy.status: "no-change"`` in the report).

Strings taken from the simulate report (scenario names, hashes, step kinds and methods, statuses) are
supplied by whoever made the report. Before they enter the output lines, the errors or the report
they go through ``sanitize.clean`` (control characters removed, 200 characters at most), so a
scenario name cannot break a line or draw a fake verdict. A migration method or gas value that is not
plain text is not accepted as a proof, because it would be repeated in the printed command.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .rulepack.near import RULES
from .rulepack.near.rust_src import SourceFile, match_close
from .sanitize import clean, clean_str, is_plain
from .scanner import ScanResult, discover, scan
from .storage import RPC_URLS, RpcError, estimate_storage, simulate_state_bytes  # noqa: F401  (RPC_URLS re-exported)
from .verify import SmokeSpecError, global_contract, onchain_code_sha256, parse_smoke, post_deploy_ok, verify_deployed

BLOCKING = ("critical", "high")
REPORT_DIR = ".bumm-guard"
REPORT_NAME = "gate-report.json"
NETWORKS = ("testnet", "mainnet")
# NEAR account ids: dot-separated parts of [a-z0-9] joined by single - or _ ; 2..64 chars
_ACCOUNT_RE = re.compile(r"^(?:[a-z\d]+[-_])*[a-z\d]+(?:\.(?:[a-z\d]+[-_])*[a-z\d]+)*$")
METHOD_MAX = 256          # NEAR's limit on a method name (bytes); longer cannot be a real migration method
GAS_MAX = 32
WASM_PLACEHOLDER = "<path/to/contract.wasm>"
ACCOUNT_PLACEHOLDER = "<account-id>"


@dataclass
class GateOutcome:
    exit_code: int
    scan: ScanResult | None = None
    lines: list[str] = field(default_factory=list)       # stdout, printed after the findings
    errors: list[str] = field(default_factory=list)      # stderr
    blocked: bool = False
    report_path: str | None = None
    command: list[str] | None = None
    upgrade: dict | None = None                          # the gate report's ``upgrade`` block
    upgrade_check: dict | None = None                    # why an upgrade was refused (on-chain, candidate, proven pairs)
    deploy: dict | None = None                           # the gate report's ``deploy`` block (status, command)
    report: dict | None = None                           # the gate report data (also with --dry-run), when the gate passed
    rpc_error: str | None = None                         # set when the gate blocked only because the target could not be read


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def valid_account(account: str) -> bool:
    return 2 <= len(account) <= 64 and bool(_ACCOUNT_RE.match(account))


def deploy_command(account: str, wasm: str, network: str, migrate: dict | None = None) -> list[str]:
    """near-cli-rs 0.30 argv; a leading ``-`` in the path is neutralised so it is not read as a flag.

    ``migrate`` (``{method, args, gas}``) makes it one batch transaction: deploy + that call, the
    same shape as the simulated upgrade step (if the call fails, the deploy is rolled back too).
    """
    path = "./" + wasm if wasm.startswith("-") else wasm
    init = ["without-init-call"]
    if migrate:
        init = ["with-init-call", migrate["method"], "json-args",
                json.dumps(migrate.get("args") or {}, separators=(",", ":"), sort_keys=True),
                "prepaid-gas", migrate.get("gas") or "100 Tgas", "attached-deposit", "0 NEAR"]
    return ["near", "contract", "deploy", account, "use-file", path, *init,
            "network-config", network, "sign-with-keychain", "send"]


def build_report(crate: Path, res: ScanResult, wasm: str | None, network: str, account: str | None,
                 abi: str | None = None) -> dict:
    hashes = {}
    for disp, _root in discover([str(crate)]):
        hashes[Path(os.path.relpath(disp, crate)).as_posix()] = sha256_file(disp)
    by_sev: dict[str, int] = {}
    for f in res.findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
    wasm_info = None
    if wasm:
        exists = Path(wasm).is_file()
        wasm_info = {"path": wasm, "sha256": sha256_file(wasm) if exists else None, "exists": exists}
    return {
        "tool": "bumm-guard",
        "version": __version__,
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "crate_dir": str(crate),
        "rule_ids_checked": [r.id for r in RULES],
        "findings_count": len(res.findings),
        "findings_by_severity": dict(sorted(by_sev.items())),
        "blocking_findings": 0,
        "network": network,
        "account": account,
        "rs_sha256": dict(sorted(hashes.items())),
        "wasm": wasm_info,
        "artifact": {"wasm_sha256": wasm_info["sha256"] if wasm_info else None,
                     "abi_sha256": sha256_file(abi) if abi else None},
        "simulate": None,
        "upgrade": None,
        "migration_guard": None,
        "storage_estimate": None,
        "deploy": None,
        "post_deploy": None,
    }


def load_simulate_report(path: str, wasm_sha256: str) -> tuple[dict | None, list[str], int]:
    """Check a docs/FORMATS.md §3 report against the wasm. Returns ``(simulate block, error lines, exit code)``.

    Exit 2 = unreadable report, 3 = blocks the deploy (other wasm, failed scenarios, no scenarios).
    """
    try:
        rep = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, [f"bumm-guard: cannot read simulate report {path}: {e}"], 2
    if not isinstance(rep, dict) or not isinstance(rep.get("scenarios"), list) or not isinstance(rep.get("summary"), dict):
        return None, [f"bumm-guard: {path} is not a simulate report (docs/FORMATS.md §3)"], 2
    if rep.get("wasm_sha256") != wasm_sha256:
        return None, ["DEPLOY BLOCKED: simulate report is for a different wasm "
                      f"(report {clean(repr(rep.get('wasm_sha256')))}, artifact {wasm_sha256!r}). Re-run `bumm-guard simulate` "
                      "against this wasm."], 3
    failing = [clean(sc.get("name", "?")) for sc in rep["scenarios"] if isinstance(sc, dict) and not sc.get("passed")]
    failed = rep["summary"].get("failed")
    if failing or not isinstance(failed, int) or failed != 0:
        names = ", ".join(failing) or "(see report)"
        return None, [f"DEPLOY BLOCKED: simulate report has failing scenario(s): {names}"], 3
    if not rep["scenarios"]:
        return None, ["DEPLOY BLOCKED: simulate report contains no scenarios"], 3
    why = _summary_inconsistency(rep) or next(
        (f"scenario {clean(sc.get('name', '?'))!r} says it passed, but {w}"
         for sc in rep["scenarios"] if isinstance(sc, dict) and (w := _scenario_inconsistency(sc))), None)
    if why:
        return None, [f"DEPLOY BLOCKED: simulate report contradicts itself (edited?): {why}"], 3
    return {"report": path, "passed": True, "scenarios": len(rep["scenarios"])}, [], 0


def _upgrade_steps(sc: dict) -> list[dict]:
    return [st for st in sc.get("steps") or [] if isinstance(st, dict) and st.get("kind") == "upgrade"]


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _upgrade_succeeded(step: dict) -> tuple[bool, str]:
    """Did the upgrade transaction of this §2a step go through? True only if the step's expectation
    was ``"ok"`` and the chain answered ``status: success``. The second value describes a mismatch."""
    status = (step.get("actual") or {}).get("status") if isinstance(step.get("actual"), dict) else None
    expected = step.get("expected")
    if expected != "ok":
        return False, f"status {clean(status)}, expected {clean(json.dumps(expected))}"
    if status != "success":
        return False, f"status {clean(status)}"
    return True, ""


def _post_upgrade_steps(sc: dict, up_idx) -> list[dict]:
    """Every step after the upgrade step as ``{index, kind, method}`` (``method`` is the account
    for a ``balance`` step); ``kind`` and ``method`` are report text, so they are cleaned for display."""
    if not _is_int(up_idx):
        return []
    out = []
    for st in sc.get("steps") or []:
        if not isinstance(st, dict) or not _is_int(st.get("index")) or st["index"] <= up_idx:
            continue
        method = st.get("method") if st.get("kind") != "balance" else st.get("account")
        out.append({"index": st["index"], "kind": None if st.get("kind") is None else clean(st["kind"]),
                    "method": None if method is None else clean(method)})
    return out


def _scenario_inconsistency(sc: dict) -> str | None:
    """Why a scenario that says ``passed: true`` contradicts its own trace, or ``None``. A report is
    unsigned JSON, so the gate does not take the scenario flag on trust: the init call and every step
    must say ``passed`` too, and each passed step must agree with what the chain answered (an ``ok`` or
    value expectation needs ``status: success``; a ``fail_contains`` expectation needs a failure whose
    error text contains it). This catches an edited verdict; a report fabricated end to end still
    passes, so take reports from a run you control (README, "What the gate trusts")."""
    if sc.get("error") is not None:
        return "the scenario recorded an error"
    init = sc.get("init")
    if isinstance(init, dict) and (init.get("passed") is not True
                                   or (init.get("actual") or {}).get("status") != "success"):
        return "its init call did not pass"
    steps = sc.get("steps")
    if not isinstance(steps, list):
        return "it has no list of steps"
    for st in steps:
        if not isinstance(st, dict) or st.get("passed") is not True:
            idx = st.get("index") if isinstance(st, dict) else "?"
            return f"step {clean(idx)} did not pass"
        actual = st.get("actual") if isinstance(st.get("actual"), dict) else {}
        status, expected = actual.get("status"), st.get("expected")
        wants_failure = isinstance(expected, dict) and "fail_contains" in expected
        if wants_failure:
            text = f"{actual.get('error', '')}{actual.get('error_detail', '')}"
            if status != "failure" or not isinstance(expected["fail_contains"], str) \
                    or expected["fail_contains"] not in text:
                return f"step {clean(st.get('index'))} is marked passed but did not fail as expected"
        elif status != "success":
            return f"step {clean(st.get('index'))} is marked passed but its status is {clean(status)}"
    return None


def _summary_inconsistency(rep: dict) -> str | None:
    """Why the report's ``summary`` does not add up to its scenarios and steps, or ``None``."""
    sm = rep.get("summary") if isinstance(rep.get("summary"), dict) else {}
    scen = [sc for sc in rep.get("scenarios") or [] if isinstance(sc, dict)]
    steps = [st for sc in scen for st in (sc.get("steps") or []) if isinstance(st, dict)]
    want = {"failed": sum(1 for sc in scen if sc.get("passed") is not True),
            "steps_failed": sum(1 for st in steps if st.get("passed") is not True)}
    for key, value in want.items():
        if sm.get(key) != value:
            return (f"summary.{key} is {clean(json.dumps(sm.get(key)))} but the scenarios show {value}")
    return None


def _state_written_before(sc: dict, up_idx) -> bool:
    """Did a ``call`` step succeed before the upgrade step? Without one, the old code never wrote
    state beyond its init, and an upgrade proven on that state proves little about live data."""
    return any(isinstance(st, dict) and st.get("kind") == "call" and _is_int(st.get("index"))
               and _is_int(up_idx) and st["index"] < up_idx and st.get("passed") is True
               and (st.get("actual") or {}).get("status") == "success"
               for st in sc.get("steps") or [])


def find_upgrade_proof(rep: dict, onchain: str, candidate: str) -> tuple[dict | None, list[str]]:
    """The first passing scenario of a §3 report whose upgrade step goes from ``onchain`` (the
    scenario's ``wasm_sha256``) to ``candidate`` (``upgrade_wasm_sha256`` and the step's
    ``code_sha256``, which must be recorded), whose upgrade transaction succeeded (expectation
    ``"ok"`` and ``actual.status == "success"``: a rolled-back upgrade proves nothing) and that has
    at least one step after the upgrade (otherwise nothing was proven on the migrated state), at least
    one successful ``call`` before it (state written by the old code), and whose trace and the report's
    summary agree with its ``passed`` flag (``_scenario_inconsistency``, ``_summary_inconsistency``).
    Returns ``(upgrade block, notes)``; the block lists the steps after the upgrade in
    ``post_upgrade_steps`` (docs/FORMATS.md §4); notes explain near misses."""
    notes, proofs = [], []
    bad_summary = _summary_inconsistency(rep)
    for sc in rep.get("scenarios") or []:
        if not isinstance(sc, dict) or not _upgrade_steps(sc):
            continue
        name = clean(sc.get("name", "?"))
        frm, to = sc.get("wasm_sha256"), sc.get("upgrade_wasm_sha256")
        if (frm, to) != (onchain, candidate):
            notes.append(f"upgrade scenario {name!r} proves {clean(frm)} -> {clean(to)}, not {onchain} -> {candidate}")
            continue
        step = _upgrade_steps(sc)[0]
        ok, why = _upgrade_succeeded(step)
        if not ok:
            notes.append(f"upgrade scenario {name!r}: the upgrade transaction did not succeed ({why}); "
                         "a rolled-back upgrade proves nothing")
            continue
        later = _post_upgrade_steps(sc, step.get("index"))
        if not later:
            notes.append(f"upgrade scenario {name!r} has no steps after the upgrade: "
                         "nothing was proven on the migrated state")
            continue
        deployed = (step.get("actual") or {}).get("code_sha256")
        if deployed is None:
            notes.append(f"upgrade scenario {name!r}: report does not record the deployed code hash "
                         "(bumm-sim older than this gate): re-run `bumm-guard simulate`")
            continue
        if deployed != candidate:
            notes.append(f"upgrade scenario {name!r}: deployed code_sha256 differs from the candidate")
            continue
        if not sc.get("passed"):
            notes.append(f"upgrade scenario {name!r} for this pair did not pass")
            continue
        why = _scenario_inconsistency(sc)
        if why:
            notes.append(f"upgrade scenario {name!r} says it passed, but {why}: the report contradicts itself "
                         "(edited?) and is not accepted as a proof")
            continue
        if bad_summary:
            notes.append(f"upgrade scenario {name!r}: the report's {bad_summary} (edited?); not accepted as a proof")
            continue
        if not _state_written_before(sc, step.get("index")):
            notes.append(f"upgrade scenario {name!r} makes no successful call before the upgrade: the old code "
                         "wrote no state beyond its init, so the upgrade was not tested on stored data; add a "
                         "state-changing call before the upgrade step")
            continue
        if step.get("method") and not isinstance(step.get("migrate"), dict):
            notes.append(f"upgrade scenario {name!r} has a migration call but the report does not record its "
                         "args (bumm-sim older than this gate): re-run `bumm-guard simulate`")
            continue
        m = step.get("migrate")
        if m and not (is_plain(m.get("method"), METHOD_MAX) and m["method"]
                      and (m.get("gas") is None or is_plain(m["gas"], GAS_MAX))):
            notes.append(f"upgrade scenario {name!r}: the recorded migration method or gas is not plain text "
                         "(control characters, or too long); it would be repeated in the deploy command, so "
                         "this scenario is not accepted as a proof")
            continue
        proofs.append({"onchain_code_sha256": onchain, "candidate_sha256": candidate, "scenario": name,
                       "migrate": {"method": m["method"], "args": m.get("args") or {}, "gas": m.get("gas")}
                       if m else None,
                       "passed": True, "post_upgrade_steps": later})
    if len({json.dumps(p["migrate"], sort_keys=True) for p in proofs}) > 1:
        notes.append(f"several passing upgrade scenarios with different migrations; using {proofs[0]['scenario']!r}")
    return (proofs[0] if proofs else None), notes


def scenario_aliases(sc: dict) -> set[str]:
    """The account aliases the runner resolves at run time (``"alice"`` -> sandbox account): the keys of
    the scenario's ``accounts`` as recorded in the §3 report, plus ``"contract"``. A report that does not
    record them falls back to every alias used as a signer or balance target in the steps."""
    names = {"contract"}
    accounts = sc.get("accounts")
    if isinstance(accounts, dict) and accounts:
        names.update(k for k in accounts if isinstance(k, str))
        return names
    for st in sc.get("steps") or []:
        if isinstance(st, dict):
            names.update(v for k in ("by", "as", "signer", "account") if isinstance(v := st.get(k), str))
    return names


def _strings_in(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in _strings_in(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings_in(v)]
    return []


def migrate_alias_args(rep: dict, scenario: str, migrate: dict | None) -> list[str]:
    """Alias names found among the string values (recursively) of the proof's ``migrate.args``
    (``scenario`` is the proof's cleaned scenario name).
    The runner resolved them to sandbox accounts; printed raw into the testnet command they would be
    wrong, so the gate blocks on any."""
    if not migrate:
        return []
    sc = next((s for s in rep.get("scenarios") or [] if isinstance(s, dict) and clean(s.get("name", "?")) == scenario), {})
    names = scenario_aliases(sc)
    return sorted({s for s in _strings_in(migrate.get("args")) if s in names})


def upgrade_check(rep, account: str, onchain: str, candidate: str, simulate_report: str | None) -> dict:
    """Structured reason for a refused upgrade, for agents (``gate_deploy`` returns it as ``upgrade_check``):
    what runs on the account, what the candidate is, and every pair the report proves or tried to prove.
    ``status``: ``no-report``, ``no-proof``, ``report-proves-other-pair`` or ``proof-upgrade-failed`` (the
    report has the pair, but in no scenario did the upgrade transaction itself succeed: ``expected`` was
    not ``"ok"`` or ``actual.status`` was not ``success``). Each pair carries ``upgrade_status`` (the
    step's ``actual.status``) and ``upgrade_succeeded``."""
    pairs = []
    for sc in (rep or {}).get("scenarios") or [] if isinstance(rep, dict) else []:
        if isinstance(sc, dict) and _upgrade_steps(sc):
            step = _upgrade_steps(sc)[0]
            actual = step.get("actual") if isinstance(step.get("actual"), dict) else {}
            pairs.append({"from": clean_str(sc.get("wasm_sha256")), "to": clean_str(sc.get("upgrade_wasm_sha256")),
                          "scenario": clean(sc.get("name", "?")), "passed": bool(sc.get("passed")),
                          "upgrade_status": clean_str(actual.get("status")),
                          "upgrade_succeeded": _upgrade_succeeded(step)[0]})
    matching = [p for p in pairs if p["from"] == onchain and p["to"] == candidate]
    if not simulate_report:
        status = "no-report"
    elif matching and not any(p["upgrade_succeeded"] for p in matching):
        status = "proof-upgrade-failed"
    elif matching:
        status = "no-proof"
    else:
        status = "report-proves-other-pair" if pairs else "no-proof"
    return {"account": account, "onchain_code_sha256": onchain, "candidate_sha256": candidate,
            "status": status, "proven_pairs": pairs}


# --- migration guard -----------------------------------------------------------------------------

_PRED_RE = re.compile(r"\b(?:\w+::)*predecessor_account_id\s*\(\s*\)")
_MACRO_RE = re.compile(r"\b(require|assert|assert_eq|assert_ne|require_eq|require_ne)\s*!\s*\(")
_BRANCH_RE = re.compile(r"\b(if|match)\b")
_REJECT_RE = re.compile(r"\b(?:panic|require|assert|assert_eq|assert_ne|require_eq|require_ne|unreachable)\s*!"
                        r"|\bpanic_str\s*\(|\breturn\b")
_OWNER_CHECK_LINES = 15


def _split_args(text: str) -> list[str]:
    """Split macro arguments on top-level commas (masked text, so no commas in strings)."""
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        if ch == "," and depth <= 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [x.strip() for x in parts]


def _tautology(*sides: str) -> bool:
    """``a == a`` / ``assert_eq!(a, a)``: the same expression on both sides proves nothing. One
    argument is a comparison expression (``a == b`` / ``a != b``), two are the sides themselves."""
    if len(sides) == 1:
        halves = re.split(r"\s*(?:==|!=)\s*", sides[0], maxsplit=1)
        if len(halves) != 2:
            return False
        sides = tuple(halves)
    a, b = (re.sub(r"\s+", "", x) for x in sides[:2])
    return bool(a) and a == b


def _close(body: str, i: int, o: str, c: str) -> int:
    j = match_close(body, i, o, c)
    return len(body) - 1 if j < 0 else j


def _owner_check(body: str) -> str:
    """Is a ``predecessor_account_id()`` comparison in the first ``_OWNER_CHECK_LINES`` lines of the
    (masked) body a guard that refuses other callers?

    ``recognized``: the comparison sits inside ``require!``/``assert!``/``assert_eq!``/``assert_ne!``/
    ``require_eq!``/``require_ne!``, or in the condition of an ``if``/``match`` whose body (any arm or
    ``else`` branch, brace level) contains ``panic!``/``env::panic_str``/``require!``/``assert!``/
    ``return``/``unreachable!``. ``unknown``: an ``if``/``match`` without such a rejection, or a
    tautology (the same expression on both sides, e.g. ``pred == pred``). ``none``: no comparison
    in a guard position. What the predecessor is compared with (``self.owner``, a constant,
    ``current_account_id()``) is not judged. ``signer_account_id()`` does not count (BG-NEAR-003)."""
    lines = body.splitlines(keepends=True)
    limit = sum(len(l) for l in lines[:_OWNER_CHECK_LINES])
    verdicts: set[str] = set()
    for pm in _PRED_RE.finditer(body):
        p = pm.start()
        if p >= limit:
            break
        verdict = None
        for mm in _MACRO_RE.finditer(body):
            o = mm.end() - 1
            if o >= p:
                break
            c = _close(body, o, "(", ")")
            if p < c:
                args = _split_args(body[o + 1:c])
                taut = (_tautology(args[0], args[1]) if len(args) >= 2 else False) \
                    if mm.group(1) in ("assert_eq", "assert_ne", "require_eq", "require_ne") else _tautology(args[0])
                verdict = "unknown" if taut else "recognized"
                break
        if verdict is None:
            for bm in _BRANCH_RE.finditer(body):
                k = bm.end()
                if k > p:
                    break
                brace = body.find("{", k)
                if brace < 0 or not (k <= p < brace):
                    continue
                cond = body[k:brace]
                end = _close(body, brace, "{", "}")
                if bm.group(1) == "if":
                    while True:  # else / else-if chain: a rejection in any branch counts
                        nxt = re.match(r"\s*else\b", body[end + 1:])
                        if not nxt:
                            break
                        brace2 = body.find("{", end + 1 + nxt.end())
                        if brace2 < 0:
                            break
                        end = _close(body, brace2, "{", "}")
                rejects = bool(_REJECT_RE.search(body[brace:end + 1]))
                taut = bm.group(1) == "if" and _tautology(cond)
                verdict = "recognized" if rejects and not taut else "unknown"
                break
        if verdict:
            verdicts.add(verdict)
    if "recognized" in verdicts:
        return "recognized"
    return "unknown" if "unknown" in verdicts else "none"


def migration_guard(crate_dir: str | Path, method: str) -> tuple[str, dict | None]:
    """Is the migration method ``method`` of the crate in ``crate_dir`` protected from arbitrary callers?

    Returns ``(status, detail)`` with status one of ``private`` (``#[private]`` among the attributes
    directly above the ``fn``, in any order with ``#[init(ignore_state)]``), ``owner-check`` (a
    recognised ``predecessor_account_id()`` guard in the first 15 lines of the body, see
    ``_owner_check``), ``owner-check-unknown`` (a ``predecessor_account_id()`` comparison was seen in an
    ``if``/``match`` but no rejection branch was recognised, or it is a tautology; the gate treats it
    like ``unprotected``), ``unprotected`` (found, none of these), ``not-found`` (no ``fn <method>`` in
    any ``impl`` of the crate's ``*.rs`` files outside ``target/``) or ``ambiguous`` (several definitions
    whose statuses differ). ``detail`` is ``{file, line}`` of the definition (for ``ambiguous``:
    ``{definitions: [{file, line, status}, ...]}``), ``None`` when not found.

    Regex and brace level, not a parser: comments, string literals and ``#[cfg(test)]`` items are
    blanked before matching (same helper as the rules); free functions outside an ``impl``, methods
    produced by macros and a check delegated to a helper (``self.assert_owner()``) or to a DAO policy
    are not recognised, so such a method is reported ``unprotected`` unless it carries ``#[private]``.
    """
    crate = Path(crate_dir)
    defs: list[dict] = []
    for disp, _root in discover([str(crate)]):
        try:
            sf = SourceFile.from_path(disp, disp)
        except OSError:
            continue
        for m in sf.methods:
            if m.name != method:
                continue
            if m.has_attr("private"):
                st = "private"
            else:
                check = _owner_check(m.body) if m.has_body else "none"
                st = {"recognized": "owner-check", "unknown": "owner-check-unknown"}.get(check, "unprotected")
            defs.append({"file": Path(os.path.relpath(disp, crate)).as_posix(), "line": m.line, "status": st})
    if not defs:
        return "not-found", None
    if len({d["status"] for d in defs}) > 1:
        return "ambiguous", {"definitions": defs}
    d = defs[0]
    return d["status"], {"file": d["file"], "line": d["line"]}


def _blocked(out: GateOutcome, report: Path, dry_run: bool, lines: list[str]) -> GateOutcome:
    """Exit 3; keeps the lines printed so far and removes a stale report."""
    out.exit_code, out.blocked = 3, True
    out.lines.extend(lines)
    if not dry_run and report.is_file():
        report.unlink()
        out.lines.append(f"removed stale {report}")
    return out


def _write_report(out: GateOutcome, data: dict, report: Path, dry_run: bool) -> None:
    out.report = data
    if dry_run:
        out.lines.append("dry-run: gate-report.json not written")
        return
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    out.report_path = str(report)
    out.lines.append(f"gate passed: report written to {report}")


def _refused(res: ScanResult, report: Path, dry_run: bool, errors: list[str]) -> GateOutcome:
    """Exit 2 (unusable input); removes a stale report."""
    out = GateOutcome(2, scan=res, errors=errors)
    if not dry_run and report.is_file():
        report.unlink()
        out.lines.append(f"removed stale {report}")
    return out


def run_gate(crate_dir: str, wasm: str | None = None, network: str = "testnet", account: str | None = None,
             dry_run: bool = False, execute: bool = False, abi: str | None = None,
             simulate_report: str | None = None, estimate_storage_flag: bool = False,
             rpc_url: str | None = None) -> GateOutcome:
    """Scan, decide, optionally write the report and (testnet only) run the deploy.

    ``simulate_report`` binds a §3 report to ``wasm``; ``estimate_storage_flag`` adds the read-only
    storage estimate (one RPC call, plus one with ``account``). ``account`` together with an existing
    ``wasm`` adds one read-only ``view_code`` call (plus a ``view_account`` call if it has no code, to
    rule out a global contract): code on the account makes this an upgrade, which
    needs a passing upgrade scenario for exactly (on-chain code -> ``wasm``) in ``simulate_report``.
    """
    crate = Path(crate_dir)
    if not crate.is_dir():
        return GateOutcome(2, errors=[f"bumm-guard: not a directory: {crate_dir}"])
    if account is not None and not valid_account(account):
        return GateOutcome(2, errors=[f"bumm-guard: invalid NEAR account id: {account!r}"])
    if execute:
        if network != "testnet":
            return GateOutcome(2, errors=[
                "bumm-guard: --execute is only allowed with --network testnet; refusing to deploy to "
                f"{network}. Print-only mode (no --execute) shows the command to run yourself."])
        if not account or not wasm:
            return GateOutcome(2, errors=["bumm-guard: --execute needs both --account and --wasm"])
        if not Path(wasm).is_file():
            return GateOutcome(2, errors=[f"bumm-guard: wasm file not found: {wasm}"])

    if abi is not None and not Path(abi).is_file():
        return GateOutcome(2, errors=[f"bumm-guard: abi file not found: {abi}"])
    if (simulate_report or estimate_storage_flag) and not (wasm and Path(wasm).is_file()):
        return GateOutcome(2, errors=["bumm-guard: --simulate-report and --estimate-storage need an existing --wasm "
                                      "(the report is bound to the wasm hash / its size is measured)"])

    res = scan([str(crate)])
    blocking = [f for f in res.findings if f.severity in BLOCKING]
    report = crate / REPORT_DIR / REPORT_NAME
    if blocking:
        out = GateOutcome(3, scan=res, blocked=True)
        if not dry_run and report.is_file():
            report.unlink()
            out.lines.append(f"removed stale {report}")
        out.lines.append(f"DEPLOY BLOCKED: {len(blocking)} high/critical finding(s) in {crate_dir}. "
                         "Fix them (see `bumm-guard fix`) and run the gate again.")
        return out

    out = GateOutcome(0, scan=res)
    data = build_report(crate, res, wasm, network, account, abi)
    sim_block, sim_report = None, None
    onchain, upgrade, rep, no_change = None, None, None, False
    if simulate_report:
        try:
            rep = json.loads(Path(simulate_report).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return _refused(res, report, dry_run, [f"bumm-guard: cannot read simulate report {simulate_report}: {e}"])
    upgrade_names = [clean(sc.get("name", "?")) for sc in (rep or {}).get("scenarios") or []
                     if isinstance(sc, dict) and _upgrade_steps(sc)] if isinstance(rep, dict) else []
    if account and wasm and Path(wasm).is_file():
        candidate = data["artifact"]["wasm_sha256"]
        try:
            onchain = onchain_code_sha256(account, network, rpc_url)
        except RpcError as e:
            out.rpc_error = f"cannot read the code on {account} ({network}): {e}"
            return _blocked(out, report, dry_run, [
                f"DEPLOY BLOCKED: cannot read the code on {account} ({network}): {e}. Without it the gate cannot "
                "tell a fresh deploy from an upgrade; retry, or pass --rpc-url."])
        if onchain is None:
            # No code is not the same as empty: an account can use a global contract (NEP-591).
            try:
                glob = global_contract(account, network, rpc_url)
            except RpcError as e:
                out.rpc_error = f"cannot read account {account} ({network}): {e}"
                return _blocked(out, report, dry_run, [
                    f"DEPLOY BLOCKED: cannot read account {account} ({network}): {e}. Without it the gate cannot "
                    "tell an empty account from one that uses a global contract (NEP-591); retry, or pass --rpc-url."])
            if glob is not None:
                return _blocked(out, report, dry_run, [
                    f"DEPLOY BLOCKED: {account} uses a global contract (NEP-591): upgrades of global-contract "
                    f"accounts are not supported by this gate ({glob})"])
            out.lines.append(f"account {account} ({network}) has no contract code: fresh deploy")
            if upgrade_names:
                out.lines.append(f"warning: simulate report has upgrade scenario(s) ({', '.join(upgrade_names)}) but "
                                 f"{account} has no code: this is a fresh deploy, the upgrade evidence is not used")
        elif onchain == candidate:
            no_change = True
            out.lines.append(f"NO CHANGE: account {account} ({network}) already runs this exact wasm ({onchain}); "
                             "nothing to deploy")
        else:
            upgrade, notes = find_upgrade_proof(rep, onchain, candidate) if isinstance(rep, dict) else (None, [])
            if upgrade is None:
                out.upgrade_check = upgrade_check(rep, account, onchain, candidate, simulate_report)
                how = ("pass --simulate-report with a passing upgrade scenario (docs/FORMATS.md §2a): contract.wasm = "
                       "the code now on the account, then `bumm-guard simulate <scenario> --upgrade-wasm "
                       f"{wasm}`" if not simulate_report else
                       "the simulate report has no passing upgrade scenario for this pair")
                return _blocked(out, report, dry_run, [
                    f"DEPLOY BLOCKED: account {account} already runs code {onchain}; this is an upgrade and no "
                    f"passing upgrade scenario proves {onchain} -> {candidate}", f"  {how}"]
                    + [f"  {n}" for n in notes])
            out.upgrade = data["upgrade"] = upgrade
            m = upgrade["migrate"]
            out.lines.append(f"upgrade of {account}: {onchain} -> {candidate}, proved by passing scenario "
                             f"{upgrade['scenario']!r}; "
                             + (f"migration {m['method']}({clean(json.dumps(m['args']))}) in the same transaction"
                                if m else "code only (no migration call)"))
            out.lines.extend(f"note: {n}" for n in notes)
            steps = upgrade["post_upgrade_steps"]
            out.lines.append(f"proved after upgrade ({len(steps)} step(s)): "
                             + ", ".join(f"{st['kind']} {st['method']}" for st in steps))
            if not any(st["kind"] == "call" for st in steps):
                out.lines.append("warning: only view calls were made on the migrated state; no state change "
                                 "was exercised")
            aliases = migrate_alias_args(rep, upgrade["scenario"], m)
            if aliases:
                return _blocked(out, report, dry_run, [
                    f"DEPLOY BLOCKED: migrate args of scenario {upgrade['scenario']!r} reference sandbox-only "
                    f"alias(es) {', '.join(clean(repr(a)) for a in aliases)}: the runner resolved them to sandbox accounts, "
                    f"but the printed {network} command would send them as written; use literal account ids in "
                    "the scenario's migrate args and re-run `bumm-guard simulate`"])
            if m:
                status, detail = migration_guard(crate, m["method"])
                data["migration_guard"] = {"method": m["method"], "status": status,
                                           "file": (detail or {}).get("file"), "line": (detail or {}).get("line")}
                where = f"{detail['file']}:{detail['line']}" if detail and "file" in detail else None
                if status == "private":
                    out.lines.append(f"migration {m['method']}: #[private] ({where})")
                elif status == "owner-check":
                    out.lines.append(f"migration {m['method']}: owner check at {where}")
                elif status == "owner-check-unknown":
                    return _blocked(out, report, dry_run, [
                        f"DEPLOY BLOCKED: migration method {m['method']} in {where} compares predecessor_account_id() "
                        "but no rejection branch was recognised (no panic!/env::panic_str/require!/assert!/return "
                        "in the if/match body, or the comparison is a tautology); the gate cannot tell that other "
                        "callers are refused",
                        "  fix: add `#[private]` above `#[init(ignore_state)]` (recommended), or "
                        "`require!(env::predecessor_account_id() == self.owner, \"only owner\")` as the first "
                        "line of the body"])
                elif status == "not-found":
                    out.lines.append(f"warning: migration method {m['method']} not found in {crate_dir} sources; "
                                     "its access control was not checked")
                else:
                    fix = ("  fix: add `#[private]` above `#[init(ignore_state)]`, or "
                           "`require!(env::predecessor_account_id() == self.owner, \"only owner\")` as the first "
                           "line of the body")
                    if status == "ambiguous":
                        defs = ", ".join(f"{d['file']}:{d['line']} ({d['status']})" for d in detail["definitions"])
                        return _blocked(out, report, dry_run, [
                            f"DEPLOY BLOCKED: migration method {m['method']} is defined several times with "
                            f"different access control: {defs}; make every definition #[private]", fix])
                    return _blocked(out, report, dry_run, [
                        f"DEPLOY BLOCKED: migration method {m['method']} in {where} is callable by anyone: it has "
                        "no #[private] and no predecessor check; after the upgrade any account could call it and "
                        "rewrite the state", fix])
    elif account:
        out.lines.append(f"warning: no --wasm: the code on {account} was not checked; an upgrade of a live "
                         "contract needs --wasm and a --simulate-report with a passing upgrade scenario")
    if upgrade:
        scen = rep.get("scenarios") or []
        failing = [clean(sc.get("name", "?")) for sc in scen if isinstance(sc, dict) and not sc.get("passed")]
        data["simulate"] = {"report": simulate_report, "passed": not failing, "scenarios": len(scen)}
        if failing:
            out.lines.append(f"note: other scenario(s) in the report did not pass: {', '.join(failing)}")
        sim_report = rep
    elif no_change and simulate_report:
        out.lines.append("note: simulate report not checked against this wasm: nothing to deploy")
        sim_report = rep if isinstance(rep, dict) else None
    elif simulate_report:
        sim_block, errs, code = load_simulate_report(simulate_report, data["artifact"]["wasm_sha256"])
        if code == 2:
            return _refused(res, report, dry_run, errs)
        if code:
            return _blocked(out, report, dry_run, errs)
        sim_report = rep
        data["simulate"] = sim_block
        out.lines.append(f"simulate report ok: {sim_block['scenarios']} scenario(s) passed for this wasm")
    else:
        out.lines.append("warning: no scenario report: behaviour not verified")
    if estimate_storage_flag:
        est = estimate_storage(wasm, network, rpc_url, account, simulate_state_bytes(sim_report))
        data["storage_estimate"] = est
        out.lines.append(f"storage: wasm {est['code_bytes']} bytes locks about {est['locked_near_for_code']} NEAR "
                         f"({est['source']}); a locked balance, not a fee")
        if est.get("state_bytes_after_init") is None:
            out.lines.append("storage: state size after init unknown (simulate report has no storage_usage)")
        acct = est.get("account")
        if acct:
            out.lines.append(f"storage: account {acct['id']}: " + (
                acct["error"] if "error" in acct else
                f"storage_usage {acct['storage_usage']} bytes, balance {acct['amount_near']} NEAR"))
    if wasm and not data["wasm"]["exists"]:
        out.errors.append(f"bumm-guard: warning: wasm not found: {wasm} (sha256 not recorded)")
    if no_change:
        data["deploy"] = out.deploy = {"status": "no-change", "command": None}
        _write_report(out, data, report, dry_run)
        return out
    cmd = deploy_command(account or ACCOUNT_PLACEHOLDER, wasm or WASM_PLACEHOLDER, network,
                         upgrade["migrate"] if upgrade else None)
    out.command = cmd
    data["deploy"] = out.deploy = {"status": "printed", "command": shlex.join(cmd)}
    _write_report(out, data, report, dry_run)
    if not execute:
        out.lines.append("deploy command (NOT executed; pass --execute with --network testnet to run it):")
        out.lines.append("  " + shlex.join(cmd))
        return out
    near = shutil.which("near")
    if near is None:
        out.exit_code = 4
        out.errors.append("bumm-guard: `near` (near-cli-rs) not found on PATH; install it or run the command above yourself.")
        out.lines.append("deploy command:")
        out.lines.append("  " + shlex.join(cmd))
        return out
    out.lines.append("executing: " + shlex.join(cmd))
    rc = subprocess.run([near, *cmd[1:]], check=False).returncode
    if rc != 0:
        out.exit_code = 5
        out.errors.append(f"bumm-guard: `near` exited with status {rc}")
    return out


def run_verify_deployed(account: str, wasm: str, network: str = "testnet", smoke: list[str] | None = None,
                        crate_dir: str | None = None, rpc_url: str | None = None,
                        dry_run: bool = False) -> GateOutcome:
    """``gate --verify-deployed``: compare on-chain code with ``wasm``, run view-only smoke calls.

    Exit 0 if the hash matches and every smoke view succeeded, 5 otherwise, 2 for bad input. The
    ``post_deploy`` block is merged into ``<crate_dir or .>/.bumm-guard/gate-report.json`` (created if absent).
    Never calls init or a change method and never holds a key.
    """
    if not valid_account(account):
        return GateOutcome(2, errors=[f"bumm-guard: invalid NEAR account id: {account!r}"])
    if not Path(wasm).is_file():
        return GateOutcome(2, errors=[f"bumm-guard: wasm file not found: {wasm}"])
    base = Path(crate_dir) if crate_dir else Path(".")
    if not base.is_dir():
        return GateOutcome(2, errors=[f"bumm-guard: not a directory: {base}"])
    try:
        for spec in smoke or []:
            parse_smoke(spec)
    except SmokeSpecError as e:
        return GateOutcome(2, errors=[f"bumm-guard: {e}"])
    block = verify_deployed(account, wasm, network, smoke, rpc_url)
    ok = post_deploy_ok(block)
    out = GateOutcome(0 if ok else 5)
    local = sha256_file(wasm)
    out.lines.append(f"post-deploy {account} ({network}): on-chain code sha256 {block['code_hash_on_chain']}")
    if "error" in block:  # nothing was compared: say so, do not call it a mismatch
        out.lines.append(f"post-deploy: local wasm sha256 {local}: NOT COMPARED (the deployed code could not be read)")
        out.errors.append(f"bumm-guard: could not fetch deployed code: {block['error']}")
    else:
        out.lines.append(f"post-deploy: local wasm sha256 {local}: "
                         + ("MATCH" if block["matches_artifact"] else "MISMATCH (deployed code is not this artifact)"))
    for sm in block["smoke"]:
        detail = json.dumps(sm["result"]) if sm["ok"] else sm.get("error", "failed")
        if sm["ok"] and "expected" in sm:
            detail += " (as expected)"
        out.lines.append(f"smoke {sm['view']}: {'ok' if sm['ok'] else 'FAILED'}: {detail}")
    out.lines.append("post-deploy verification " + ("passed" if ok else
                                                    "FAILED (not verified: the RPC did not answer)" if "error" in block
                                                    else "FAILED"))
    report = base / REPORT_DIR / REPORT_NAME
    if dry_run:
        out.lines.append("dry-run: gate-report.json not written")
        return out
    data: dict = {}
    if report.is_file():
        try:
            loaded = json.loads(report.read_text(encoding="utf-8"))
            data = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            data = {}
    if not data:
        data = {"tool": "bumm-guard", "version": __version__,
                "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "network": network, "account": account,
                "wasm": {"path": wasm, "sha256": local, "exists": True},
                "artifact": {"wasm_sha256": local, "abi_sha256": None},
                "simulate": None, "storage_estimate": None}
    else:
        prev = (data.get("artifact") or {}).get("wasm_sha256")
        if prev and prev != local:
            out.errors.append("bumm-guard: warning: existing gate-report.json is for a different wasm "
                              f"({prev}); post_deploy was compared with {wasm}")
    data["post_deploy"] = block
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    out.report_path = str(report)
    out.lines.append(f"post_deploy written to {report}")
    return out
