# BUMM Guard NEAR rule-pack: report

> Build report of 2026-10-30; numbers are of that date, the README is current.

Date: 2026-10-30. Spec: the detector specification of 2026-10-29 (a working note, not in the repository). Python 3.14.7, stdlib only (code kept 3.12 compatible).

## What was built

- `bumm_guard/model.py`: `Finding(rule_id, severity, file, line, message, fix_hint, snippet, rule_name)` + `to_dict()` (a `to_bumm_vulnerability()` mapper existed at the time of this report; removed in 0.5.0 as unused).
- `bumm_guard/rulepack/near/rust_src.py`: shared helper. Masks comments, nested block comments, strings, raw strings and char literals while keeping offsets; brace/paren matching; attribute collection (forward in impl bodies, backward before `impl`); `#[cfg(test)]` item removal; `impl` detection (exported = `#[near]`/`#[near_bindgen]`, trait impls); method extraction (name, attrs, `pub`, receiver, params, body).
- `bumm_guard/rulepack/near/project.py`: crate-level view, callback index from `.then(Self::ext(..)[.with_*(..)]*.cb(..))`, shared self-check exclusion.
- `non_private_callback.py` (BG-NEAR-001), `callback_ignores_failure.py` (BG-NEAR-002), `signer_auth.py` (BG-NEAR-003), registry in `rulepack/near/__init__.py`.
- `bumm_guard/scanner.py` (discovery, grouping per `Cargo.toml`), `bumm_guard/cli.py` (`scan`, `rules`, `--format json|text`, `--exclude`), `python -m bumm_guard`.
- `tests/fixtures/near/`: the seven crates + the workspace `Cargo.toml`/`Cargo.lock` from the research phase. `cargo check --offline --workspace` on a copy: all 7 compile (near-sdk =5.17.2, rustc 1.85).

## Test output

```
$ python3 -m unittest discover -s tests
.........................................
Ran 41 tests in 0.27s
OK
```

Covered: each `*_vuln` fixture gives exactly its own finding (lines 23 / 33 / 18, severity high); `*_safe` and `proxy` give 0; whole fixture dir gives 3 findings; CLI exit codes (1 high, 0 clean, 0 for info-only, 2 bad path); JSON shape; text format; `target/` skipped but explicit file scanned; `rules` command; `python -m bumm_guard`; 30 synthetic edge cases (exclusions, severities, braces in strings/comments, `cfg(test)`, trait impls, foreign `ext` calls, `// signer-ok`).

`bumm-guard scan tests/fixtures/near` prints 3 findings (BG-NEAR-001 d1_vuln:23, BG-NEAR-002 d2_vuln:33, BG-NEAR-003 d3_vuln:18), exit code 1. `pip install --no-build-isolation .` into a venv gives a working `bumm-guard` console script.

## Corpus run

Corpus: `scratchpad/corpus/` (290 `.rs` files: near-sdk-rs examples + standards, near/core-contracts, near-examples hello/donation/guest-book). All files scanned including `tests/` dirs (no extra excludes). Runtime about 1.4 s. 99 exported impls / 332 exported methods parsed, no crashes.

| Rule | Findings | By severity |
|---|---|---|
| BG-NEAR-001 | 5 | 2 high, 3 info |
| BG-NEAR-002 | 0 | |
| BG-NEAR-003 | 7 | 0 high, 7 low (REVIEW) |

Paths relative to `corpus/sdk/examples/`:

| # | Rule | Sev | Location | Judgement |
|---|---|---|---|---|
| 1 | 001 | info | adder/src/lib.rs:40 `add_callback_vec(&self, #[callback_vec])` | True positive by rule, no impact (pure function of its arguments, demo). |
| 2 | 001 | info | factory-contract-global/src/lib.rs:116 `get_result` | True positive by rule, low impact (read-only, returns a `get_status` promise). Scheduled via `Self::ext_self()`, which the detector resolves. |
| 3 | 001 | info | factory-contract/high-level/src/lib.rs:41 `get_result` | Same as 2. |
| 4 | 001 | high | factory-contract/low-level/src/lib.rs:60 `get_result(&mut self, ..)` | True positive by pattern (public callback), practical impact low: no state is mutated; it is high only because of `&mut self`. Severity is arguably over-rated. |
| 5 | 001 | high | mpc-contract/src/lib.rs:159 `sign_on_finish` | True positive, real: attacker-chosen `request_index` makes it `self.requests.remove(..)`. |
| 6 | 003 | low | factory-contract-global/src/lib.rs:122 | Not a defect: signer forwarded as an argument to `get_status` (identity, not authorisation). Review noise. |
| 7 | 003 | low | factory-contract/high-level/src/lib.rs:47 | Same as 6. |
| 8 | 003 | low | factory-contract/low-level/src/lib.rs:69 | Same as 6. |
| 9 | 003 | low | mission-control/src/mission_control.rs:20 `add_agent` | Review-worthy: registers the signer as an agent; a relaying contract would register the human signer. Demo code. |
| 10 | 003 | low | mpc-contract/src/lib.rs:67 | Review-worthy: stores signer as request owner (`predecessor` is usually the intended identity). |
| 11 | 003 | low | mpc-contract/src/lib.rs:127 | Same as 10. |
| 12 | 003 | low | status-message/src/lib.rs:23 `set_status` | Legitimate use (per-user key), the spec's own example; no patch. |

Result: 0 false positives among HIGH findings (2 high, both true positives by pattern; 1 of them real impact). 0 hits in `near-contract-standards`, `core-contracts` (old `ext_self::cb(..)` syntax, see limitations), hello/donation/guest-book. Same 001 set as the research prototype except `mpc-contract::do_something` (static `fn`, no receiver; excluded because the spec requires `&self`/`&mut self`). The sample is small and educational, so this is a sanity check, not an FP rate.

## Spec ambiguities and choices (fewer false positives)

- 001: methods without `&self`/`&mut self` receiver (e.g. a static `fn do_something(#[callback_unwrap] ..)`) are not reported (spec requires a receiver). `pub(crate)` is not treated as exported (near-sdk exports only plain `pub`).
- 001/002: only `Self::ext`, the contract's own type name, or an alias containing "self"/"callback" counts as scheduling a callback; `ext_ft::ext(..).ft_transfer()` to another contract never turns a same-named local method into a callback. Recognises `Self::ext_self()` as well.
- 001 exclusion for self-check also accepts a local alias (`let caller = env::predecessor_account_id(); ... caller == env::current_account_id()`).
- 002: finding is reported at the callback C (where the fix goes), the initiator S is named in the message. Mutation is searched only before the specific `.then(` leading to C. Base severity high; `#[callback_unwrap]`/`#[callback_vec]` medium; "pending marker" (no identifier of the mutated statement passed to C) info. Method names (`x.clone()`) are ignored when matching identifiers. A custom handler is any `self.`/`Self::` call whose name contains resolve|rollback|revert|refund|restore.
- 003: one finding per method. An alias bound by `let a = env::signer_account_id()` stays REVIEW (spec: identity/key use) even when `a` is later compared; REVIEW is severity `low` and message starts with `REVIEW:`. Logging-only uses are decided by the innermost enclosing macro/call (`log!`, `format!`, `log_str`, ...). `// signer-ok` is looked for in the comment/attribute lines directly above the method and inside it.
- Severity vocabulary: lower-case `critical|high|medium|low|info` to match the BUMM backend; exit code 1 on `high`/`critical`.

## Known limitations

- Regex/brace-level analysis; no type information and no name resolution. A local method named like a callback can only be matched by name within the crate.
- Macros are not expanded: methods generated by macros, `include!`, `macro_rules!` bodies are invisible. Items inside `#[cfg(test)]` are dropped, but `#[cfg(feature = ..)]` variants are all analysed.
- Callback scheduling is recognised only as `.then(<Self|OwnType|*self*|*callback*>::ext[_self](..)[.with_*(..)]*.cb(..))`. Missed: old `ext_self::cb(args, &acc, 0, gas)` syntax (this is why core-contracts give 0), raw `Promise::function_call("cb", ..)`/`env::promise_then` string names (strings are masked), `.and()` joins, callbacks stored in variables. For such callbacks 001 still fires through parameter attributes or promise-result reads in the body, 002 does not.
- 002 mutation detection is textual (`self.` paths, a fixed list of mutating methods); mutation via a local alias (`let b = &mut self.x; *b -= 1;`) or in a helper called from S is not seen; the callback's own correctness (a rollback that panics first, wrong amount) is not checked.
- 003 does not follow the signer through helper functions or struct fields; alias use is REVIEW only.
- Crate grouping uses the nearest `Cargo.toml`; without one, files scanned together under the same root are treated as one crate.
- The research prototype's tests/exploit scenarios (near-workspaces) are not part of this package; the fixtures only exercise static detection.
