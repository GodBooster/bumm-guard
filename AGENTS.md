# Notes for AI agents (Codex, Claude Code and others)

BUMM Guard checks in a sandbox that a NEAR contract upgrade works on state the old code wrote, binds that proof to the exact pair of code hashes, and only then hands an agent or a human the deploy command. Python 3.12+ standard library for the CLI (`python3 -m bumm_guard`, no install needed from the repository root); a Rust scenario runner (`contracts/sim-runner`, near-workspaces) for the real sandbox run.

## Verify the README's claims

```sh
scripts/judge-check.sh             # offline checks, then read-only testnet checks; about 1-2 minutes
scripts/judge-check.sh --offline   # offline checks only
scripts/judge-check.sh --sandbox   # also the real near-sandbox run (Linux, or macOS arm64); cold runner build 2-6 minutes on CI machines
scripts/judge-check.sh --sandbox --docker   # the same in a Linux container (macOS x86_64)
```

Every check is labelled (`A*` offline, `B*` testnet read-only, `C*` near-sandbox) and printed as PASS, FAIL or SKIP with the reason; the exit code is 0 when nothing failed. If `cargo` is on PATH, two unit tests in A8 run `cargo check --offline` on patched fixture copies (no download); remove cargo from PATH if you must not run it. The same script runs on clean runners in the `judge-replay` workflow (`.github/workflows/judge-replay.yml`: ubuntu with the README steps run literally, macOS arm64, a Linux container, and a run with the network removed), so its logs are the evidence for whatever your machine cannot run.

| claim | check | needs |
|---|---|---|
| the committed wasm files are the ones deployed on testnet | A3 (`evidence/wasm/SHA256SUMS`), B1-B3 | offline / internet |
| rule-pack: three NEAR defect classes, gate blocks on a high finding | A4, A5 | offline |
| the gate accepts a passing upgrade scenario as proof of exactly one hash pair and refuses a failing one | A7: the real gate CLI on the committed CI reports and wasm; only the on-chain code hash is replaced, by the value recorded on testnet before each live upgrade (printed as RECORDED) | offline |
| the live testnet upgrades kept their state; the paid job's approve stored the sha256 of `evidence/review-job-0-2026-11-07.json` | B1-B3 (`gate --verify-deployed` with smoke views) | internet |
| the live gate on the demo account | B4 | internet |
| a code-only upgrade breaks, deploy + `migrate` in one batch works, in a real near-sandbox | C (six upgrade scenarios on the committed wasm; your own reports are then fed to the gate, C7) | Linux or macOS arm64 with cargo, or Docker |

## Rules for agents working in this repository

- Never sign or send a transaction. Do not run any command with `sign-with-keychain` or `send`, and do not pass `--execute` to `bumm-guard gate`. The gate prints deploy commands for a human to run; printing them is the product.
- The testnet accounts named in `evidence/` belong to the author. Read them (view calls, `gate --verify-deployed`), never write to them.
- Do not rebuild and commit the wasm in `evidence/wasm/`: they are the files that were deployed, checked by hash in CI. A local build differs in bytes.
- Tests: `python3 -m unittest discover -s tests` (Python), `node --test ui/test` (panel), `python3 -m bumm_guard simulate scenarios/*/*.json --validate-only` (scenario format). Cargo builds of `contracts/` and `tests/fixtures/near/` need the pinned toolchains in their `rust-toolchain.toml`.

## Map

`bumm_guard/` CLI, gate, rule-pack, simulate, MCP server · `contracts/sim-runner` scenario runner (Rust) · `tests/fixtures/near/` fixture contracts · `scenarios/` JSON scenarios · `evidence/` CI reports, committed wasm, testnet records · `docs/` formats, architecture, guarded jobs, submission text · `scripts/` judge check, offline gate replay, Linux runner.
