"""Run the deploy gate on the committed evidence without a network.

For each of the three contract pairs that were upgraded live on testnet, this runs the real
`bumm-guard gate` CLI on the fixture crate, the committed candidate wasm (evidence/wasm/) and a
committed CI scenario report (evidence/*-report-ci.json). The only thing replaced is the gate's
one read-only RPC call (`view_code` of the target account): it returns the old-version code hash
that was read on testnet right before that live upgrade (RECORDED, see the `source` column), and
any other network use raises. Expected: the with-migrate report is accepted as the proof of that
exact hash pair (exit 0, deploy command with `with-init-call migrate`), the code-only report is
refused (exit 3, no command).

    python3 scripts/replay_gate_offline.py                 # committed CI reports
    python3 scripts/replay_gate_offline.py --reports-dir D # your own reports, same file names

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

# pair: (on-chain code sha256 RECORDED before the live upgrade, date, source, candidate crate,
#        with-migrate report name, code-only report name)
PAIRS = {
    "N06 -> N10 (bumm-guard-demo.testnet)": (
        "c3f50addfb067db8b9fcfc4b8dc6d1a6a197e66d392648bda372f7a7b3a3f617", "2026-11-03",
        "evidence/UPGRADE_TESTNET.md", "event_registration_waitlist",
        "upgrade-with-migrate-report-ci.json", "upgrade-code-only-report-ci.json"),
    "escrow v1 -> v2 (bumm-guard-escrow.testnet)": (
        "ecd8a805ea3252f1ffa2a285964d3e035d6e3c6be16e6974c33c479c3e034443", "2026-11-04",
        "evidence/UPGRADE_TESTNET.md", "escrow_v2",
        "escrow-upgrade-with-migrate-report-ci.json", "escrow-upgrade-code-only-report-ci.json"),
    "swarm_jobs v1 -> v2 (bumm-guard-jobs.testnet)": (
        "bcace58397f2ee597dd5687187ea9e178e3cd5405dfdb810553214e416a9a92b", "2026-11-07",
        "evidence/SWARM_JOBS_TESTNET.md", "swarm_jobs_v2",
        "swarm-jobs-upgrade-with-migrate-report-ci.json", "swarm-jobs-upgrade-code-only-report-ci.json"),
}


def run_gate(onchain: str, account: str, crate: str, report: Path) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    argv = ["gate", f"tests/fixtures/near/{crate}", "--wasm", f"evidence/wasm/{crate}.wasm",
            "--account", account, "--network", "testnet", "--simulate-report", str(report), "--dry-run"]
    with mock.patch("bumm_guard.gate.onchain_code_sha256", return_value=onchain), \
            mock.patch("urllib.request.urlopen", side_effect=RuntimeError("network use is not allowed here")), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue() + err.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reports-dir", type=Path, default=ROOT / "evidence",
                    help="directory with the scenario reports (default: evidence/)")
    args = ap.parse_args()
    args.reports_dir = args.reports_dir.resolve()
    os.chdir(ROOT)
    ok = True
    for pair, (onchain, date, source, crate, good, bad) in PAIRS.items():
        print(f"== {pair}")
        print(f"   on-chain code sha256 (RECORDED on testnet {date} before the live upgrade, {source}): {onchain}")
        for name, want in ((good, 0), (bad, 3)):
            report = args.reports_dir / name
            if not report.is_file():
                print(f"   FAIL {name}: not found in {args.reports_dir}")
                ok = False
                continue
            account = re.search(r"\((\S+)\)", pair).group(1)
            code, text = run_gate(onchain, account, crate, report)
            keep = [ln for ln in text.splitlines()
                    if ln.startswith(("upgrade of", "proved after", "migration migrate", "DEPLOY BLOCKED",
                                      "gate passed", "  near contract deploy"))]
            verdict = "ok" if code == want else "FAIL"
            ok &= code == want
            print(f"   {verdict}: {name}: exit {code} (expected {want})")
            for ln in keep:
                print(f"      {ln.strip()[:220]}")
    print("offline gate replay:", "all pairs as expected" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
