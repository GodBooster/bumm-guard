//! `bumm-sim run --scenario <file.json> [--scenario ...] --out <report.json> [--wasm PATH] [--upgrade-wasm PATH]`
//!
//! One fresh near-sandbox per scenario file (near-workspaces 0.24). Accounts are created as
//! sub-accounts of the sandbox root with the requested balances, the wasm is deployed to the
//! sub-account `contract`, `init` is called by the contract account itself, then the steps run
//! in order. An `upgrade` step (FORMATS section 2a) is one batch transaction signed by the
//! contract account to itself: `DeployContract` plus, with `migrate`, a `FunctionCall`; the
//! sandbox state stays, so later steps run against the upgraded code. Every step is executed and recorded even after a failed expectation, so the
//! report shows the full trace. Exit: 0 all passed, 1 a scenario failed, 2 usage/parse error.

use anyhow::{Context, Result};
use bumm_sim::{
    build_report, format_near, gas_to_tgas, parse_args, parse_scenario, resolve_aliases, resolve_wasm, sha256_hex,
    shared_hash, Actual, Command, InitResult, Scenario, ScenarioResult, StepKind, StepResult, CONTRACT_ALIAS,
    CONTRACT_INITIAL_BALANCE, DEFAULT_GAS, RUNNER_VERSION, USAGE,
};
use near_workspaces::network::Sandbox;
use near_workspaces::operations::Function;
use near_workspaces::result::{ExecutionFinalResult, ViewResultDetails};
use near_workspaces::types::{Gas, NearToken};
use near_workspaces::{Account, Contract, Worker};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::Instant;

struct Loaded {
    file: PathBuf,
    sha256: String,
    scenario: Scenario,
}

#[tokio::main]
async fn main() {
    std::process::exit(real_main().await);
}

async fn real_main() -> i32 {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let (files, out, wasm_override, upgrade_override) = match parse_args(&argv) {
        Ok(Command::Help) => {
            println!("{USAGE}");
            return 0;
        }
        Ok(Command::Version) => {
            println!("bumm-sim {RUNNER_VERSION}");
            return 0;
        }
        Ok(Command::Run { scenarios, out, wasm, upgrade_wasm }) => (scenarios, out, wasm, upgrade_wasm),
        Err(e) => {
            eprintln!("bumm-sim: {e}\n{USAGE}");
            return 2;
        }
    };

    // Parse every file before starting any sandbox: a typo in file 3 should not cost two runs.
    let mut loaded = Vec::new();
    for file in files {
        let bytes = match std::fs::read(&file) {
            Ok(b) => b,
            Err(e) => {
                eprintln!("bumm-sim: cannot read {}: {e}", file.display());
                return 2;
            }
        };
        let text = match std::str::from_utf8(&bytes) {
            Ok(t) => t,
            Err(_) => {
                eprintln!("bumm-sim: {}: not UTF-8", file.display());
                return 2;
            }
        };
        match parse_scenario(text) {
            Ok(scenario) => loaded.push(Loaded { sha256: sha256_hex(&bytes), file, scenario }),
            Err(e) => {
                eprintln!("bumm-sim: {}: {e}", file.display());
                return 2;
            }
        }
    }

    let mut results = Vec::new();
    for l in &loaded {
        let r = run_scenario(l, wasm_override.as_deref(), upgrade_override.as_deref()).await;
        println!(
            "{} {} ({}/{} steps passed, {:.1}s){}",
            if r.passed { "PASS" } else { "FAIL" },
            r.name,
            r.steps.iter().filter(|s| s.passed).count(),
            r.steps.len(),
            r.duration_s,
            r.error.as_ref().map(|e| format!(": {e}")).unwrap_or_default()
        );
        results.push(r);
    }

    let report = build_report(results);
    let all_passed = report.summary.failed == 0;
    if let Err(e) = write_report(&out, &report) {
        eprintln!("bumm-sim: cannot write report {}: {e:#}", out.display());
        return 1;
    }
    println!(
        "{}/{} scenario(s) passed, {} step(s) failed; report: {}",
        report.summary.passed,
        report.summary.scenarios,
        report.summary.steps_failed,
        out.display()
    );
    if all_passed {
        0
    } else {
        1
    }
}

fn write_report(out: &Path, report: &bumm_sim::Report) -> Result<()> {
    if let Some(dir) = out.parent().filter(|d| !d.as_os_str().is_empty()) {
        std::fs::create_dir_all(dir)?;
    }
    std::fs::write(out, serde_json::to_string_pretty(report)? + "\n")?;
    Ok(())
}

/// Upgrade wasm bytes by step index, read before the sandbox starts.
type UpgradeCode = BTreeMap<usize, Vec<u8>>;

async fn run_scenario(l: &Loaded, wasm_override: Option<&Path>, upgrade_override: Option<&Path>) -> ScenarioResult {
    let start = Instant::now();
    let sc = &l.scenario;
    let wasm_path = resolve_wasm(&sc.wasm, &l.file, wasm_override);
    let wasm = std::fs::read(&wasm_path);
    // Every upgrade wasm is read up front: a missing file is an input error, not a step result.
    let mut upgrade_code = UpgradeCode::new();
    let mut upgrade_paths = Vec::new();
    let mut upgrade_error = None;
    for step in &sc.steps {
        if let StepKind::Upgrade { wasm: spec, .. } = &step.kind {
            let p = resolve_wasm(spec, &l.file, upgrade_override);
            match std::fs::read(&p) {
                Ok(b) => {
                    upgrade_code.insert(step.index, b);
                }
                Err(e) => {
                    upgrade_error.get_or_insert(format!("upgrade wasm not found or unreadable: {}: {e}", p.display()));
                }
            }
            upgrade_paths.push(p.display().to_string());
        }
    }
    let upgrade_hashes: Vec<String> = upgrade_code.values().map(|b| sha256_hex(b)).collect();
    let mut res = ScenarioResult {
        name: sc.name.clone(),
        file: l.file.display().to_string(),
        scenario_sha256: l.sha256.clone(),
        wasm: wasm_path.display().to_string(),
        wasm_sha256: wasm.as_ref().ok().map(|b| sha256_hex(b)),
        upgrade_wasm: shared_hash(upgrade_paths.iter().map(String::as_str)),
        upgrade_wasm_sha256: if upgrade_error.is_none() { shared_hash(upgrade_hashes.iter().map(String::as_str)) } else { None },
        passed: false,
        duration_s: 0.0,
        error: None,
        accounts: BTreeMap::new(),
        init: None,
        steps: Vec::new(),
    };
    match (wasm, upgrade_error) {
        (Err(e), _) => res.error = Some(format!("wasm not found or unreadable: {}: {e}", wasm_path.display())),
        (Ok(_), Some(e)) => res.error = Some(e),
        (Ok(bytes), None) => {
            if let Err(e) = execute(sc, &bytes, &upgrade_code, &mut res).await {
                res.error = Some(format!("{e:#}"));
            }
        }
    }
    let aliases = res.accounts.clone();
    res.finish(sc, &aliases, start.elapsed().as_secs_f64());
    res
}

async fn execute(sc: &Scenario, wasm: &[u8], upgrade_code: &UpgradeCode, res: &mut ScenarioResult) -> Result<()> {
    let worker: Worker<Sandbox> = near_workspaces::sandbox().await.context("start near-sandbox")?;
    let root = worker.root_account().context("sandbox root account")?;

    let mut accounts: BTreeMap<String, Account> = BTreeMap::new();
    for (alias, balance) in &sc.accounts {
        let acc = root
            .create_subaccount(alias)
            .initial_balance(NearToken::from_yoctonear(*balance))
            .transact()
            .await
            .with_context(|| format!("create account {alias}"))?
            .into_result()
            .map_err(|f| anyhow::anyhow!("create account {alias}: {f}"))?;
        res.accounts.insert(alias.clone(), acc.id().to_string());
        accounts.insert(alias.clone(), acc);
    }
    let contract_account = root
        .create_subaccount(CONTRACT_ALIAS)
        .initial_balance(NearToken::from_yoctonear(CONTRACT_INITIAL_BALANCE))
        .transact()
        .await
        .context("create contract account")?
        .into_result()
        .map_err(|f| anyhow::anyhow!("create contract account: {f}"))?;
    let contract: Contract = contract_account
        .deploy(wasm)
        .await
        .context("deploy wasm")?
        .into_result()
        .map_err(|f| anyhow::anyhow!("deploy wasm: {f}"))?;
    res.accounts.insert(CONTRACT_ALIAS.to_string(), contract.id().to_string());
    let aliases = res.accounts.clone();

    if let Some(init) = &sc.init {
        let outcome = contract
            .call(&init.method)
            .args_json(resolve_aliases(&init.args, &aliases))
            .gas(Gas::from_gas(DEFAULT_GAS))
            .transact()
            .await;
        let actual = call_actual(outcome);
        let passed = actual.is_success();
        res.init = Some(InitResult { method: init.method.clone(), passed, actual });
        if !passed {
            return Ok(()); // steps are recorded as skipped by ScenarioResult::finish
        }
    }

    for step in &sc.steps {
        let actual = match &step.kind {
            StepKind::Call { method, by, args, deposit, gas } => {
                let signer: &Account = if by == CONTRACT_ALIAS { contract.as_account() } else { &accounts[by] };
                let outcome = signer
                    .call(contract.id(), method)
                    .args_json(resolve_aliases(args, &aliases))
                    .deposit(NearToken::from_yoctonear(*deposit))
                    .gas(Gas::from_gas(*gas))
                    .transact()
                    .await;
                call_actual(outcome)
            }
            StepKind::View { method, args } => {
                let outcome = contract.view(method).args_json(resolve_aliases(args, &aliases)).await;
                view_actual(outcome)
            }
            StepKind::Balance { account } => {
                let id: near_workspaces::AccountId = aliases[account].parse().context("account id")?;
                match worker.view_account(&id).await {
                    Ok(details) => {
                        let yocto = details.balance.as_yoctonear();
                        Actual { near: Some(format_near(yocto)), ..Actual::success(Some(yocto.to_string().into())) }
                    }
                    Err(e) => Actual::failure(e.to_string()),
                }
            }
            StepKind::Upgrade { migrate, .. } => {
                let code = &upgrade_code[&step.index];
                // One batch: if the migration call fails, the deploy is rolled back with it.
                let mut tx = contract.batch().deploy(code);
                if let Some(m) = migrate {
                    tx = tx.call(
                        Function::new(&m.method)
                            .args_json(resolve_aliases(&m.args, &aliases))
                            .gas(Gas::from_gas(m.gas)),
                    );
                }
                Actual { code_sha256: Some(sha256_hex(code)), ..call_actual(tx.transact().await) }
            }
        };
        res.steps.push(StepResult::new(step, actual, &aliases));
    }
    Ok(())
}

/// Status, error text, return value, gas and logs of a change call.
fn call_actual(outcome: near_workspaces::Result<ExecutionFinalResult>) -> Actual {
    let r = match outcome {
        Ok(r) => r,
        // RPC-level refusal (e.g. invalid transaction): the chain did not accept the call.
        Err(e) => {
            return Actual { error_debug: Some(format!("{e:?}")), ..Actual::failure(e.to_string()) };
        }
    };
    let gas = gas_to_tgas(r.total_gas_burnt.as_gas());
    let logs: Vec<String> = r.logs().into_iter().map(str::to_owned).collect();
    let mut actual = match r.into_result() {
        Ok(success) => match success.raw_bytes() {
            Ok(bytes) => Actual::from_return_bytes(&bytes),
            Err(e) => Actual::failure(format!("cannot decode return value: {e}")),
        },
        Err(failure) => Actual { error_debug: Some(format!("{failure:?}")), ..Actual::failure(failure.to_string()) },
    };
    actual.gas_burnt_tgas = Some(gas);
    actual.logs = logs;
    actual
}

fn view_actual(outcome: near_workspaces::Result<ViewResultDetails>) -> Actual {
    match outcome {
        Ok(v) => {
            let mut a = Actual::from_return_bytes(&v.result);
            a.logs = v.logs;
            a
        }
        // The Display text is only the RPC wrapper ("unable to fulfill the query request"); the
        // contract's own panic text sits in the source chain / debug form (FORMATS section 3,
        // `error_detail`).
        Err(e) => {
            let mut sources = Vec::new();
            let mut src = std::error::Error::source(&e);
            while let Some(s) = src {
                sources.push(s.to_string());
                src = s.source();
            }
            Actual::view_failure(e.to_string(), format!("{e:?}"), sources)
        }
    }
}
