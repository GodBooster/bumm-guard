# Upgrade step (`upgrade`, FORMATS §2a): report, 2026-11-03

Branch `claude/upgrade-step-e0bdc1`. Spec: `docs/FORMATS.md` §2a (not changed). `.github/` not
changed. Nothing merged into `main`. No NEAR accounts, no testnet.

## What was done

| path | what |
|---|---|
| `contracts/sim-runner/src/lib.rs` | `StepKind::Upgrade { wasm, migrate: Option<Migrate { method, args, gas }> }`. Allowed keys: `upgrade`, `migrate`, `expect`; in `migrate`: `method` (required), `args` (default `{}`), `gas` (default `"100 Tgas"`, `DEFAULT_MIGRATE_GAS`). `migrate: null` is the same as no `migrate`. `expect` accepts only `"ok"` and `{"fail_contains": …}` (§2a: "same vocabulary as `call`"); anything else is a parse error, just as `contains` is refused on a `balance` step. Report: `actual.code_sha256` (sha256 of the bytes in the `DeployContract` action), step `kind: "upgrade"`, `by: "contract"`, `method` = migration method (absent for code-only), `wasm` = the path as written in the scenario. On each scenario: `upgrade_wasm` (resolved path) and `upgrade_wasm_sha256`. At the top level: `upgrade_wasm_sha256`. Both are always present and `null` without an upgrade step. CLI: `--upgrade-wasm PATH`. 5 new unit tests (17 in total). |
| `contracts/sim-runner/src/main.rs` | Upgrade = one batch transaction signed by the contract account to itself: `contract.batch().deploy(code)` plus, when there is `migrate`, `.call(Function::new(method).args_json(..).gas(..))`. The sandbox and state are not recreated between steps. `actual` comes from the same `call_actual` that `call` uses (status, error, gas_burnt_tgas, logs), plus `code_sha256`. Every upgrade wasm is read **before** the sandbox starts. A missing file is reported as a scenario `error` and the steps as `skipped`, the same as a missing `contract.wasm`. |
| `contracts/sim-runner/tests/upgrade_sandbox.rs` | Integration test. It builds `event_registration` + `event_registration_waitlist` with rustc 1.85.0, runs the `bumm-sim` binary on both `scenarios/upgrade/*.json` and checks the report (see "CI run" below). It runs in the existing CI job `sandbox` (`cargo test --workspace --locked` in `contracts/`), so `.github/` did not need editing. Locally it is not run: `--lib --bins` excludes it. |
| `bumm_guard/simulate.py`, `cli.py` | Validation of the `upgrade` step, mirroring Rust (same cases, same texts in substance). `validate_scenario(...)["upgrades"]` lists the upgrade-step paths. `bumm-guard simulate --upgrade-wasm PATH` is passed to the runner as `--upgrade-wasm`. The local wasm hash is computed and compared with the report's `upgrade_wasm_sha256`: a mismatch gives exit 1. A report with no upgrade steps must have `null`. A missing upgrade wasm gives exit 2 with the hint `-p event_registration_waitlist`; under `--validate-only` it is only a warning. The summary shows `step N upgrade <file> + migrate` / `(code only)` and the line `upgrade_wasm_sha256 …`. |
| `tests/test_simulate.py` | +9 tests (30 in the file): the repo's upgrade scenarios are valid, `upgrade` errors, upgrade summary, and with the fake runner: pass + hash, `--upgrade-wasm` override (does not touch `contract.wasm`), upgrade hash mismatch, a non-null hash without upgrade steps, missing upgrade wasm, validate-only. |

### Decisions not spelled out in §2a
- **`--wasm` vs `--upgrade-wasm`.** `--wasm` replaces only `contract.wasm` (the old code); `--upgrade-wasm` replaces only the upgrade steps. To score a candidate N10 build, pass `--upgrade-wasm`, **not** `--wasm`.
- **Several upgrade steps.** Each one has its own `actual.code_sha256`. The scenario's `upgrade_wasm_sha256` is the shared hash, or `null` if the files differ.
- **Top-level `upgrade_wasm_sha256`.** It is the hash shared by the scenarios that **have** an upgrade step. Scenarios without one (for example `scenarios/n06/*` in the same run) do not reset it to `null`. If the hashes differ, it is `null`. Python follows the same rule.
- **Atomicity.** `DeployContract` and `FunctionCall` sit in one receipt. If `migrate` panics, NEAR rolls back the deploy too, so the old code stays live. The step's `actual` then has `status: failure` with the panic text, which `{"fail_contains": …}` can check.
- **`#[private] migrate`** works because the signer and predecessor is the contract account itself.
- `wasm` in the step result is the path as written in the scenario. The actually resolved path (after `--upgrade-wasm`) is in the scenario's `upgrade_wasm`.

## What was verified

1. **Container, `bumm-sim`** (`RUSTUP_AUTO_INSTALL=0 scripts/linux-test.sh contracts test -p bumm-sim --lib --bins --offline`): exit 0.
   ```
   running 17 tests
   ... tests::upgrade_step_parsing ... ok
   ... tests::upgrade_step_errors ... ok
   ... tests::upgrade_report_fields ... ok
   ... tests::parses_the_repo_upgrade_scenarios ... ok
   test result: ok. 17 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
   Running unittests src/main.rs ... test result: ok. 0 passed; 0 failed
   ```
   0 compiler warnings. `contracts/Cargo.lock` unchanged, no new crates.
   In addition, `scripts/linux-test.sh contracts test -p bumm-sim --offline --no-run` compiled all three targets (lib, bin, `tests/upgrade_sandbox.rs`).
2. **Python**: `python3 -m unittest discover -s tests` gave `Ran 146 tests … OK (skipped=14)` (MCP tests, as before). `tests.test_simulate` alone: 30 OK.
3. **`bumm-guard simulate --validate-only scenarios/upgrade/*.json scenarios/n06/*.json`**: exit 0, all 5 files valid. Locally the wasm files are not built, so they are shown as `(missing)` with build hints.
4. **CI run on the branch**: see the next section.

## CI run (real near-sandbox)

Pushed to `claude/upgrade-step-e0bdc1`. CI `on: push` passed **all 5 jobs** in two runs: [41615654220](https://github.com/GodBooster/bumm-guard/actions/runs/41615654220) (commit `cf3fdca`) and [41616014833](https://github.com/GodBooster/bumm-guard/actions/runs/41616014833) (commit `b4c0ba1`, the same code with a step trace printed to stderr). The existing `simulate` job (N06 + gate) is green too, so the runner has no regression.

In the `sandbox` job, `tests/upgrade_sandbox.rs` built both wasm files with rustc 1.85.0 and ran **a real upgrade in near-sandbox** (`test upgrade_scenarios_on_near_sandbox ... ok`, about 41 s). bumm-sim output:
```
PASS Upgrade N06 → N10 (upgrade with migrate()): … (16/16 steps passed, 5.7s)
FAIL Upgrade N06 → N10 (code-only upgrade): …    (4/16 steps passed, 8.6s)
```
The test's assertions (all passed): bumm-sim exit = 1; top-level `wasm_sha256` = sha of `event_registration.wasm`; `upgrade_wasm_sha256` = sha of `event_registration_waitlist.wasm`; the upgrade step's `actual.code_sha256` = the same sha in both scenarios; with migrate: `passed: true`; code-only: steps 0–3 passed, steps 4–15 all `failure`.

Step trace from the CI log (abridged):
```
with migrate():
   3 upgrade migrate   passed=true success gas=16.721
   4 view get_capacity          value=2
   6 view get_registered        value=["alice.test.near","bob.test.near"]
   8 call register (alice)      failure "…Account alice.test.near is already registered"   (expected)
  10 view get_waitlist          value=["carol.test.near"]
  12 view is_registered(carol)  value=true      (after bob's cancel, carol moves from the waitlist)
  15 view get_waitlist          value=["dave.test.near"]
code only:
   3 upgrade           passed=true success gas=15.351   (the deploy itself succeeds, as §2a says)
   4 view get_capacity          passed=false failure "unable to fulfill the query request"
   8 call register              passed=false failure "Smart contract panicked: Cannot deserialize the contract state."
  … every step from 4 to 15 is failure (views: the query error; calls: "Cannot deserialize the contract state.")
```
So, in fact: the `upgrade` step performs a real `DeployContract` (+ `migrate`) on the same account with its state, the state survives, and a code-only upgrade breaks the contract on the first access to state. That is the scenario the A/B pilot could not see.

## YAML steps for the `simulate` job (`.github/workflows/ci.yml`; I did not edit it)

Add after the step `build event_registration wasm (rustc 1.85.0)` and after `build bumm-sim`:

```yaml
      - name: build event_registration_waitlist wasm (rustc 1.85.0; N10 upgrade target)
        working-directory: tests/fixtures/near
        run: cargo build --release --locked --target wasm32-unknown-unknown -p event_registration_waitlist
      - name: upgrade N06 -> N10 with migrate() (expected PASS)
        run: |
          python -m bumm_guard simulate scenarios/upgrade/n06_to_n10_with_migrate.json \
            --runner contracts/target/release/bumm-sim --out simulate-out/upgrade-with-migrate.json
      - name: upgrade N06 -> N10 code only (expected FAIL after the upgrade step)
        run: |
          set +e
          python -m bumm_guard simulate scenarios/upgrade/n06_to_n10_code_only.json \
            --runner contracts/target/release/bumm-sim --out simulate-out/upgrade-code-only.json
          code=$?
          set -e
          test "$code" -eq 1 || { echo "expected exit 1 (scenario failed), got $code"; exit 1; }
          python - <<'EOF'
          import json
          r = json.load(open("simulate-out/upgrade-code-only.json"))
          (sc,) = r["scenarios"]
          up = next(s["index"] for s in sc["steps"] if s["kind"] == "upgrade")
          before = [s for s in sc["steps"] if s["index"] <= up]
          after = [s for s in sc["steps"] if s["index"] > up]
          assert sc.get("error") is None, sc["error"]
          assert all(s["passed"] for s in before), [s for s in before if not s["passed"]]
          assert after and all(not s["passed"] and s["actual"]["status"] == "failure" for s in after), after
          assert r["upgrade_wasm_sha256"] and sc["steps"][up]["actual"]["code_sha256"] == r["upgrade_wasm_sha256"]
          print(f"code-only upgrade: steps 0..{up} passed, {len(after)} step(s) after it failed, as expected")
          EOF
```
And add `simulate-out/upgrade-*.json` to the `path` of the `simulate-report` artifact (or upload the whole `simulate-out/`).

**How CI treats the expected FAIL.** Exit 1 from `bumm-guard simulate` alone is not enough, because the same code also means a hash mismatch or a broken report. So the step requires exit 1 **and** checks the report: no infrastructure `error`, all steps up to and including `upgrade` passed, and every step after it is `failure`. Any other outcome (exit 0, 2, 4; an upgrade transaction that failed; a step after the upgrade that passed) turns the job red. The PASS scenario is an ordinary step: any non-zero exit means red.

Once these steps are in `simulate`, the integration test `contracts/sim-runner/tests/upgrade_sandbox.rs` duplicates them. It can be kept as a fast "runner + sandbox" test, or deleted to keep the `sandbox` job shorter (it builds two wasm files and starts two sandboxes).

## Not verified / known limits

- **Locally**, a real upgrade cannot run: near-sandbox does not work on this Intel Mac. The only real run is CI (above). `bumm-guard simulate` (Python) has not been run against the real `bumm-sim` with upgrade scenarios. Only the binary was run directly, from the integration test. The Python path was checked with the fake runner. The YAML steps above are the first real check of it.
- **A failed view after a broken upgrade reads poorly.** `actual.error` for a view holds the RPC's Display text, `unable to fulfill the query request`. The real panic (`Cannot deserialize the contract state`) is only in `error_debug`, which `fail_contains` can see but which is not serialized into the report. This is the existing runner behaviour for all views, not new. Improving it (for example, serializing the root cause) is a separate small task.
- **The gate does not yet see the upgrade.** `bumm-guard gate --simulate-report` compares only `wasm_sha256` with `--wasm`. To gate an upgrade it would need to compare `upgrade_wasm_sha256` with the candidate wasm (for example `gate --wasm NEW --simulate-report R`, accepting `R.upgrade_wasm_sha256 == sha(NEW)`). That is a separate task for the coordinator (the gate and FORMATS §4).
- **`evaluate.yml` cannot score an upgrade candidate as is.** It passes the built wasm as `--wasm`, which replaces the **old** code. Upgrade scenarios need `--upgrade-wasm`, for example a new input `wasm_flag` (default `--wasm`) substituted into `python -m bumm_guard simulate … ${{ inputs.wasm_flag }} "<wasm>"`. Also, `crate_dir=tests/fixtures/near` would not work: `ls …/*.wasm | head -1` picks `d1_safe.wasm`.
- `migrate` has no `deposit` (§2a does not provide one); the call goes with 0 yocto.
- Only the final transaction status is checked, as with `call`. The upgrade transaction has a single receipt, so there are no hidden partial failures.
- The `README.md` / `SIMULATE_REPORT.md` text does not describe `upgrade` yet. I left them alone to avoid conflicts; this report is the description.
