use anyhow::{ensure, Context, Result};
use near_workspaces::types::NearToken;
use serde_json::json;
use std::{path::PathBuf, process::Command};

// Build the actual detector fixtures, not copies with potentially different behavior.
fn build_fixtures() -> Result<PathBuf> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let target = root.join("contracts/target/fixtures");
    // Run cargo from the fixtures directory with the harness's RUSTUP_TOOLCHAIN unset, so
    // rustup picks tests/fixtures/near/rust-toolchain.toml (1.85.0): near-sdk 5.17.2 wasm
    // host imports do not link on rustc 1.99 (undefined symbol: promise_batch_then ...).
    let status = Command::new("cargo")
        .current_dir(root.join("tests/fixtures/near"))
        .env_remove("RUSTUP_TOOLCHAIN")
        .args([
            "build",
            "--locked",
            "--release",
            "--target",
            "wasm32-unknown-unknown",
            "--manifest-path",
            "Cargo.toml",
            "-p",
            "d1_vuln",
            "-p",
            "d1_safe",
            "-p",
            "proxy",
            "--target-dir",
        ])
        .arg(&target)
        .status()
        .context("build detector fixtures")?;
    ensure!(status.success(), "fixture WASM build failed: {status}");
    Ok(target.join("wasm32-unknown-unknown/release"))
}

#[tokio::test]
async fn bg_near_001_spoof_and_legitimate_payment() -> Result<()> {
    let wasm = build_fixtures()?;
    let worker = near_workspaces::sandbox().await?;
    let buyer = worker.dev_create_account().await?;
    let seller = worker.dev_create_account().await?;
    let mallory = worker.dev_create_account().await?;
    let proxy = worker
        .dev_deploy(&std::fs::read(wasm.join("proxy.wasm"))?)
        .await?;

    for (fixture, protected) in [("d1_vuln", false), ("d1_safe", true)] {
        let bytes = std::fs::read(wasm.join(format!("{fixture}.wasm")))?;
        let escrow = worker.dev_deploy(&bytes).await?;
        escrow
            .call("new")
            .args_json(json!({"buyer": buyer.id(), "seller": seller.id()}))
            .transact()
            .await?
            .into_result()?;
        assert!(!escrow.view("is_paid").await?.json::<bool>()?);
        let balance_before = seller.view_account().await?.balance;
        // e30= is base64("{}"). A successful proxy promise supplies the callback result.
        let attack = mallory
            .call(proxy.id(), "spoof_callback")
            .args_json(json!({"target": escrow.id(), "method": "on_paid", "args": "e30="}))
            .max_gas()
            .transact()
            .await?;
        if protected {
            assert!(
                attack.is_failure(),
                "{fixture}: spoof unexpectedly succeeded"
            );
            let error = format!("{:?}", attack.into_result().unwrap_err());
            assert!(
                error.contains("Method on_paid is private"),
                "{fixture}: {error}"
            );
        } else {
            attack
                .into_result()
                .context("vulnerable spoof must succeed")?;
        }
        assert_eq!(
            escrow.view("is_paid").await?.json::<bool>()?,
            !protected,
            "{fixture}"
        );
        assert_eq!(
            seller.view_account().await?.balance,
            balance_before,
            "spoof must not pay the seller"
        );

        // Fresh state prevents the vulnerable exploit from masking a broken pay callback.
        let legitimate = worker.dev_deploy(&bytes).await?;
        legitimate
            .call("new")
            .args_json(json!({"buyer": buyer.id(), "seller": seller.id()}))
            .transact()
            .await?
            .into_result()?;
        assert!(!legitimate.view("is_paid").await?.json::<bool>()?);
        let before = seller.view_account().await?.balance;
        let payment = NearToken::from_near(1);
        buyer
            .call(legitimate.id(), "pay")
            .deposit(payment)
            .max_gas()
            .transact()
            .await?
            .into_result()
            .context("legitimate payment")?;
        assert!(
            legitimate.view("is_paid").await?.json::<bool>()?,
            "{fixture}: pay callback"
        );
        assert_eq!(
            seller.view_account().await?.balance.as_yoctonear(),
            before.as_yoctonear() + payment.as_yoctonear(),
            "{fixture}: seller payment"
        );
    }
    Ok(())
}
