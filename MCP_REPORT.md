# MCP_REPORT (BUMM Guard 0.3.0)

## Built
- `bumm_guard/mcp_server.py`, entry point `bumm-guard-mcp` (stdio). Tools: `list_rules`, `scan_contract(path, exclude?)`, `fix_contract(path, apply=false, exclude?)`, `gate_deploy(crate_dir, wasm?, account?, network="testnet")`. Tool logic is plain stdlib Python; the `mcp` SDK is imported lazily in `build_server()`. Works with the mcp 2.x class (`MCPServer`) and the 1.x class (`FastMCP`).
- `cli.py` refactor: `scan_to_dict()` and `fix_to_dict()` extracted; `render_json`/`render_fix_json` call them. CLI output unchanged.
- `gate_deploy` calls `run_gate(..., execute=False, dry_run=False)`: no `execute` parameter exists; `deploy_executed` is always `false`; the command is returned as text only. On a pass it writes `<crate_dir>/.bumm-guard/gate-report.json` (same as the CLI) and returns the absolute `report_path`.
- Path validation: every path (`path`, `crate_dir`, `wasm`) is resolved with `realpath` and must be inside the server's cwd; otherwise a tool error. `BUMM_GUARD_ALLOW_ANY_PATH=1` lifts it. Symlinks that escape are refused.
- `pyproject.toml`: version 0.3.0, `[project.optional-dependencies] mcp = ["mcp>=1.0"]`, script `bumm-guard-mcp`. `.gitignore`: `.venv-mcp/`.
- `clients/claude-plugin/`: `.claude-plugin/plugin.json` (name `bumm-guard`), `.mcp.json`, `skills/near-deploy-gate/SKILL.md`, `examples/mcp-config.json`.
- `README.md`: section "Use from a coding agent (MCP)"; version 0.3.0.
- `tests/test_mcp.py`: 12 tests over real stdio subprocess + SDK client (initialize, tools/list = 4 tools and no `execute`, rules, scan d1_vuln = 1 x BG-NEAR-001 and equal to CLI JSON, fix dry-run leaves file unchanged, fix apply on a temp copy writes and re-scans to 0, gate blocked on d1_vuln, gate passes on a temp copy of d1_safe, outside-cwd and symlink escape rejected, `BUMM_GUARD_ALLOW_ANY_PATH`, missing path = tool error). Skipped with a message if `mcp` is missing.

## SDK version
`.venv-mcp/` (python 3.14.7): `mcp 2.3.0` (installed by `pip install -e '.[mcp]'`). Also run in a scratch venv with `mcp 1.30.0` (`pip install 'mcp<2'`): 12/12 pass. Because `mcp>=1.0` is not capped, a future 3.x could break the import; consider `mcp>=1.0,<3`.

## Test output
- `.venv-mcp/bin/python -m unittest discover -s tests`: Ran 83 tests, OK (0 skipped).
- `python3 -m unittest discover -s tests` (no mcp installed): Ran 83 tests, OK (skipped=12, the MCP tests).
- Smoke: `.venv-mcp/bin/bumm-guard-mcp` answers a JSON-RPC `initialize` on stdin.
- Note: I deleted a stale, gitignored `tests/fixtures/near/d1_safe/.bumm-guard/` (a gate report from an earlier manual run, 15:12). It made `test_gate.test_dry_run_writes_nothing` and `test_execute_on_mainnet_is_refused` fail because the tests copy the fixture directory.

## CI step for the coordinator (`.github/workflows/ci.yml`, job `rulepack`)
Add before `unit tests`:
```yaml
      - name: install package with MCP extra
        run: python -m pip install -e '.[mcp]'
```
`python -m unittest discover -s tests -v` then runs the 12 MCP tests too (about 10-15 s). Optional second matrix entry or step without the extra to confirm the skip path: `python -m unittest tests.test_mcp` before the install. The workflow's scan step needs no change. Also consider ignoring/cleaning `tests/fixtures/near/*/.bumm-guard` in scripts (`scripts/linux-test.sh`) before the test run.

## Limitations
- Not verified: `claude mcp add ...` and `claude --plugin-dir clients/claude-plugin` (the `claude` CLI is not installed here); Cursor and Codex configs are untested. The plugin manifest follows the documented layout but was only JSON-validated.
- `bumm-guard-mcp` must be on the PATH of the process that launches it (venv activation, or use an absolute command path in the config).
- `gate_deploy` writes `.bumm-guard/gate-report.json` on a pass (no dry-run option over MCP). `fix_contract(apply=true)` writes files; the "ask the human first" step is enforced only by the tool description and the skill, not by the server.
- Path check is cwd-based: an agent started in `~` can reach everything under `~`.
- Same detector limits as the CLI (three rules, regex/brace level). The gate is not an audit.
- SDK logging (`Processing request of type ...`) goes to stderr only; stdout carries protocol messages only.
