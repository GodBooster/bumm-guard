"""Run the deploy gate on the committed evidence without a network.

For each contract pair below this runs the real `bumm-guard gate` CLI on the fixture crate, the
committed candidate wasm (evidence/wasm/) and a committed CI scenario report
(evidence/*-report-ci.json). The only thing replaced is the gate's one read-only RPC call
(`view_code` of the target account): it returns the old-version code hash that was read on testnet
(RECORDED, see the note of each pair), and any other network use raises.

Three pairs were upgraded live on testnet on 2026-11-03, 04 and 07. The gate of that time accepted
their with-migrate reports. The current gate refuses all three (exit 3, no command): their
`migrate` is written `#[private] #[init(ignore_state)]` and the crates build with near-sdk 5.17.2,
which generates no caller check for an `#[init]` method, so any account can call it (README,
"Honest limits"). The fourth pair, escrow v1 -> v3, is the same upgrade with a caller check in the
body of `migrate`: its with-migrate report is accepted (exit 0, deploy command with
`with-init-call migrate`). A code-only report is refused for every pair (exit 3).

    python3 scripts/replay_gate_offline.py                 # committed CI reports
    python3 scripts/replay_gate_offline.py --reports-dir D # your own reports, same file names

A second, sandbox-only group does the same for the third-party pairs built from the official
near-sdk-rs example `examples/versioned` (tests/fixtures/near_official/NOTICE.md). They were never
deployed on testnet: the "on-chain" hash given to the gate is the old wasm the sandbox ran, read from
evidence/wasm/, and the account name is a placeholder. Expected: the passing code-only report from the
V0 era is accepted without any migrate call (exit 0, `without-init-call`), the failing report from a
plain-struct build is refused (exit 3), and the V0 proof does not cover the plain-struct hash (exit 3).

Exit 0 when every pair gives the expected verdict, 1 otherwise.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bumm_guard import cli  # noqa: E402
from bumm_guard.gate import sha256_file  # noqa: E402

ESCROW_V1 = "ecd8a805ea3252f1ffa2a285964d3e035d6e3c6be16e6974c33c479c3e034443"
OPEN_MIGRATE = "upgraded live with this report; refused now: migrate is callable by any account"
PROBE_OPEN = "the same scenario re-run with the access probe: the other account's migrate call went through"
PROBE_REPORT_SUFFIX = "-probe-report-ci.json"

# pair: (on-chain code sha256 RECORDED on testnet, where and when it was read, candidate crate,
#        [(report name, expected gate exit, what the case shows)])
PAIRS = {
    "N06 -> N10 (bumm-guard-demo.testnet)": (
        "c3f50addfb067db8b9fcfc4b8dc6d1a6a197e66d392648bda372f7a7b3a3f617",
        "2026-11-03 before the live upgrade, evidence/UPGRADE_TESTNET.md", "event_registration_waitlist",
        [("upgrade-with-migrate-report-ci.json", 3, OPEN_MIGRATE),
         ("upgrade-with-migrate-probe-report-ci.json", 3, PROBE_OPEN),
         ("upgrade-code-only-report-ci.json", 3, "code only")]),
    "escrow v1 -> v2 (bumm-guard-escrow.testnet)": (
        ESCROW_V1, "2026-11-04 before the live upgrade, evidence/UPGRADE_TESTNET.md", "escrow_v2",
        [("escrow-upgrade-with-migrate-report-ci.json", 3, OPEN_MIGRATE),
         ("escrow-upgrade-with-migrate-probe-report-ci.json", 3, PROBE_OPEN),
         ("escrow-upgrade-code-only-report-ci.json", 3, "code only")]),
    "swarm_jobs v1 -> v2 (bumm-guard-jobs.testnet)": (
        "bcace58397f2ee597dd5687187ea9e178e3cd5405dfdb810553214e416a9a92b",
        "2026-11-07 before the live upgrade, evidence/SWARM_JOBS_TESTNET.md", "swarm_jobs_v2",
        [("swarm-jobs-upgrade-with-migrate-report-ci.json", 3, OPEN_MIGRATE),
         ("swarm-jobs-upgrade-with-migrate-probe-report-ci.json", 3, PROBE_OPEN),
         ("swarm-jobs-upgrade-code-only-report-ci.json", 3, "code only")]),
    "escrow v1 -> v3 (bumm-guard-escrow2.testnet)": (
        ESCROW_V1, "2026-11-08, evidence/SWARM_JOBS_TESTNET.md: the account still runs escrow v1", "escrow_v3",
        [("escrow-v3-upgrade-with-migrate-report-ci.json", 0, "migrate checks its caller; the access probe is refused"),
         ("escrow-upgrade-with-migrate-report-ci.json", 3, "a proof for another candidate (escrow v2)")]),
}


# sandbox-only pairs: (old wasm whose hash stands in for the on-chain code, candidate crate dir, candidate wasm,
#                      [(report name, expected exit)])
SANDBOX_ACCOUNT = "sandbox-only-versioned.testnet"   # placeholder, never contacted (network use raises)
SANDBOX_PAIRS = {
    "official near-sdk-rs versioned: V0 era -> official code": (
        "versioned_v0", "tests/fixtures/near_official/versioned", "versioned",
        [("official-versioned-v0-upgrade-report-ci.json", 0)]),
    "official near-sdk-rs versioned: plain struct -> official code": (
        "versioned_plain", "tests/fixtures/near_official/versioned", "versioned",
        [("official-versioned-plain-upgrade-report-ci.json", 3),
         ("official-versioned-v0-upgrade-report-ci.json", 3)]),
}


def run_gate(onchain: str, account: str, crate_dir: str, wasm: str, report: Path) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    argv = ["gate", crate_dir, "--wasm", wasm,
            "--account", account, "--network", "testnet", "--simulate-report", str(report), "--dry-run"]
    with mock.patch("bumm_guard.gate.onchain_code_sha256", return_value=onchain), \
            mock.patch("urllib.request.urlopen", side_effect=RuntimeError("network use is not allowed here")), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue() + err.getvalue()


def check_reports(reports_dir: Path, onchain: str, account: str, crate_dir: str, wasm: str,
                  cases: list[tuple]) -> bool:
    ok = True
    for name, want, *why in cases:
        report = reports_dir / name
        if name.endswith(PROBE_REPORT_SUFFIX) and not report.is_file() and reports_dir != ROOT / "evidence":
            # Only the committed evidence keeps the earlier report and the probe re-run side by side. A
            # fresh run writes one with-migrate report per pair, and it already carries the probe.
            print(f"   skip: {name}: only in the committed evidence (this run's with-migrate report has the probe)")
            continue
        if not report.is_file():
            print(f"   FAIL {name}: not found in {reports_dir}")
            ok = False
            continue
        code, text = run_gate(onchain, account, crate_dir, wasm, report)
        keep = [ln for ln in text.splitlines()
                if ln.startswith(("upgrade of", "proved after", "state at the upgrade", "access probe",
                                  "migration migrate", "DEPLOY BLOCKED", "gate passed", "  near contract deploy"))]
        verdict = "ok" if code == want else "FAIL"
        ok &= code == want
        print(f"   {verdict}: {name}: exit {code} (expected {want}" + (f"; {why[0]}" if why else "") + ")")
        for ln in keep:
            print(f"      {ln.strip()[:220]}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reports-dir", type=Path, default=ROOT / "evidence",
                    help="directory with the scenario reports (default: evidence/)")
    args = ap.parse_args()
    args.reports_dir = args.reports_dir.resolve()
    os.chdir(ROOT)
    ok = True
    for pair, (onchain, read, crate, cases) in PAIRS.items():
        print(f"== {pair}")
        print(f"   on-chain code sha256 (RECORDED on testnet {read}): {onchain}")
        account = re.search(r"\((\S+)\)", pair).group(1)
        ok &= check_reports(args.reports_dir, onchain, account, f"tests/fixtures/near/{crate}",
                            f"evidence/wasm/{crate}.wasm", cases)
    for pair, (old, crate_dir, new, cases) in SANDBOX_PAIRS.items():
        onchain = sha256_file(f"evidence/wasm/{old}.wasm")
        print(f"== {pair} (sandbox only, never deployed on testnet; account {SANDBOX_ACCOUNT} is a placeholder)")
        print(f"   \"on-chain\" code sha256 = evidence/wasm/{old}.wasm, the old code the sandbox ran: {onchain}")
        ok &= check_reports(args.reports_dir, onchain, SANDBOX_ACCOUNT, crate_dir, f"evidence/wasm/{new}.wasm", cases)
    print("offline gate replay:", "all pairs as expected" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
