#!/usr/bin/env bash
# One command for judges and AI agents: verifies the README's claims in three labelled groups.
#
#   scripts/judge-check.sh             offline checks, then read-only testnet checks (skipped with a
#                                      reason when the RPC is unreachable); the sandbox group is skipped
#   scripts/judge-check.sh --offline   offline checks only
#   scripts/judge-check.sh --sandbox   also build bumm-sim and run the eight upgrade scenarios in a real
#                                      near-sandbox on the committed wasm (Linux x86_64/aarch64 or
#                                      macOS arm64; needs cargo; the cold runner build took about 2 min on
#                                      a macOS arm64 CI runner and 5.5-7 min on ubuntu, and downloads Rust
#                                      crates and the near-sandbox binary)
#   scripts/judge-check.sh --sandbox --docker
#                                      the same inside a Linux container (for macOS x86_64, where
#                                      near-sandbox has no native binary); needs Docker
#
# Nothing here signs or sends a transaction, and nothing needs a NEAR account or key.
# Exit 0 when no check failed (skipped checks are listed with their reason), 1 otherwise.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
OFFLINE=0; SANDBOX=0; DOCKER=0
for a in "$@"; do
  case "$a" in
    --offline) OFFLINE=1 ;;
    --sandbox) SANDBOX=1 ;;
    --docker) DOCKER=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

OUT="$ROOT/.bumm-guard/judge"
mkdir -p "$OUT"
RESULTS=()
FAILED=0
LOG="$OUT/last.log"

record() { # status id text
  RESULTS+=("$(printf '%-5s %-4s %s' "$1" "$2" "$3")")
  [ "$1" = "FAIL" ] && FAILED=1
  printf '  -> %s\n' "$1"
}

# check ID "claim" EXPECTED_EXIT GREP_PATTERN -- command...
check() {
  local id="$1" claim="$2" want="$3" pat="$4"; shift 5
  printf '\n[%s] %s\n  $ %s\n' "$id" "$claim" "$*"
  local t0=$SECONDS
  "$@" >"$LOG" 2>&1
  local code=$?
  sed -e 's/^/    /' "$LOG" | tail -n 12
  local dt=$((SECONDS - t0))
  if [ "$code" != "$want" ]; then
    record FAIL "$id" "$claim (exit $code, expected $want, ${dt}s)"
  elif [ -n "$pat" ] && ! grep -Eq -- "$pat" "$LOG"; then
    record FAIL "$id" "$claim (output lacks /$pat/, ${dt}s)"
  else
    record PASS "$id" "$claim (exit $code, ${dt}s)"
  fi
}

skip() { # id claim reason
  printf '\n[%s] %s\n' "$1" "$2"
  printf '    skipped: %s\n' "$3"
  record SKIP "$1" "$2: $3"
}

sha_check() {
  if command -v sha256sum >/dev/null 2>&1; then (cd evidence/wasm && sha256sum -c SHA256SUMS)
  else (cd evidence/wasm && shasum -a 256 -c SHA256SUMS); fi
}

scan_three_rules() {
  "$PY" -m bumm_guard scan tests/fixtures/near --format json >"$OUT/scan.json"
  local code=$?
  "$PY" - "$OUT/scan.json" <<'EOF' || return 9
import json, sys
r = json.load(open(sys.argv[1]))
ids = sorted(f["rule_id"] for f in r["findings"])
print("findings:", ids)
assert ids == ["BG-NEAR-001", "BG-NEAR-002", "BG-NEAR-003"], ids
EOF
  return $code
}

# --- the sandbox group, run inside a Linux container -------------------------------------------
if [ "$SANDBOX" = 1 ] && [ "$DOCKER" = 1 ]; then
  command -v docker >/dev/null 2>&1 || { echo "--docker: docker not found on PATH" >&2; exit 2; }
  IMAGE="${BUMM_JUDGE_IMAGE:-rust:1.99.1-slim-trixie}"   # Debian 13: python3 3.13
  echo "running the sandbox group in $IMAGE (linux/amd64); the repository is mounted at /work"
  exec docker run --rm --platform linux/amd64 -v "$ROOT":/work -w /work "$IMAGE" sh -c '
    apt-get update -qq >/dev/null && apt-get install -qq -y --no-install-recommends python3 pkg-config libssl-dev ca-certificates curl >/dev/null \
    && scripts/judge-check.sh --offline --sandbox'
fi

echo "BUMM Guard judge check — $(uname -s) $(uname -m), $("$PY" --version 2>&1)"
echo "logs and reports: .bumm-guard/judge/"

# --- group A: offline ----------------------------------------------------------------------------
echo; echo "== A. offline (no network, no Rust, no account)"
check A1 "Python is 3.12 or newer" 0 "" -- "$PY" -c 'import sys; print(sys.version); sys.exit(sys.version_info < (3, 12))'
check A2 "CLI runs from the repository root without installing" 0 "^bumm-guard [0-9]" -- "$PY" -m bumm_guard --version
check A3 "committed wasm files equal evidence/wasm/SHA256SUMS (recorded as the deployed files; B1-B3 and B5 compare them with the chain)" 0 "swarm_jobs_v2.wasm: OK" -- sha_check
check A4 "rule-pack: the fixtures give exactly one finding per rule, exit 1" 1 "BG-NEAR-003" -- scan_three_rules
check A5 "deploy gate blocks a crate with a high finding, exit 3" 3 "DEPLOY BLOCKED" -- "$PY" -m bumm_guard gate tests/fixtures/near/d1_vuln --dry-run
check A6 "all 24 scenario files are valid (format check, nothing executed)" 0 "^24 scenario file\(s\) valid" -- "$PY" -m bumm_guard simulate scenarios/*/*.json --validate-only
check A7 "gate replayed offline on the committed CI reports and wasm: the three pairs upgraded live are refused now (their migrate is callable by any account: near-sdk 5.17.2 drops #[private] on #[init]), escrow v1 -> v3 with a caller check passes, code-only refused (on-chain hash RECORDED; plus the official near-sdk-rs versioned example, sandbox only)" 0 "all pairs as expected" -- "$PY" scripts/replay_gate_offline.py
check A8 "unit tests (with cargo on PATH, two of them also run cargo check --offline on fixture copies)" 0 "^OK" -- "$PY" -m unittest discover -s tests
if command -v node >/dev/null 2>&1; then
  check A9 "ABI panel tests (node)" 0 "# fail 0" -- node --test ui/test
else
  skip A9 "ABI panel tests (node)" "node not found on PATH (needs Node 22)"
fi

# --- group B: read-only testnet ------------------------------------------------------------------
echo; echo "== B. read-only testnet (needs internet; no key; nothing is signed)"
rpc_up() {
  "$PY" - <<'EOF'
import json, urllib.request
req = urllib.request.Request("https://rpc.testnet.near.org", data=json.dumps(
    {"jsonrpc": "2.0", "id": "j", "method": "status", "params": []}).encode(),
    headers={"Content-Type": "application/json"})
json.load(urllib.request.urlopen(req, timeout=10))
EOF
}
if [ "$OFFLINE" = 1 ]; then
  for id in B1 B2 B3 B4 B5 B6; do skip "$id" "testnet check" "--offline given"; done
elif ! rpc_up >/dev/null 2>&1; then
  for id in B1 B2 B3 B4 B5 B6; do skip "$id" "testnet check" "https://rpc.testnet.near.org unreachable; group A7 replays the gate on the recorded hashes instead"; done
else
  check B1 "escrow upgraded live: code = escrow_v2.wasm, the v1 deal reads back in the v2 shape" 0 "post-deploy verification passed" -- \
    "$PY" -m bumm_guard gate --verify-deployed bumm-guard-escrow.testnet --wasm evidence/wasm/escrow_v2.wasm --network testnet --dry-run \
      --smoke 'get_deal:{"id":0}={"id":0,"buyer":"bumm-guard-demo.testnet","seller":"bumm-guard-escrow.testnet","amount":"1000000000000000000000000","status":"Funded","created_at_ms":0}'
  check B2 "N06 -> N10 upgraded live: code = event_registration_waitlist.wasm, new method answers" 0 "smoke get_waitlist: ok" -- \
    "$PY" -m bumm_guard gate --verify-deployed bumm-guard-demo.testnet --wasm evidence/wasm/event_registration_waitlist.wasm --network testnet --dry-run \
      --smoke get_registered_count --smoke get_waitlist
  REVIEW0_SHA="$("$PY" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' evidence/review-job-0-2026-11-07.json)"
  check B3 "jobs contract upgraded live to v2; job 0 paid, approve bound to the committed review report (sha256 ${REVIEW0_SHA:0:8}…)" 0 "\"status\": \"Paid\".*\"gate_report_sha256\": \"$REVIEW0_SHA\"" -- \
    "$PY" -m bumm_guard gate --verify-deployed bumm-guard-jobs.testnet --wasm evidence/wasm/swarm_jobs_v2.wasm --network testnet --dry-run \
      --smoke 'get_job:{"id":0}'
  # bumm-guard-escrow2.testnet was upgraded to escrow v3 (migrate() checks its caller) on 2026-11-11, for the
  # demo video (evidence/UPGRADE_TESTNET.md). Before that the gate printed the proven upgrade command here;
  # now the account runs the candidate and the gate answers NO CHANGE.
  check B4 "live gate on bumm-guard-escrow2.testnet with the committed escrow v3 migrate proof: the account already runs that wasm (NO CHANGE)" 0 "NO CHANGE" -- \
    "$PY" -m bumm_guard gate tests/fixtures/near/escrow_v3 --wasm evidence/wasm/escrow_v3.wasm --account bumm-guard-escrow2.testnet --network testnet \
      --simulate-report evidence/escrow-v3-upgrade-with-migrate-report-ci.json --dry-run
  check B5 "escrow2 upgraded live to v3: code = escrow_v3.wasm, the v1 deal reads back in the v3 shape" 0 "post-deploy verification passed" -- \
    "$PY" -m bumm_guard gate --verify-deployed bumm-guard-escrow2.testnet --wasm evidence/wasm/escrow_v3.wasm --network testnet --dry-run \
      --smoke 'get_deal:{"id":0}={"id":0,"buyer":"bumm-guard-demo.testnet","seller":"bumm-guard-escrow2.testnet","amount":"1000000000000000000000000","status":"Funded","created_at_ms":0}'
  REVIEW4_SHA="$("$PY" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' evidence/review-job-4-2026-11-11.json)"
  check B6 "job 4 (escrow v3) paid; approve bound to the committed review report (sha256 ${REVIEW4_SHA:0:8}…)" 0 "\"status\": \"Paid\".*\"gate_report_sha256\": \"$REVIEW4_SHA\"" -- \
    "$PY" -m bumm_guard gate --verify-deployed bumm-guard-jobs.testnet --wasm evidence/wasm/swarm_jobs_v2.wasm --network testnet --dry-run \
      --smoke 'get_job:{"id":4}'
fi

# --- group C: real near-sandbox ------------------------------------------------------------------
echo; echo "== C. real near-sandbox run of the upgrade scenarios"
CI_NOTE="evidence instead: the judge-replay and ci workflows (README, 'For judges and AI agents') and the committed reports evidence/*-report-ci.json, checked offline by A7"
os="$(uname -s)-$(uname -m)"
if [ "$SANDBOX" = 0 ]; then
  skip C "near-sandbox run" "not requested (pass --sandbox; cold runner build about 2 min on macOS arm64, about 7 min on Linux CI). $CI_NOTE"
elif ! command -v cargo >/dev/null 2>&1; then
  skip C "near-sandbox run" "cargo not found (install rustup; contracts/rust-toolchain.toml pins 1.99.1). $CI_NOTE"
elif [ "$os" = "Darwin-x86_64" ]; then
  skip C "near-sandbox run" "near-sandbox has no macOS x86_64 binary; run scripts/judge-check.sh --sandbox --docker. $CI_NOTE"
else
  check C0 "build bumm-sim (near-workspaces 0.24 runner)" 0 "" -- sh -c 'cd contracts && cargo build --release --locked -p bumm-sim'
  # a code-only run must fail the right way: every step up to and including the upgrade passed (the deploy
  # itself succeeds), and the steps after it that fail do so because the new code cannot read the old state
  code_only() { # report.json, then the simulate command
    local rep="$1"; shift
    "$@"; local code=$?
    "$PY" - "$rep" <<'PYEOF' || return 9
import json, sys
(sc,) = json.load(open(sys.argv[1]))["scenarios"]
assert sc.get("error") is None, sc["error"]
up = next(s["index"] for s in sc["steps"] if s["kind"] == "upgrade")
before = [s for s in sc["steps"] if s["index"] <= up]
after = [s for s in sc["steps"] if s["index"] > up]
assert all(s["passed"] for s in before), "a step up to the upgrade failed"
bad = [s for s in after if not s["passed"]]
assert bad and all("Cannot deserialize" in s["actual"].get("error", "") for s in bad), "unexpected failure"
print(f"shape: steps 0..{up} passed (upgrade included); {len(bad)} of {len(after)} later steps "
      "failed with 'Cannot deserialize'")
PYEOF
    return $code
  }
  n=0
  run_pair() { # scenario-base old-wasm new-wasm migrate-report-name code-only-report-name
    local base="$1" old="$2" new="$3"
    n=$((n + 1)); check "C$n" "sandbox: $base with migrate() passes on the committed wasm" 0 "1 scenario\(s\): 1 passed, 0 failed" -- \
      "$PY" -m bumm_guard simulate "scenarios/upgrade/${base}_with_migrate.json" --runner contracts/target/release/bumm-sim \
        --wasm "evidence/wasm/$old.wasm" --upgrade-wasm "evidence/wasm/$new.wasm" --out "$OUT/$4"
    n=$((n + 1)); check "C$n" "sandbox: $base code only breaks the state after the upgrade step (exit 1 expected)" 1 "^shape: " -- \
      code_only "$OUT/$5" "$PY" -m bumm_guard simulate "scenarios/upgrade/${base}_code_only.json" --runner contracts/target/release/bumm-sim \
        --wasm "evidence/wasm/$old.wasm" --upgrade-wasm "evidence/wasm/$new.wasm" --out "$OUT/$5"
  }
  run_pair n06_to_n10 event_registration event_registration_waitlist upgrade-with-migrate-report-ci.json upgrade-code-only-report-ci.json
  run_pair escrow_v1_to_v2 escrow_v1 escrow_v2 escrow-upgrade-with-migrate-report-ci.json escrow-upgrade-code-only-report-ci.json
  run_pair swarm_jobs_v1_to_v2 swarm_jobs swarm_jobs_v2 swarm-jobs-upgrade-with-migrate-report-ci.json swarm-jobs-upgrade-code-only-report-ci.json
  # the fixed escrow: same upgrade, migrate() checks its caller in the body; the report records the access probe
  probe_refused() { # report.json, then the simulate command
    local rep="$1"; shift
    "$@" || return $?
    "$PY" - "$rep" <<'PYEOF'
import json, sys
(sc,) = json.load(open(sys.argv[1]))["scenarios"]
up = next(s for s in sc["steps"] if s["kind"] == "upgrade")
p = up["access_probe"]
assert p["status"] == "failure" and p["by"] not in sc["accounts"].values(), p
assert p["state_sha256"] == up["state_before"]["sha256"], "the probe ran on another state"
assert p["control_status"] == "success", "the same call from the contract account did not go through"
assert "only the contract itself may migrate" in p["error"], p["error"]
print(f"access probe: {p['method']} sent by {p['by']} was refused: {p['error']}; the same call from the contract account went through")
PYEOF
  }
  check C6b "sandbox: escrow v1 -> v3 with migrate() passes on the committed wasm, and migrate() refuses another caller (access probe)" 0 "^access probe: migrate sent by .* was refused" -- \
    probe_refused "$OUT/escrow-v3-upgrade-with-migrate-report-ci.json" "$PY" -m bumm_guard simulate scenarios/upgrade/escrow_v1_to_v3_with_migrate.json \
      --runner contracts/target/release/bumm-sim --wasm evidence/wasm/escrow_v1.wasm --upgrade-wasm evidence/wasm/escrow_v3.wasm \
      --out "$OUT/escrow-v3-upgrade-with-migrate-report-ci.json"
  # third-party pairs: the official near-sdk-rs example examples/versioned (tests/fixtures/near_official/NOTICE.md)
  check C7 "sandbox: official near-sdk-rs versioned example, V0 era -> official code, code only, passes without migrate" 0 "1 scenario\(s\): 1 passed, 0 failed" -- \
    "$PY" -m bumm_guard simulate scenarios/upgrade/official_versioned_v0_to_v1_code_only.json --runner contracts/target/release/bumm-sim \
      --wasm evidence/wasm/versioned_v0.wasm --upgrade-wasm evidence/wasm/versioned.wasm --out "$OUT/official-versioned-v0-upgrade-report-ci.json"
  check C8 "sandbox: plain-struct build -> official versioned code, code only, breaks the state after the upgrade step (exit 1 expected)" 1 "^shape: " -- \
    code_only "$OUT/official-versioned-plain-upgrade-report-ci.json" "$PY" -m bumm_guard simulate scenarios/upgrade/official_versioned_plain_to_v1_code_only.json \
      --runner contracts/target/release/bumm-sim --wasm evidence/wasm/versioned_plain.wasm --upgrade-wasm evidence/wasm/versioned.wasm \
      --out "$OUT/official-versioned-plain-upgrade-report-ci.json"
  check C9 "gate on the reports this run produced: the three pairs with an open migrate() are refused, escrow v1 -> v3 passes, code-only refused, the official V0 proof passes without migrate" 0 "all pairs as expected" -- \
    "$PY" scripts/replay_gate_offline.py --reports-dir "$OUT"
fi

echo; echo "== summary"
printf '%s\n' "${RESULTS[@]}"
if [ "$FAILED" = 0 ]; then echo "no check failed"; else echo "AT LEAST ONE CHECK FAILED"; fi
exit "$FAILED"
