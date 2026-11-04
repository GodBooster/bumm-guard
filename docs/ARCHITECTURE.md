# BUMM Guard — architecture and what was built (state on 2026-11-04)

This is the review document for the project: what the thing is, how the parts fit, what each part verifiably does, what evidence exists, and what is deliberately not claimed. It is written for a reader who has not seen the repository. Every number here is either in a committed file (named) or in a CI run (numbered). Facts and hypotheses are kept apart: a hypothesis is marked *(hypothesis)*.

Repository: `GodBooster/bumm-guard` (private until 2026-11-13), `main` at `a3bead6`, 60 commits on four working days (2026-10-30, 11-02, 11-03, 11-04). License MIT. Written during the NEAR hackathon window with Claude Code; BUMM (the author's Solana builder) is prior work and is not in this repository.

## 1. The one sentence and the one failure

**BUMM Guard proves that a change to a NEAR contract works on the state that is already deployed, before an agent or a human deploys it.**

The failure it targets is specific to NEAR's storage model. Contract state is Borsh-serialised into the account's key-value store. When new code with a different struct layout is deployed over an account that holds state written by the old layout, the deploy transaction *succeeds*, and then:

- if the **root struct** changed, every method panics with `Cannot deserialize the contract state.` (near-sdk reads the root on every call);
- if only a **record inside a collection** changed (`IterableMap<u64, Deal>`, etc.), the root still loads, "shallow" views keep answering, and every method that reads a record panics with `Cannot deserialize element`.

Unit tests, `cargo near` builds, fresh-deploy integration tests and a post-deploy smoke view do not see this: they never run the new code against old state. The correct NEAR idiom is a `migrate()` method with `#[init(ignore_state)]` and `#[private]`, called in the same batch as `DeployContract` so a failing migration rolls the deploy back. Coding agents reliably write the feature and unreliably write the migration (one pilot, below).

Everything in the repository exists to make this failure visible before deploy and to refuse the deploy until it is proven absent for the exact pair of code hashes involved.

## 2. Components

```
                 ┌──────────────────────────────────────────────────────────┐
                 │  bumm_guard  (Python 3.12+, stdlib only, 2 009 lines)     │
                 │                                                          │
  .rs sources ──►│  scanner ─► rulepack (3 rules) ─► fix (2 fixers)          │
                 │                                                          │
  scenario.json─►│  simulate ──spawns──► bumm-sim (Rust, near-workspaces)   │──► simulate-report.json
                 │                        └─► near-sandbox (local chain)    │
                 │                                                          │
  wasm + account►│  gate ─► storage (RPC) ─► verify (RPC view_code/account) │──► gate-report.json
                 │          └─ find_upgrade_proof / migration_guard          │    + printed deploy cmd
                 │                                                          │
  agent (stdio)─►│  mcp_server (6 tools, FastMCP)                            │
                 └──────────────────────────────────────────────────────────┘
                 contracts/sim-runner  (bumm-sim, Rust 1.99, near-workspaces 0.24)
                 contracts/sandbox     (near-workspaces test for BG-NEAR-001)
                 tests/fixtures/near   (11 crates, rustc 1.85.0 pinned)
                 scenarios/            (14 JSON scenarios, 4 of them upgrades)
                 ui/                   (static ABI panel, 794 lines JS/HTML)
                 clients/claude-plugin (Claude Code plugin + skill)
                 .github/workflows     (ci.yml 5 jobs, evaluate.yml dispatch)
                 evidence/             (8 JSON reports, 4 markdown records)
```

### 2.1 Static rule-pack (`bumm_guard/scanner.py`, `rulepack/`, `fix/`)

Three NEAR-specific defect classes, detected at regex/brace level over `.rs` text (no parser, no type information):

| Rule | Detects | Fix |
|---|---|---|
| BG-NEAR-001 `non-private-callback` | a method scheduled via `.then(Self::ext(..).cb(..))` that has no `#[private]`: anyone can call it with a forged promise result | adds `#[private]` |
| BG-NEAR-002 `callback-ignores-failure` | state mutated before a cross-contract call, callback has no failure branch (no rollback) | suggested patch only (a correct rollback is semantic) |
| BG-NEAR-003 `signer-auth` | authorisation by `env::signer_account_id()` instead of `predecessor_account_id()` (phishable through an intermediate contract) | rewrites to `predecessor_account_id()` |

CLI: `scan` (exit 1 on high/critical, 2 on usage error), `rules`, `fix` (dry run by default). Each rule has a `*_vuln`/`*_safe` fixture pair; the full fixture scan gives exactly 3 findings in 11 files, asserted in CI. One run over 290 real `.rs` files from near-sdk examples/standards is recorded in `RULEPACK_REPORT.md` (5 + 0 + 7 findings, two true-positive highs); it is a sanity check, not a false-positive rate.

### 2.2 Scenario runner (`bumm_guard/simulate.py` + `contracts/sim-runner`)

A scenario is a JSON file (format: `docs/FORMATS.md` §2): accounts with balances, a contract wasm with an init call, and a linear list of steps of kind `call`, `view`, `balance` and — the addition of this project — **`upgrade`** (§2a): deploy a second wasm to the same account, optionally with a `migrate` call in the same batch, then keep running steps on the upgraded state. Expectations: `ok`, `fail_contains`, `equals`, `contains`, `at_least/at_most`.

Python validates the file (`--validate-only` runs nothing and works on any OS), hashes the scenario and the wasm files, spawns `bumm-sim`, and renders a report (`FORMATS.md` §3): per step `passed`, `expected`, `actual`, and for failures the panic text in `error_detail` (view steps included; that is what lets CI assert the exact failure string). `bumm-sim` is a Rust binary on `near-workspaces 0.24` that starts a local `near-sandbox`; Linux only (no local real run on macOS x86_64, so every real run in this project happened in GitHub Actions).

Two contract pairs are shipped as fixtures, written by the author to exhibit the two failure shapes:

| Pair | Change | Code-only upgrade in sandbox | With `migrate()` |
|---|---|---|---|
| `event_registration` (N06) → `event_registration_waitlist` (N10) | root struct gains waitlist fields | FAIL 4/16: every call after the upgrade → `Cannot deserialize the contract state.` | PASS 16/16 |
| `escrow_v1` → `escrow_v2` | `Deal` record gains `status`, `created_at_ms` in place of `released`; root unchanged | FAIL 9/17: `deal_count` *passes* (answers 3), every `get_deal`/`release`/`refund` → `Cannot deserialize element` | PASS 33/33 |

CI asserts the failing shape exactly (which steps pass, which fail, with which string, no infrastructure error), so a different failure turns the job red. The reports are committed: `evidence/upgrade-*-report-ci.json`, `evidence/escrow-upgrade-*-report-ci.json`.

Fixtures are pinned to `rustc 1.85.0` via `rust-toolchain.toml` (near-sdk 5.17.2 wasm does not link on 1.99); `bumm-sim` builds on stable 1.99. Because of the pin and dev-dependency MSRVs, fixtures are `cargo check`ed in CI rather than `cargo test`ed.

### 2.3 Deploy gate (`bumm_guard/gate.py`, `verify.py`, `storage.py`)

`gate <crate> --wasm W --account A --network testnet [--simulate-report R] [--estimate-storage]`. It is **pipeline control**: it decides whether to print a deploy command, and by default never runs one. Decision order (all read-only RPC):

1. Scan the crate; any high/critical finding → `DEPLOY BLOCKED`, exit 3.
2. If a simulate report is given: it must be for this exact wasm (`wasm_sha256` in the report = sha256 of `--wasm`), else blocked. Binding by hash is what stops "I tested *a* build".
3. `view_code(A)`:
   - no code → `view_account`: if the account uses a **global contract** (NEP-591, `global_contract_hash` / `global_contract_account_id`) → blocked (out of scope, refused rather than treated as empty); else `fresh deploy`, no upgrade proof required (a report with upgrade scenarios only triggers a warning).
   - code hash = candidate hash → `already runs this exact wasm … deploying it again changes no code`; treated as a no-op redeploy (passes, warning if no report). *Verified live on 2026-11-04.*
   - code hash ≠ candidate → **this is an upgrade**. `find_upgrade_proof` searches the report for a *passing* scenario whose `upgrade` step went from exactly the on-chain hash to exactly the candidate hash, recorded the deployed `code_sha256`, and has at least one step after the upgrade. None → blocked, with a structured `upgrade_check` (`status`: `no-report` / `no-proof` / `report-proves-other-pair`, plus every pair the report does prove).
4. If the proof carries a `migrate` call: `migration_guard` finds the method in the candidate crate's sources and requires `#[private]` or a `predecessor_account_id()` check in the first 15 lines of its body. Unprotected → blocked (anyone could call `migrate` after the deploy). Not found → warning (the gate cannot vouch for a wasm built elsewhere). Ambiguous (several definitions) → blocked.
5. `--estimate-storage`: reads `EXPERIMENTAL_protocol_config` for the per-byte price and the account's balance/`storage_usage`; prints the NEAR that the code will lock (a locked balance, not a fee).
6. Pass: writes `.bumm-guard/gate-report.json` (`FORMATS.md` §4: scan summary, wasm/abi hashes, simulate binding, `upgrade` block, `migration_guard`, storage, account) and prints exactly one `near contract deploy …` command — `without-init-call` for a fresh deploy, `with-init-call <migrate> json-args '…' prepaid-gas '…'` for a proven upgrade, i.e. deploy + migrate in one batch.

`--execute` runs the printed command; it is off by default, refuses `--network mainnet` (exit 2), requires `near` on PATH, and is not exposed through MCP. The repository ships no key and no account.

`gate --verify-deployed A --wasm W --smoke M[:ARGS]…` is the post-deploy half: `view_code` hash vs artifact hash (`MATCH`/`MISMATCH`), then the given view calls; exit 5 on failure. Reports are written to the same file.

### 2.4 MCP server and agent client (`bumm_guard/mcp_server.py`, `clients/claude-plugin`, `.mcp.json`)

`bumm-guard-mcp` (installed with the `mcp` extra; FastMCP over stdio) exposes six tools: `list_rules`, `scan_contract`, `fix_contract`, `gate_deploy`, `estimate_storage`, `verify_deployed`. `gate_deploy` returns the same decision as the CLI as JSON (`passed`, `reasons`, `upgrade`, `migration`, `upgrade_check`, `deploy_command`) and has no execute path. The repository root carries a project-scope `.mcp.json`, so a Claude Code session opened in the repo sees the server without configuration; `clients/claude-plugin` is the same server as a plugin with a skill, `near-deploy-gate`, that tells the agent the order (scan → simulate → gate → hand the command to the human; if no runner is available where the agent runs, ask the human for a CI report rather than skip the step). MCP is tested over a real stdio subprocess (`tests/test_mcp.py`, 14 tests).

A dry run with a real agent (Claude Code desktop, 2026-11-03, `docs/DEMO_AGENT.md`): given the prompt "upgrade this account to escrow v2, the proof is in `evidence/`", the agent discovered the six tools, called `scan_contract`, then `gate_deploy` three times (no report → blocked; code-only report → blocked "did not pass"; migrate report → passed with the command), and stopped with the command for the human. Two minutes. The structured `upgrade_check` was added after this run because the agent's first blocked result did not say *which* pairs the report did prove.

### 2.5 ABI panel (`ui/`)

A static page (no backend) that loads a contract ABI and calls view methods through public RPC; the change-call path through a wallet is written but **not verified**. It is an interaction panel, tested with `node --test`, and is not part of the main thesis.

### 2.6 CI (`.github/workflows/`)

`ci.yml`, on every push and PR, 5 jobs, `permissions: contents: read`, timeouts set; about 2 minutes with a warm cache:

1. `rule-pack tests (python)`: 203 unit tests (14 skipped outside the MCP venv), the fixture scan assertion, the migration-guard assertion on the real fixtures.
2. `ABI panel tests (node)`.
3. `fixtures compile (cargo check, wasm32)`: `--workspace --locked`, host and `wasm32-unknown-unknown`, pinned toolchain.
4. `sandbox tests (near-workspaces, linux only)`: `contracts/sandbox` runs the BG-NEAR-001 exploit against the vulnerable fixture and its absence against the fixed one.
5. `scenario runner (bumm-sim on near-sandbox, linux only)`: builds N06, N10, escrow v1, v2 (rustc 1.85.0) and `bumm-sim` (1.99.1); runs the N06 and escrow fresh scenarios; runs the gate against each report (fresh deploy, binding by hash); runs the four upgrade scenarios, two expected to pass, two expected to fail *after* the upgrade step with the exact panic string; uploads `simulate-report` and the four wasm files as artifacts. **The wasm that CI simulated is the wasm the gate later accepts**: a local rebuild differs in bytes, so the artifact is the deployable.

`evaluate.yml` (manual dispatch; inputs `crate_dir`, `scenarios`, `upgrade=candidate`, `label`, `toolchain`) builds any crate from any branch and scores it with the same runner. It scored the A/B pilot.

## 3. Evidence on a live network (testnet, all signed by the owner)

Nothing in the repository holds or uses a key. In every case below the gate printed a command and the owner ran it with `near-cli-rs` from their own keychain. All transactions are linked in `evidence/E2E_TESTNET.md` and `evidence/UPGRADE_TESTNET.md`.

| Date | Account | What happened | Result |
|---|---|---|---|
| 2026-11-02 | `bumm-guard-demo.testnet` | fresh deploy of CI-built N06 through the gate (simulate 3/3 → gate → storage estimate 1.81 NEAR → deploy) | `--verify-deployed`: code hash = artifact, MATCH |
| 2026-11-03 | same | owner `register`s (state exists); gate for N10: no proof → BLOCKED; code-only report → BLOCKED; migrate report → passed, printed deploy+`migrate` batch; owner ran it (tx `8kvdZu87…`) | MATCH on `8526195a…`; the registration survived; `get_waitlist` answers |
| 2026-11-04 | `bumm-guard-escrow.testnet` | fresh v1 through the gate; `new`; one deal of 1 NEAR from `bumm-guard-demo`; gate for v2: BLOCKED / BLOCKED "did not pass" / passed with `migration migrate: #[private] (src/lib.rs:118)`; owner ran it (tx `DPgBviLp…`) | MATCH on `13425a04…`; `get_deal(0)` returns the v1 deal in v2 shape (`status: "Funded"`, `created_at_ms: 0`) |
| 2026-11-04 | `bumm-guard-demo.testnet` + `alice`, `bob` | waitlist on the migrated N10: alice takes the last seat, bob lands on the waitlist, alice cancels → bob promoted | each state read from the archival RPC at that receipt's block (276189585/596/606) |
| 2026-11-04 | `bumm-guard-escrow2.testnet` | prepared on v1 with one deal for the video recording; the three gate calls dry-run to the scripted outputs | not upgraded yet, on purpose |

## 4. The pilot (one run per arm, not statistics)

`evidence/AB_PILOT_N10.md`: the same task (add a waitlist to N06), same model, one run per arm. Arm A had the BUMM Guard pipeline (scan, simulate, upgrade step, gate); arm B had nothing. Fresh deploy: both 3/3 scenarios, 35/35 steps; B was faster (4.5 min vs 15). Upgrade of the contract with state: A (which wrote `migrate()`) PASS 16/16; B FAIL — the code that passed every fresh-deploy check could not read the existing state. Interpretation that the repository makes: the failure is real and a fresh-deploy check cannot see it. Interpretation that it does not make: how often agents make it (n = 1).

## 5. Formats as the contract between parts

`docs/FORMATS.md` is the normative file: §1 build artifact, §2 scenario (§2a upgrade step), §3 scenario result, §4 gate report, §5 panel inputs, §6 independent acceptance for N06/N10. Every producer and consumer in the repository (Python, Rust runner, CI assertions, MCP) reads these shapes; the sha256 fields are the glue (scenario file, old wasm, new wasm, report binding, deployed code).

## 6. Honest limits (also in README "Honest limits")

- Detection, fixes and the migration guard are regex/brace level: no macro expansion, no name resolution; helper-delegated checks (`self.assert_owner()`), DAO or access-key policies are not recognised — the gate then blocks a possibly safe contract (add `#[private]` to pass).
- The sandbox state is the scenario's own transactions, not a copy of live state; the proof covers the listed steps on that state.
- The `upgrade` step models a full-access-key batch by the contract account; not DAO-controlled upgrades; a cross-contract call inside `migrate` is not rolled back with a failing batch.
- Linux-only real runs; the author's two contract pairs only; no third-party contract through the runner; wallet path of the panel unverified; no mainnet.
- The gate is not an on-chain guarantee: anyone with shell access can run `near contract deploy` by hand. It is the step a coding agent's workflow goes through.

## 7. Repository map

| Path | What |
|---|---|
| `bumm_guard/` | Python package: `cli.py` (251), `gate.py` (562), `simulate.py` (524), `mcp_server.py` (270), `verify.py` (141), `storage.py` (139), `scanner.py`, `model.py`, `rulepack/near/*`, `fix/*` |
| `contracts/sim-runner/` | `bumm-sim` (Rust): scenario execution on near-workspaces; `tests/upgrade_sandbox.rs` |
| `contracts/sandbox/` | near-workspaces test for BG-NEAR-001 |
| `tests/` | 10 Python test modules, 203 tests; `tests/fixtures/near/` 11 crates (d1–d3 vuln/safe, proxy, N06, N10, escrow v1, v2) |
| `scenarios/` | `n06/` (3), `n10/` (3 acceptance), `escrow/` (2), `upgrade/` (4) |
| `evidence/` | CI reports (6 JSON), testnet gate reports (3 JSON), `E2E_TESTNET.md`, `UPGRADE_TESTNET.md`, `AB_PILOT_N10.md`, `AB_TASK_N10.md` |
| `docs/` | `FORMATS.md` (normative), `DEMO_AGENT.md` (agent rehearsal), `SUBMISSION.md` (form text, X post, video script), this file |
| `clients/claude-plugin/` | plugin manifest, `.mcp.json`, skill `near-deploy-gate` |
| `ui/` | ABI panel + node tests |
| `.github/workflows/` | `ci.yml`, `evaluate.yml` |
| `README.md` | 293 lines: thesis, measured section, "Prove an upgrade (do X, see Y)", testnet, try-it, MCP, gate, rules, runner, panel, testing, limits |

## 8. Questions a reviewer should push on

These are the places where the author expects disagreement or wants a second opinion.

1. **Scope of the proof.** The proof is "these steps pass on state the scenario itself created". Is it worth adding a mode that seeds the sandbox from a live account's state (RPC `view_state` → sandbox patch), or does that over-promise? *(hypothesis: a `view_state` seed for small contracts is one day of work and changes the claim from "on the scenario's state" to "on a copy of the live state at block N")*.
2. **No-op redeploy passes.** When the account already runs the candidate hash, the gate passes with a warning. Correct (nothing changes) or should it block as "nothing to deploy"?
3. **Migration guard false blocks.** The regex guard blocks `migrate` protected through a helper or a DAO. Is a documented false block acceptable at this stage, or should "not recognised" be a warning like "not found"?
4. **Rule-pack value.** Three regex rules with a 290-file sanity run. Keep as a secondary layer, or drop from the pitch to keep the thesis single?
5. **Agent integration surface.** MCP (6 tools) + a Claude Code skill. Cursor/Codex configs are not verified. Is a GitHub Action (`uses: bumm-guard/gate@v0`) a more natural surface for CI-centred teams than MCP?
6. **Evidence hygiene.** CI wasm artifacts are the deployables because local rebuilds are not byte-identical. Should reproducible builds (`cargo near build --reproducible-wasm`, SourceScan) be the next step so the gate can also check "this wasm came from this source"?
7. **Pilot size.** One run per arm. A second pilot where both agents are told the contract is live (`research/bumm_value_study/`) was planned and deliberately not run before submission. Worth running after?
8. **What is NEAR-specific vs portable.** The failure (Borsh state + no migration) and the fix (`#[init(ignore_state)]` + batch) are NEAR's. The gate shape (bind proof to hashes, refuse without it, print don't execute) is portable to any chain with upgradeable code. Is the NEAR framing right for the hackathon, and the portable framing right for the company?

## 9. Timeline (from `git log`, commit dates)

- 10-30 — rule-pack (3 detectors), fixtures d1–d3, CLI, first CI; fix-loop and deploy gate (v0.2.0); MCP server + plugin (v0.3.0); ABI panel; shared formats; scenario runner (`bumm-sim`) with the N06 fixture.
- 11-02 — gate binding to simulate report by hash, storage estimate, `--verify-deployed`; N10 acceptance scenarios written before the implementation; first testnet deploy through the gate; `evaluate.yml`; A/B pilot run and recorded; `upgrade` step (FORMATS §2a) and the upgrade proof in the gate (sessions `upgrade-runner`, `docs-pitch`); near-workspaces sandbox test for BG-NEAR-001; rustc 1.85.0 pin; README for judges.
- 11-03 — live N06→N10 upgrade on testnet; architecture debate (12 items K1–K12) and its fixes merged the same day: docs synced (K1), panic text in reports (K8), non-empty proof (K11), global-contract refusal (K3), negative CI evidence committed (K10), escrow pair (K4), `evaluate.yml` toolchain input (K7), migration guard (K2), agent rehearsal with structured `upgrade_check` and project `.mcp.json` (K5); CI hardening.
- 11-04 — live escrow v1→v2 upgrade; waitlist on testnet; video script v2; account for the recording; this document.
