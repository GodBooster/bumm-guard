//! Real near-sandbox run of the FORMATS section 2a acceptance scenarios (Linux only, like the
//! `sandbox` crate: near-sandbox has no macOS x86_64 binary). Runs the `bumm-sim` binary of
//! this crate on `scenarios/upgrade/*.json` and checks the report:
//! - with `migrate()`: the scenario passes, so registrations survive the upgrade;
//! - code only: the upgrade transaction itself succeeds, every step after it fails
//!   (the new code cannot read the old state).

use anyhow::{ensure, Context, Result};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{path::PathBuf, process::Command};

const RELEASE: &str = "tests/fixtures/near/target/wasm32-unknown-unknown/release";
const UPGRADE_INDEX: usize = 3;

fn sha256_file(p: &PathBuf) -> Result<String> {
    let bytes = std::fs::read(p).with_context(|| format!("read {}", p.display()))?;
    Ok(Sha256::digest(bytes).iter().map(|b| format!("{b:02x}")).collect())
}

/// Build the old (N06) and new (N10 waitlist) contracts where the scenario files expect them.
fn build_fixtures(root: &PathBuf) -> Result<()> {
    // RUSTUP_TOOLCHAIN unset so rustup picks tests/fixtures/near/rust-toolchain.toml (1.85.0).
    let status = Command::new("cargo")
        .current_dir(root.join("tests/fixtures/near"))
        .env_remove("RUSTUP_TOOLCHAIN")
        .args(["build", "--locked", "--release", "--target", "wasm32-unknown-unknown"])
        .args(["-p", "event_registration", "-p", "event_registration_waitlist"])
        .status()
        .context("build event_registration fixtures")?;
    ensure!(status.success(), "fixture WASM build failed: {status}");
    Ok(())
}

#[test]
fn upgrade_scenarios_on_near_sandbox() -> Result<()> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize()?;
    build_fixtures(&root)?;
    let old_sha = sha256_file(&root.join(RELEASE).join("event_registration.wasm"))?;
    let new_sha = sha256_file(&root.join(RELEASE).join("event_registration_waitlist.wasm"))?;

    let out = root.join("contracts/target/upgrade-sandbox/simulate-report.json");
    let _ = std::fs::remove_file(&out);
    let status = Command::new(env!("CARGO_BIN_EXE_bumm-sim"))
        .current_dir(&root)
        .args(["run", "--scenario", "scenarios/upgrade/n06_to_n10_with_migrate.json"])
        .args(["--scenario", "scenarios/upgrade/n06_to_n10_code_only.json", "--out"])
        .arg(&out)
        .status()
        .context("run bumm-sim")?;
    let report: Value = serde_json::from_str(&std::fs::read_to_string(&out).context("read report")?)?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    // Written straight to stderr (not captured by libtest), so a passing CI run still shows
    // what the chain returned for every step.
    let mut trace = String::new();
    for sc in report["scenarios"].as_array().into_iter().flatten() {
        trace += &format!("{} passed={}\n", sc["name"], sc["passed"]);
        for s in sc["steps"].as_array().into_iter().flatten() {
            let a = &s["actual"];
            let err: String = a["error"].as_str().unwrap_or("").chars().take(160).collect();
            trace += &format!(
                "  {:>2} {:<7} {:<22} passed={:<5} {} gas={} value={} {err}\n",
                s["index"], s["kind"].as_str().unwrap_or(""), s["method"].as_str().unwrap_or(""),
                s["passed"], a["status"].as_str().unwrap_or(""), a["gas_burnt_tgas"], a["value"]
            );
        }
    }
    std::io::Write::write_all(&mut std::io::stderr(), trace.as_bytes())?;
    assert_eq!(status.code(), Some(1), "code-only scenario must fail, so bumm-sim exits 1");

    assert_eq!(report["wasm_sha256"], old_sha);
    assert_eq!(report["upgrade_wasm_sha256"], new_sha);
    assert_eq!(report["summary"]["passed"], 1);
    assert_eq!(report["summary"]["failed"], 1);
    let scenarios = report["scenarios"].as_array().context("scenarios")?;

    let with = &scenarios[0];
    assert!(with["name"].as_str().unwrap().contains("with migrate"), "{}", with["name"]);
    assert_eq!(with["passed"], true, "with migrate: {with:#}");
    assert_eq!(with["upgrade_wasm_sha256"], new_sha);
    let up = &with["steps"][UPGRADE_INDEX];
    assert_eq!((up["kind"].as_str(), up["method"].as_str()), (Some("upgrade"), Some("migrate")));
    assert_eq!(up["actual"]["status"], "success");
    assert_eq!(up["actual"]["code_sha256"], new_sha);

    let code_only = &scenarios[1];
    assert!(code_only["name"].as_str().unwrap().contains("code-only"), "{}", code_only["name"]);
    assert_eq!(code_only["passed"], false);
    let steps = code_only["steps"].as_array().context("steps")?;
    assert_eq!(steps[UPGRADE_INDEX]["kind"], "upgrade");
    assert_eq!(steps[UPGRADE_INDEX]["actual"]["code_sha256"], new_sha);
    for s in &steps[..=UPGRADE_INDEX] {
        assert_eq!(s["passed"], true, "code only, up to and including the upgrade: {s:#}");
    }
    for s in &steps[UPGRADE_INDEX + 1..] {
        assert_eq!(s["passed"], false, "code only, after the upgrade: {s:#}");
        assert_eq!(s["actual"]["status"], "failure", "{s:#}");
    }
    Ok(())
}
