# End-to-end on NEAR testnet (2026-11-02)

One path, from a contract that compiles to a contract people can use, with every step
bound to the same wasm by its sha256.

| Step | What | Evidence |
|---|---|---|
| 1 | Build `event_registration` (N06) in CI with rustc 1.85.0 | CI artifact `event_registration-wasm`, 180 626 bytes, sha256 `c3f50addfb067db8b9fcfc4b8dc6d1a6a197e66d392648bda372f7a7b3a3f617` |
| 2 | `bumm-guard simulate scenarios/n06/*.json` in near-sandbox (CI) | 3/3 scenarios, 24/24 steps; refusals observed with the contract's own panic text. Report: `simulate-report-ci-41428851380-series.json` (same wasm sha256) |
| 3 | `bumm-guard gate … --simulate-report … --estimate-storage --account bumm-guard-demo.testnet` | `simulate report ok: 3 scenario(s) passed for this wasm`; storage lock ≈1.8063 NEAR (`storage_amount_per_byte` 1e19 from `EXPERIMENTAL_protocol_config`, testnet); account had 10 NEAR. Gate printed the deploy command and did not run it |
| 4 | Owner deployed with their own key (`sign-with-keychain`), init `new({"capacity": 2})` in the same transaction | tx [`A4FxLHmLW7aPBYnGgzn2fV5oaHYhydkp3QwaE6y61KP8`](https://explorer.testnet.near.org/transactions/A4FxLHmLW7aPBYnGgzn2fV5oaHYhydkp3QwaE6y61KP8), 1.8 Tgas, fee 0.000172 NEAR |
| 5 | `bumm-guard gate --verify-deployed bumm-guard-demo.testnet --wasm … --smoke …` | on-chain code sha256 = artifact sha256 (**MATCH**); smoke views: `get_capacity` 2, `get_registered_count` 0, `get_seats_left` 2, `is_registered(bumm-guard-demo.testnet)` false; init not re-called. Report: `testnet-gate-report-2026-11-02.json` |
| 6 | ABI panel (`ui/`) pointed at `bumm-guard-demo.testnet`, N06 ABI | view calls from the page returned `get_capacity` 2, `get_registered_count` 0, `get_registered` [], `get_seats_left` 2 |

Not covered yet: a change call (`register`) signed through the panel's wallet path;
reproducible-wasm builds (the CI build and a local build differ in bytes, so the gate
only accepts evidence for the exact file that was simulated — which is why step 4
deployed the CI artifact).

Reproduce steps 5–6 without any account: `bumm-guard gate tests/fixtures/near/event_registration --verify-deployed bumm-guard-demo.testnet --wasm <CI artifact> --network testnet --smoke get_capacity`, and open `ui/index.html` with contract `bumm-guard-demo.testnet`.
