# Scenario runner (`bumm-guard simulate` / `bumm-sim`): report, 2026-11-02

> Build report of 2026-11-02; numbers are of that date, the README is current.

Contract: `docs/FORMATS.md` §2 (scenario file), §3 (scenario result), §6 (N06 acceptance).
Nothing here was committed. The real sandbox run has **not** been executed; see "Unverified".

## What was built

| path | what |
|---|---|
| `tests/fixtures/near/event_registration/` | The N06 contract. `src/lib.rs` is the pilot's N06 `src/lib.rs` byte for byte (the pilot's working directory is not in the repository), with a 7-line header comment added. Its public methods: `new(capacity)` (init), `register`, `cancel`, `get_capacity`, `get_registered_count`, `get_registered`, `is_registered(account_id)`, `get_seats_left`. When the event is full it panics with `Event is full: capacity N reached, registration refused`. All 7 unit tests are kept. It is a new member of the fixtures workspace (rustc 1.85.0 pinned there), with `near-sdk =5.17.2` plus `non-contract-usage` like the other fixtures, and `unit-testing` as a dev-dependency. |
| `contracts/sim-runner/` | Rust crate `bumm-sim` 0.4.0, a member of `contracts/` (rustc 1.99.1). `src/lib.rs` holds the logic that needs no sandbox: the §2 parser (it rejects unknown fields, step kinds and accounts, and bad amounts), amount parsing (yocto string, `"<decimal> NEAR"`, `"<n> Tgas"`, exact integer arithmetic), alias resolution, expectation evaluation, the §3 report model, hashing and CLI parsing. It has 13 unit tests. `src/main.rs` is the near-workspaces glue. |
| `scenarios/n06/01_capacity_cancel_frees.json` | The §2 example: capacity 2, the third registration is refused with `Event is full`, cancel frees a seat, carol then registers, `get_seats_left == 0`, bob's balance is at least 9 NEAR. 9 steps. |
| `scenarios/n06/02_duplicate_registration_refused.json` | Registering a second time fails with `is already registered`. Count 1, `get_registered == ["alice"]`, `is_registered(bob) == false`, then bob registers. 7 steps. |
| `scenarios/n06/03_cancel_without_registration_refused.json` | Cancel by an account that never registered fails with `is not registered`. A second cancel after a real one also fails. Counts and seats are checked after each step. 8 steps. |
| `bumm_guard/simulate.py` + `cli.py` | `bumm-guard simulate <scenario.json>... [--wasm] [--out] [--runner] [--validate-only]`. Standard library only. |
| `tests/test_simulate.py` | 21 unittest tests: amounts, validation, hashing, summary rendering, and the CLI path driven by a fake `bumm-sim` (a Python script that writes a canned §3 report). |
| `tests/test_rules.py` | One line changed: the whole-fixture-directory scan now scans 8 files instead of 7. `event_registration` is clean (0 findings), so the 3 expected findings and the CI rule-id assertion are unchanged. |

All three scenarios point at `tests/fixtures/near/target/wasm32-unknown-unknown/release/event_registration.wasm`. To build it:
```sh
cd tests/fixtures/near
cargo build --release --locked --target wasm32-unknown-unknown -p event_registration   # rustc 1.85.0 via rust-toolchain.toml
```

### Runner semantics (decisions not spelled out in FORMATS)
- **One sandbox per file.** Each scenario file gets its own `near_workspaces::sandbox()`. Every account is created as `root.create_subaccount(alias).initial_balance(..)`. The wasm is deployed to the sub-account `contract` with 50 NEAR, a fixed amount that covers storage staking and is not part of the format. `init` is called by the contract account with 30 Tgas.
- **Account names are substituted.** A JSON string that exactly equals an account name (or `"contract"`) is replaced by the full sandbox id, in `args` and in expected values. That is how `{"contains": "carol"}` matches `["carol.<root>"]`. Side effect: a string argument that happens to equal an account name is substituted too.
- **Every step runs.** Steps keep executing after a failed expectation, so the report shows the full trace. If the sandbox, deploy or init fails, the remaining steps are still listed, with `actual.status: "skipped"` and the reason.
- **What `actual` holds.** It always has `status` (`success` / `failure` / `skipped`). Calls add `error` (the Display text of the chain's `TxExecutionError`), the return `value` as JSON (`null` for an empty return), `gas_burnt_tgas` (transaction plus receipts, 3 decimals) and `logs`. `fail_contains` falls back to the Debug form of the error if the Display text misses the needle. Balance steps return `value` as a yocto string plus a readable `near`.
- **How expectations compare.** `equals` is JSON equality, with numbers compared numerically. `contains` checks array membership (or that every element of an expected array is present), a substring for strings, and a key or subset for objects. `at_least` / `at_most` work on a JSON integer or an integer string, so they also apply to U128 views. An absent `expect` means `"ok"`.
- **Report fields beyond the §3 example.** Each scenario also carries `file`, `scenario_sha256`, `wasm`, `wasm_sha256`, `accounts` (name to id), `init` and, on infrastructure failure, `error`. Each step also carries `reason` when it failed. At the top level, `wasm_sha256` is the hash shared by all scenarios, or `null` if they use different wasm files. `scenario_sha256` is the file's sha256 for one file; for several files it is the sha256 of their sorted hex digests joined by `\n`. Python computes the same value and refuses a report whose hashes differ from the inputs (exit 1). `version` is the runner's crate version (`0.4.0`, as in the §3 example), while the Python package is still `0.3.0`.
- **Where the wasm is looked up.** In this order: `--wasm`, an absolute path, a path relative to the current directory (run from the repo root), then a path relative to the scenario file.
- **Exit codes.** `bumm-sim`: 0 all passed, 1 any failure (also when the report cannot be written), 2 usage or parse error (no sandbox is started). `bumm-guard simulate`: 0 all passed or all files valid, 1 a scenario failed, no readable report, or a hash mismatch, 2 invalid file or missing wasm, 4 runner not found (it prints `cd contracts && cargo build --release --locked -p bumm-sim`). The runner is looked up via `--runner`, then `$BUMM_SIM`, then `contracts/target/release/bumm-sim`. The default report path is `.bumm-guard/simulate-report.json`. A stale report is deleted before each run.

## Verified (2026-11-02, Intel Mac + Docker Desktop)

1. **Container, `bumm-sim`:** `RUSTUP_AUTO_INSTALL=0 scripts/linux-test.sh contracts test -p bumm-sim --lib --bins --offline` finished with exit 0 in 7 min 13 s (5 min 30 s of compiling, from the cached volume). The lib reported `test result: ok. 13 passed; 0 failed`. The bin (`src/main.rs`, all the near-workspaces calls) compiled, type-checked and linked as a test harness, with 0 tests and 0 compiler warnings. `contracts/Cargo.lock` gained only the `bumm-sim` package entry: no new crate, no version change, nothing downloaded from crates.io. Docker network I/O was about 38 MB, all from the script's `apt-get` and the `rustup target add wasm32` step.
2. **Host, fixtures (rustc 1.85.0, `--offline`):** `cargo check --workspace --locked` passed. `cargo build --release --locked --target wasm32-unknown-unknown -p event_registration` produced a 181 KB wasm, sha256 `825ddd4daa527e33db2710da5854d9fb8748da76b8c17605d1babed79e2fe27a`. Its exports are `new register cancel get_capacity get_registered_count get_registered is_registered get_seats_left`. Lock check: 188 packages added (the `unit-testing` dev-dependency tree, all from the local cache), 0 existing versions changed.
3. **Host, contract unit tests:** `cargo +1.99.1 test --offline --locked -p event_registration` gave 7 passed. They cannot run on 1.85: the dev-only `unit-testing` tree resolves to `darling 0.24`, `serde_with 3.24` and `time 0.3.55`, which need rustc ≥ 1.88, and older copies are not in the local cache. A downgrade would need new downloads. The wasm build and `cargo check` on 1.85 are not affected, because dev-dependencies are not part of those builds.
4. **Python:** `python3 -m unittest discover -s tests` ran 104 tests, OK (skipped=12, the MCP tests, as before). 21 of them are new in `test_simulate.py`. `bumm-guard simulate --validate-only scenarios/n06/*.json` exits 0 with combined `scenario_sha256 378d90e1…bd17b`. `bumm-guard simulate scenarios/n06/*.json` without a runner exits 4 and prints the build command.

## Unverified

- **The real sandbox run.** `bumm-sim run` against near-sandbox has not been executed anywhere. near-sandbox has no macOS x86_64 binary, and starting it in the container would download the sandbox binary, which the traffic limit rules out. Not yet observed: account creation with these balances, deploy, the exact panic text as the chain returns it, gas numbers, the 50 NEAR contract balance being enough, and the three N06 scenarios actually passing. The first CI run is the first real test.
- The Python and Rust validators are kept in sync by hand. The error texts match in substance (the tests check the same cases on both sides), but they are not generated from one source.

## CI job proposed at the time (`.github/workflows/ci.yml`, not edited in this build; since added)

```yaml
  simulate:
    name: scenario runner (bumm-sim on near-sandbox, linux only)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with:
          targets: wasm32-unknown-unknown
      - name: install the fixtures' pinned toolchain
        run: rustup toolchain install 1.85.0 --profile minimal --target wasm32-unknown-unknown
      - uses: Swatinem/rust-cache@v2
        with:
          workspaces: |
            contracts
            tests/fixtures/near
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: build event_registration wasm (rustc 1.85.0)
        working-directory: tests/fixtures/near
        run: cargo build --release --locked --target wasm32-unknown-unknown -p event_registration
      - name: build bumm-sim (rustc 1.99.1)
        working-directory: contracts
        run: cargo build --release --locked -p bumm-sim
      - name: run N06 scenarios
        run: |
          python -m bumm_guard simulate scenarios/n06/*.json \
            --runner contracts/target/release/bumm-sim --out simulate-out/simulate-report.json
      - if: always()
        uses: actions/upload-artifact@v4
        with:
          name: simulate-report
          path: simulate-out/simulate-report.json
          if-no-files-found: warn
```
The step fails on any non-zero exit: 1 a scenario failed or a hash mismatch, 2 invalid file or missing wasm, 4 no runner. The report is uploaded even then (`if: always()`). The existing `sandbox` job (`cargo test --workspace --locked` in `contracts/`) now also runs the 13 `bumm-sim` lib tests, with no change needed.

## Known limits

- The format only covers a single contract, named accounts, linear steps and JSON args. There are no borsh args, no cross-contract setups, no time travel (`fast_forward`) and no state patching.
- Receipt failures inside a transaction whose final status is success (for example a failed callback that is handled) are not shown in `actual`. Only the final status counts.
- `gas_burnt_tgas` is the sum of the transaction and all receipt outcomes. Init gas and deposit are fixed (30 Tgas, 0), and the contract balance is fixed at 50 NEAR.
- Scenarios run one after another, each with its own sandbox process. Expect about 5–15 s per file on CI on top of building.
- `README.md` was not updated. It still says "7 files" for the fixture scan, 83 tests, and does not document `simulate`. That was left for the merge to avoid conflicts.
