//! Sandbox-free part of `bumm-sim`: scenario parsing (docs/FORMATS.md section 2), amount
//! parsing, expectation evaluation, hashing and the report model (section 3).
//!
//! Everything here is pure so it can be unit-tested without starting near-sandbox
//! (`cargo test -p bumm-sim --lib`). `main.rs` only adds the near-workspaces calls.

use serde::Serialize;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

pub const TOOL: &str = "bumm-guard";
pub const RUNNER_VERSION: &str = env!("CARGO_PKG_VERSION");
/// Pinned in contracts/sim-runner/Cargo.toml (`=0.24.0`) and contracts/Cargo.lock.
pub const NEAR_WORKSPACES_VERSION: &str = "0.24.0";
/// near-sandbox crate version resolved in contracts/Cargo.lock for near-workspaces 0.24.0.
pub const NEAR_SANDBOX_VERSION: &str = "0.4.0";

pub const YOCTO_PER_NEAR: u128 = 1_000_000_000_000_000_000_000_000;
pub const GAS_PER_TGAS: u64 = 1_000_000_000_000;
pub const DEFAULT_GAS: u64 = 30 * GAS_PER_TGAS;
/// Default gas of the `migrate` call in an upgrade step (FORMATS section 2a).
pub const DEFAULT_MIGRATE_GAS: u64 = 100 * GAS_PER_TGAS;
/// The contract is deployed to the sub-account `contract.<root>`; scenarios refer to it as
/// `"contract"` (caller for self-calls, `balance` target, alias in args/expectations).
pub const CONTRACT_ALIAS: &str = "contract";
/// Initial balance of the contract account. Covers storage staking for a ~200 KB wasm
/// (about 2 NEAR at 10^19 yocto/byte) with a wide margin. Not part of the scenario format.
pub const CONTRACT_INITIAL_BALANCE: u128 = 50 * YOCTO_PER_NEAR;
/// Sub-account that repeats the migration call after an upgrade step (the access probe). Longer
/// than an alias may be (32 characters), so it cannot collide with a scenario account.
pub const PROBE_ACCOUNT: &str = "bumm-sim-access-probe-not-an-alias";
pub const PROBE_INITIAL_BALANCE: u128 = 5 * YOCTO_PER_NEAR;

// ---------------------------------------------------------------------------------------
// Amounts
// ---------------------------------------------------------------------------------------

/// Parse a decimal string with at most `scale` fractional digits into an integer scaled by
/// `10^scale`. Exact (no floating point).
fn parse_scaled(num: &str, scale: u32, what: &str) -> Result<u128, String> {
    let (int_part, frac_part) = match num.split_once('.') {
        Some((i, f)) => (i, f),
        None => (num, ""),
    };
    let digits_ok = |s: &str| s.chars().all(|c| c.is_ascii_digit());
    if (int_part.is_empty() && frac_part.is_empty()) || !digits_ok(int_part) || !digits_ok(frac_part) {
        return Err(format!("bad {what} {num:?}: expected a non-negative decimal number"));
    }
    if frac_part.len() as u32 > scale {
        return Err(format!("bad {what} {num:?}: more than {scale} decimal places"));
    }
    let overflow = || format!("bad {what} {num:?}: value too large");
    let int_v: u128 = if int_part.is_empty() { 0 } else { int_part.parse().map_err(|_| overflow())? };
    let mut frac_v: u128 = if frac_part.is_empty() { 0 } else { frac_part.parse().map_err(|_| overflow())? };
    frac_v = frac_v
        .checked_mul(10u128.pow(scale - frac_part.len() as u32))
        .ok_or_else(overflow)?;
    int_v
        .checked_mul(10u128.pow(scale))
        .and_then(|v| v.checked_add(frac_v))
        .ok_or_else(overflow)
}

/// NEAR amount: a yoctoNEAR integer string (`"1000"`) or `"<decimal> NEAR"` (`"1.5 NEAR"`).
pub fn parse_near_amount(s: &str) -> Result<u128, String> {
    let t = s.trim();
    if let Some(num) = strip_suffix_ci(t, "NEAR") {
        return parse_scaled(num.trim_end(), 24, "NEAR amount");
    }
    if !t.is_empty() && t.chars().all(|c| c.is_ascii_digit()) {
        return t
            .parse::<u128>()
            .map_err(|_| format!("bad NEAR amount {s:?}: value too large"));
    }
    Err(format!(
        "bad NEAR amount {s:?}: expected a yoctoNEAR integer string like \"1000\" or \"<decimal> NEAR\" like \"1.5 NEAR\""
    ))
}

/// Gas: a raw gas integer string or `"<decimal> Tgas"` (`"30 Tgas"`).
pub fn parse_gas(s: &str) -> Result<u64, String> {
    let t = s.trim();
    let v: u128 = if let Some(num) = strip_suffix_ci(t, "Tgas") {
        parse_scaled(num.trim_end(), 12, "gas")?
    } else if !t.is_empty() && t.chars().all(|c| c.is_ascii_digit()) {
        t.parse::<u128>().map_err(|_| format!("bad gas {s:?}: value too large"))?
    } else {
        return Err(format!("bad gas {s:?}: expected a gas integer string or \"<n> Tgas\" like \"30 Tgas\""));
    };
    u64::try_from(v).map_err(|_| format!("bad gas {s:?}: value too large"))
}

fn strip_suffix_ci<'a>(s: &'a str, suffix: &str) -> Option<&'a str> {
    if s.len() >= suffix.len() && s.is_char_boundary(s.len() - suffix.len()) {
        let (head, tail) = s.split_at(s.len() - suffix.len());
        if tail.eq_ignore_ascii_case(suffix) {
            return Some(head);
        }
    }
    None
}

/// `1_500_000_000_000_000_000_000_000` -> `"1.5"`; for human-readable report fields only.
pub fn format_near(yocto: u128) -> String {
    let int = yocto / YOCTO_PER_NEAR;
    let frac = yocto % YOCTO_PER_NEAR;
    if frac == 0 {
        return int.to_string();
    }
    let f = format!("{frac:024}");
    format!("{int}.{}", f.trim_end_matches('0'))
}

/// `100_000_000_000_000` -> `"100 Tgas"`, `2_500_000_000_000` -> `"2.5 Tgas"` (exact; what
/// near-cli-rs `prepaid-gas` accepts).
pub fn format_tgas(gas: u64) -> String {
    let (int, frac) = (gas / GAS_PER_TGAS, gas % GAS_PER_TGAS);
    if frac == 0 {
        return format!("{int} Tgas");
    }
    format!("{int}.{} Tgas", format!("{frac:012}").trim_end_matches('0'))
}

pub fn gas_to_tgas(gas: u64) -> f64 {
    ((gas as f64 / GAS_PER_TGAS as f64) * 1000.0).round() / 1000.0
}

// ---------------------------------------------------------------------------------------
// Scenario model and parser
// ---------------------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq)]
pub struct Scenario {
    pub name: String,
    /// alias -> initial balance in yoctoNEAR (BTreeMap: deterministic creation order)
    pub accounts: BTreeMap<String, u128>,
    pub wasm: String,
    pub init: Option<Init>,
    pub steps: Vec<Step>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Init {
    pub method: String,
    pub args: Value,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Step {
    pub index: usize,
    pub kind: StepKind,
    pub expect: Expect,
    /// The `expect` value exactly as written (echoed into the report as `expected`).
    pub expect_raw: Value,
}

#[derive(Debug, Clone, PartialEq)]
pub enum StepKind {
    Call { method: String, by: String, args: Value, deposit: u128, gas: u64 },
    View { method: String, args: Value },
    Balance { account: String },
    /// FORMATS section 2a: deploy `wasm` to the contract account (signed by itself), plus an
    /// optional migration call in the same batch transaction.
    Upgrade { wasm: String, migrate: Option<Migrate> },
}

#[derive(Debug, Clone, PartialEq)]
pub struct Migrate {
    pub method: String,
    pub args: Value,
    pub gas: u64,
}

impl StepKind {
    pub fn kind_name(&self) -> &'static str {
        match self {
            StepKind::Call { .. } => "call",
            StepKind::View { .. } => "view",
            StepKind::Balance { .. } => "balance",
            StepKind::Upgrade { .. } => "upgrade",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Expect {
    Ok,
    FailContains(String),
    Equals(Value),
    Contains(Value),
    AtLeast(u128),
    AtMost(u128),
}

const TOP_KEYS: &[&str] = &["name", "description", "accounts", "contract", "steps"];
const CONTRACT_KEYS: &[&str] = &["wasm", "init"];
const INIT_KEYS: &[&str] = &["method", "args"];
const CALL_KEYS: &[&str] = &["call", "by", "args", "deposit", "gas", "expect"];
const VIEW_KEYS: &[&str] = &["view", "args", "expect"];
const BALANCE_KEYS: &[&str] = &["balance", "expect"];
const UPGRADE_KEYS: &[&str] = &["upgrade", "migrate", "expect"];
const MIGRATE_KEYS: &[&str] = &["method", "args", "gas"];
const STEP_KINDS: &[&str] = &["call", "view", "balance", "upgrade"];

fn check_keys(obj: &Map<String, Value>, allowed: &[&str], ctx: &str) -> Result<(), String> {
    for k in obj.keys() {
        if !allowed.contains(&k.as_str()) {
            return Err(format!("{ctx}: unknown field {k:?} (allowed: {})", allowed.join(", ")));
        }
    }
    Ok(())
}

fn req_str<'a>(obj: &'a Map<String, Value>, key: &str, ctx: &str) -> Result<&'a str, String> {
    match obj.get(key) {
        Some(Value::String(s)) if !s.is_empty() => Ok(s),
        Some(_) => Err(format!("{ctx}: {key:?} must be a non-empty string")),
        None => Err(format!("{ctx}: missing {key:?}")),
    }
}

fn args_of(obj: &Map<String, Value>, ctx: &str) -> Result<Value, String> {
    match obj.get("args") {
        None | Some(Value::Null) => Ok(Value::Object(Map::new())),
        Some(v @ Value::Object(_)) => Ok(v.clone()),
        Some(_) => Err(format!("{ctx}: \"args\" must be a JSON object")),
    }
}

/// Sub-account prefix: what `create_subaccount` accepts as one id part.
pub fn valid_alias(a: &str) -> bool {
    let b = a.as_bytes();
    !b.is_empty()
        && b.len() <= 32
        && b.iter().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'-' || *c == b'_')
        && b[0].is_ascii_alphanumeric()
        && b[b.len() - 1].is_ascii_alphanumeric()
        && !a.contains("--")
        && !a.contains("__")
        && !a.contains("-_")
        && !a.contains("_-")
}

fn gas_of(obj: &Map<String, Value>, default: u64, ctx: &str) -> Result<u64, String> {
    match obj.get("gas") {
        None => Ok(default),
        Some(Value::String(g)) => parse_gas(g).map_err(|e| format!("{ctx}: gas: {e}")),
        Some(_) => Err(format!("{ctx}: gas must be a string like \"30 Tgas\"")),
    }
}

fn amount_value(v: &Value, ctx: &str) -> Result<u128, String> {
    match v {
        Value::String(s) => parse_near_amount(s).map_err(|e| format!("{ctx}: {e}")),
        Value::Number(n) => n
            .as_u64()
            .map(u128::from)
            .ok_or_else(|| format!("{ctx}: bad amount {n}: expected a non-negative integer or amount string")),
        _ => Err(format!("{ctx}: amount must be a string like \"9 NEAR\" or a yoctoNEAR integer string")),
    }
}

pub fn parse_expect(v: Option<&Value>, ctx: &str) -> Result<(Expect, Value), String> {
    let v = match v {
        None => return Ok((Expect::Ok, Value::String("ok".into()))),
        Some(v) => v,
    };
    let e = match v {
        Value::String(s) if s == "ok" => Expect::Ok,
        Value::Object(o) if o.len() == 1 => {
            let (k, val) = o.iter().next().expect("len 1");
            match k.as_str() {
                "fail_contains" => match val {
                    Value::String(s) if !s.is_empty() => Expect::FailContains(s.clone()),
                    _ => return Err(format!("{ctx}: \"fail_contains\" must be a non-empty string")),
                },
                "equals" => Expect::Equals(val.clone()),
                "contains" => Expect::Contains(val.clone()),
                "at_least" => Expect::AtLeast(amount_value(val, ctx)?),
                "at_most" => Expect::AtMost(amount_value(val, ctx)?),
                other => {
                    return Err(format!(
                        "{ctx}: unknown expectation {other:?} (allowed: \"ok\", fail_contains, equals, contains, at_least, at_most)"
                    ))
                }
            }
        }
        _ => {
            return Err(format!(
                "{ctx}: expect must be \"ok\" or an object with exactly one of fail_contains, equals, contains, at_least, at_most"
            ))
        }
    };
    Ok((e, v.clone()))
}

/// Parse and validate one scenario file (FORMATS section 2). Errors name the step index
/// (0-based, as in the report) and the offending field.
pub fn parse_scenario(text: &str) -> Result<Scenario, String> {
    let root: Value = serde_json::from_str(text).map_err(|e| format!("invalid JSON: {e}"))?;
    let obj = root.as_object().ok_or("scenario must be a JSON object")?;
    check_keys(obj, TOP_KEYS, "scenario")?;
    let name = req_str(obj, "name", "scenario")?.to_string();

    let mut accounts = BTreeMap::new();
    match obj.get("accounts") {
        Some(Value::Object(a)) => {
            for (alias, bal) in a {
                if alias == CONTRACT_ALIAS {
                    return Err(format!("accounts: {CONTRACT_ALIAS:?} is reserved for the contract account"));
                }
                if !valid_alias(alias) {
                    return Err(format!(
                        "accounts: bad account name {alias:?}: use 1-32 chars of a-z, 0-9, '-', '_' (no dots)"
                    ));
                }
                let bal = match bal {
                    Value::String(s) => parse_near_amount(s).map_err(|e| format!("accounts.{alias}: {e}"))?,
                    _ => return Err(format!("accounts.{alias}: balance must be a string like \"10 NEAR\"")),
                };
                accounts.insert(alias.clone(), bal);
            }
        }
        Some(_) => return Err("accounts must be an object {name: balance}".into()),
        None => return Err("scenario: missing \"accounts\"".into()),
    }

    let contract = match obj.get("contract") {
        Some(Value::Object(c)) => c,
        Some(_) => return Err("contract must be an object".into()),
        None => return Err("scenario: missing \"contract\"".into()),
    };
    check_keys(contract, CONTRACT_KEYS, "contract")?;
    let wasm = req_str(contract, "wasm", "contract")?.to_string();
    let init = match contract.get("init") {
        None | Some(Value::Null) => None,
        Some(Value::Object(i)) => {
            check_keys(i, INIT_KEYS, "contract.init")?;
            Some(Init {
                method: req_str(i, "method", "contract.init")?.to_string(),
                args: args_of(i, "contract.init")?,
            })
        }
        Some(_) => return Err("contract.init must be an object {method, args}".into()),
    };

    let known = |a: &str| a == CONTRACT_ALIAS || accounts.contains_key(a);
    let known_list = || {
        let mut v: Vec<&str> = accounts.keys().map(String::as_str).collect();
        v.push(CONTRACT_ALIAS);
        v.join(", ")
    };

    let raw_steps = match obj.get("steps") {
        Some(Value::Array(s)) => s,
        Some(_) => return Err("steps must be an array".into()),
        None => return Err("scenario: missing \"steps\"".into()),
    };
    let mut steps = Vec::with_capacity(raw_steps.len());
    for (index, raw) in raw_steps.iter().enumerate() {
        let ctx = format!("step {index}");
        let s = raw.as_object().ok_or_else(|| format!("{ctx}: must be a JSON object"))?;
        let kinds: Vec<&str> = STEP_KINDS.iter().copied().filter(|k| s.contains_key(*k)).collect();
        let kind = match kinds.as_slice() {
            [k] => *k,
            [] => {
                let keys: Vec<&str> = s.keys().map(String::as_str).collect();
                return Err(format!(
                    "{ctx}: unknown step kind (expected exactly one of call, view, balance, upgrade; got keys: {})",
                    keys.join(", ")
                ));
            }
            _ => return Err(format!("{ctx}: more than one step kind ({})", kinds.join(", "))),
        };
        let (expect, expect_raw) = parse_expect(s.get("expect"), &ctx)?;
        let kind = match kind {
            "call" => {
                check_keys(s, CALL_KEYS, &ctx)?;
                let method = req_str(s, "call", &ctx)?.to_string();
                let by = req_str(s, "by", &ctx)?.to_string();
                if !known(&by) {
                    return Err(format!("{ctx}: unknown account {by:?} in \"by\" (known: {})", known_list()));
                }
                let deposit = match s.get("deposit") {
                    None => 0,
                    Some(Value::String(d)) => parse_near_amount(d).map_err(|e| format!("{ctx}: deposit: {e}"))?,
                    Some(_) => return Err(format!("{ctx}: deposit must be a string like \"0\" or \"1 NEAR\"")),
                };
                let gas = gas_of(s, DEFAULT_GAS, &ctx)?;
                StepKind::Call { method, by, args: args_of(s, &ctx)?, deposit, gas }
            }
            "upgrade" => {
                check_keys(s, UPGRADE_KEYS, &ctx)?;
                let wasm = req_str(s, "upgrade", &ctx)?.to_string();
                let migrate = match s.get("migrate") {
                    None | Some(Value::Null) => None,
                    Some(Value::Object(m)) => {
                        let mctx = format!("{ctx}.migrate");
                        check_keys(m, MIGRATE_KEYS, &mctx)?;
                        Some(Migrate {
                            method: req_str(m, "method", &mctx)?.to_string(),
                            args: args_of(m, &mctx)?,
                            gas: gas_of(m, DEFAULT_MIGRATE_GAS, &mctx)?,
                        })
                    }
                    Some(_) => return Err(format!("{ctx}: migrate must be an object {{method, args, gas}}")),
                };
                StepKind::Upgrade { wasm, migrate }
            }
            "view" => {
                check_keys(s, VIEW_KEYS, &ctx)?;
                StepKind::View { method: req_str(s, "view", &ctx)?.to_string(), args: args_of(s, &ctx)? }
            }
            _ => {
                check_keys(s, BALANCE_KEYS, &ctx)?;
                let account = req_str(s, "balance", &ctx)?.to_string();
                if !known(&account) {
                    return Err(format!("{ctx}: unknown account {account:?} in \"balance\" (known: {})", known_list()));
                }
                StepKind::Balance { account }
            }
        };
        if matches!(kind, StepKind::Balance { .. })
            && matches!(expect, Expect::FailContains(_) | Expect::Contains(_))
        {
            return Err(format!("{ctx}: a balance step supports ok, equals, at_least, at_most"));
        }
        if matches!(kind, StepKind::Upgrade { .. }) && !matches!(expect, Expect::Ok | Expect::FailContains(_)) {
            return Err(format!("{ctx}: an upgrade step supports ok, fail_contains"));
        }
        steps.push(Step { index, kind, expect, expect_raw });
    }
    Ok(Scenario { name, accounts, wasm, init, steps })
}

// ---------------------------------------------------------------------------------------
// Aliases, actual results, expectation evaluation
// ---------------------------------------------------------------------------------------

/// Replace every JSON string that exactly equals an account alias (or `"contract"`) with the
/// full sandbox account id, recursively. Used for call/view args and expected values, so
/// `{"contains": "carol"}` matches `["carol.test.near"]`.
pub fn resolve_aliases(v: &Value, aliases: &BTreeMap<String, String>) -> Value {
    match v {
        Value::String(s) => Value::String(aliases.get(s).cloned().unwrap_or_else(|| s.clone())),
        Value::Array(a) => Value::Array(a.iter().map(|x| resolve_aliases(x, aliases)).collect()),
        Value::Object(o) => Value::Object(o.iter().map(|(k, x)| (k.clone(), resolve_aliases(x, aliases))).collect()),
        other => other.clone(),
    }
}

/// What the chain returned for one step. Always filled; `status` is `success`, `failure`
/// or `skipped` (not executed because the sandbox/init failed earlier).
#[derive(Debug, Clone, Default, PartialEq, Serialize)]
pub struct Actual {
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    /// Return value as JSON (`null` for an empty return). Balance steps: yoctoNEAR string.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub value: Option<Value>,
    /// true when the return bytes were not JSON; `value` then holds them as a lossy UTF-8 string.
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub value_not_json: bool,
    /// Balance steps: the same balance in NEAR, for reading.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub near: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gas_burnt_tgas: Option<f64>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub logs: Vec<String>,
    /// Upgrade steps: sha256 of the wasm sent in the `DeployContract` action.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub code_sha256: Option<String>,
    /// Failed `view` steps: the contract's own error text (the `vm_error` of the RPC query error,
    /// e.g. `... GuestPanic { panic_msg: "Cannot deserialize the contract state." }`), when it
    /// could be extracted; `error` then ends with the same text. Absent elsewhere.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_detail: Option<String>,
    /// Debug form of the error, only used for matching when `error` (Display) misses the text.
    #[serde(skip)]
    pub error_debug: Option<String>,
}

impl Actual {
    pub fn success(value: Option<Value>) -> Self {
        Actual { status: "success".into(), value, ..Default::default() }
    }
    pub fn failure(error: impl Into<String>) -> Self {
        Actual { status: "failure".into(), error: Some(error.into()), ..Default::default() }
    }
    pub fn skipped(reason: impl Into<String>) -> Self {
        Actual { status: "skipped".into(), error: Some(reason.into()), ..Default::default() }
    }
    pub fn is_success(&self) -> bool {
        self.status == "success"
    }
    /// A failed `view` step. `display` is the near-workspaces error text (an RPC wrapper such as
    /// `unable to fulfill the query request`), `debug` its `{:?}` form and `sources` the text of
    /// its `std::error::Error::source` chain, outermost first. The contract's own text (see
    /// `inner_error_text`) goes to `error_detail` and is appended to `error` unless the wrapper
    /// already contains it, so a report shows why the view failed, not only that the RPC refused.
    pub fn view_failure(display: String, debug: String, sources: Vec<String>) -> Self {
        let detail = inner_error_text(&debug, &sources);
        let error = match &detail {
            Some(d) if !display.contains(d.as_str()) => format!("{display}: {d}"),
            _ => display,
        };
        Actual { error_detail: detail, error_debug: Some(debug), ..Actual::failure(error) }
    }
    /// Convert raw return bytes into `value` (JSON if possible).
    pub fn from_return_bytes(bytes: &[u8]) -> Self {
        if bytes.is_empty() {
            return Actual::success(Some(Value::Null));
        }
        match serde_json::from_slice::<Value>(bytes) {
            Ok(v) => Actual::success(Some(v)),
            Err(_) => Actual {
                value_not_json: true,
                ..Actual::success(Some(Value::String(String::from_utf8_lossy(bytes).into_owned())))
            },
        }
    }
}

/// The contract's own error text inside a failed RPC query: the `vm_error: "..."` string of the
/// error's `Debug` form (escapes undone), else the innermost text of `sources` (the Display of
/// the error's `source()` chain), else nothing. Never invents text.
pub fn inner_error_text(debug: &str, sources: &[String]) -> Option<String> {
    if let Some(v) = debug_field(debug, "vm_error") {
        if !v.trim().is_empty() {
            return Some(v);
        }
    }
    sources.iter().rev().find(|s| !s.trim().is_empty()).cloned()
}

/// Value of the first `name: "..."` field in a Rust `Debug` rendering, with `\"`, `\\`, `\n`
/// and `\t` unescaped. `None` when the field is absent or its string is not closed.
fn debug_field(debug: &str, name: &str) -> Option<String> {
    let key = format!("{name}: \"");
    let start = debug.find(&key)? + key.len();
    let mut out = String::new();
    let mut chars = debug[start..].chars();
    while let Some(c) = chars.next() {
        match c {
            '\\' => match chars.next()? {
                'n' => out.push('\n'),
                't' => out.push('\t'),
                other => out.push(other),
            },
            '"' => return Some(out),
            other => out.push(other),
        }
    }
    None
}

fn json_eq(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => match (x.as_i64(), y.as_i64()) {
            (Some(i), Some(j)) => i == j,
            _ => match (x.as_u64(), y.as_u64()) {
                (Some(i), Some(j)) => i == j,
                _ => x.as_f64() == y.as_f64(),
            },
        },
        (Value::Array(x), Value::Array(y)) => x.len() == y.len() && x.iter().zip(y).all(|(p, q)| json_eq(p, q)),
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).is_some_and(|w| json_eq(v, w)))
        }
        _ => a == b,
    }
}

fn json_contains(actual: &Value, expected: &Value) -> bool {
    match (actual, expected) {
        (Value::Array(items), Value::Array(wanted)) => {
            wanted.iter().all(|w| items.iter().any(|i| json_eq(i, w)))
        }
        (Value::Array(items), w) => items.iter().any(|i| json_eq(i, w)),
        (Value::String(s), Value::String(w)) => s.contains(w.as_str()),
        (Value::Object(o), Value::String(k)) => o.contains_key(k),
        (Value::Object(o), Value::Object(w)) => w.iter().all(|(k, v)| o.get(k).is_some_and(|x| json_eq(x, v))),
        _ => false,
    }
}

/// Numeric view of a returned value: JSON integer or integer string (NEAR U128 is a string).
fn as_amount(v: &Value) -> Option<u128> {
    match v {
        Value::Number(n) => n.as_u64().map(u128::from),
        Value::String(s) => s.trim().parse::<u128>().ok(),
        _ => None,
    }
}

/// Evaluate one expectation. Returns (passed, reason-if-failed).
pub fn evaluate(expect: &Expect, actual: &Actual, aliases: &BTreeMap<String, String>) -> (bool, Option<String>) {
    let fail = |r: String| (false, Some(r));
    if actual.status == "skipped" {
        return fail("step not executed".into());
    }
    if let Expect::FailContains(needle) = expect {
        if actual.is_success() {
            return fail(format!("expected a failure containing {needle:?}, but the call succeeded"));
        }
        let hit = actual.error.as_deref().is_some_and(|e| e.contains(needle.as_str()))
            || actual.error_debug.as_deref().is_some_and(|e| e.contains(needle.as_str()));
        return if hit { (true, None) } else { fail(format!("failed, but the error does not contain {needle:?}")) };
    }
    if !actual.is_success() {
        return fail(format!(
            "expected success, got failure: {}",
            actual.error.as_deref().unwrap_or("(no error text)")
        ));
    }
    let value = actual.value.clone().unwrap_or(Value::Null);
    match expect {
        Expect::Ok => (true, None),
        Expect::Equals(want) => {
            let want = resolve_aliases(want, aliases);
            if json_eq(&value, &want) { (true, None) } else { fail(format!("expected {want}, got {value}")) }
        }
        Expect::Contains(want) => {
            let want = resolve_aliases(want, aliases);
            if json_contains(&value, &want) { (true, None) } else { fail(format!("{value} does not contain {want}")) }
        }
        Expect::AtLeast(min) => match as_amount(&value) {
            Some(n) if n >= *min => (true, None),
            Some(n) => fail(format!("{n} < {min}")),
            None => fail(format!("{value} is not an integer amount")),
        },
        Expect::AtMost(max) => match as_amount(&value) {
            Some(n) if n <= *max => (true, None),
            Some(n) => fail(format!("{n} > {max}")),
            None => fail(format!("{value} is not an integer amount")),
        },
        Expect::FailContains(_) => unreachable!("handled above"),
    }
}

// ---------------------------------------------------------------------------------------
// Report (FORMATS section 3)
// ---------------------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct StepResult {
    pub index: usize,
    pub kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub method: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub by: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub account: Option<String>,
    /// Upgrade steps: the wasm path as written in the scenario (the resolved path, after any
    /// `--upgrade-wasm` override, is the scenario's `upgrade_wasm`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub wasm: Option<String>,
    /// Upgrade steps with a migration: `{method, args, gas}` exactly as written in the scenario
    /// (args before alias resolution, gas as `"<n> Tgas"`), so the deploy gate can print the
    /// same batch call for the real account.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub migrate: Option<Value>,
    pub passed: bool,
    pub expected: Value,
    pub actual: Actual,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    /// Upgrade steps: the contract's key-value state right before the upgrade transaction.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub state_before: Option<StateDigest>,
    /// Upgrade steps with a migration that succeeded: the same migration call repeated by an
    /// account that is not the contract.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub access_probe: Option<AccessProbe>,
}

/// Size and fingerprint of a contract's key-value state at one moment. `sha256` covers every
/// key and value (sorted by key, each prefixed with its length as 8 little-endian bytes), so two
/// digests are equal only for the same state.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct StateDigest {
    pub keys: usize,
    pub bytes: usize,
    pub sha256: String,
}

pub fn state_digest<'a>(pairs: impl Iterator<Item = (&'a [u8], &'a [u8])>) -> StateDigest {
    let mut sorted: Vec<(&[u8], &[u8])> = pairs.collect();
    sorted.sort();
    let mut hasher = Sha256::new();
    let mut bytes = 0usize;
    for (k, v) in &sorted {
        hasher.update((k.len() as u64).to_le_bytes());
        hasher.update(k);
        hasher.update((v.len() as u64).to_le_bytes());
        hasher.update(v);
        bytes += k.len() + v.len();
    }
    let sha256 = hasher.finalize().iter().map(|b| format!("{b:02x}")).collect();
    StateDigest { keys: sorted.len(), bytes, sha256 }
}

/// The migration call of an upgrade step, sent again after the upgrade by `by` (an account that
/// is neither the contract nor a scenario account). `status` is `success` or `failure` as the
/// chain answered, or `not-run` when the probe account could not be created; `error` is the
/// chain's text. A `success` means anyone can call the migration method on the upgraded contract.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct AccessProbe {
    pub method: String,
    pub by: String,
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl StepResult {
    /// Record the access probe of an upgrade step. A probe that succeeded fails the step: the
    /// upgrade went through, but the migration method accepts a call from another account.
    pub fn apply_access_probe(&mut self, probe: AccessProbe) {
        if probe.status == "success" {
            self.passed = false;
            self.reason = Some(format!(
                "access probe: {} called {} after the upgrade and the call succeeded; the migration method must refuse other callers (#[private] or an owner check)",
                probe.by, probe.method
            ));
        }
        self.access_probe = Some(probe);
    }

    pub fn new(step: &Step, actual: Actual, aliases: &BTreeMap<String, String>) -> Self {
        let (passed, reason) = evaluate(&step.expect, &actual, aliases);
        let (method, by, account, wasm) = match &step.kind {
            StepKind::Call { method, by, .. } => (Some(method.clone()), Some(by.clone()), None, None),
            StepKind::View { method, .. } => (Some(method.clone()), None, None, None),
            StepKind::Balance { account } => (None, None, Some(account.clone()), None),
            StepKind::Upgrade { wasm, migrate } => (
                migrate.as_ref().map(|m| m.method.clone()),
                Some(CONTRACT_ALIAS.to_string()),
                None,
                Some(wasm.clone()),
            ),
        };
        let migrate = match &step.kind {
            StepKind::Upgrade { migrate: Some(m), .. } => Some(serde_json::json!({
                "method": m.method,
                "args": m.args,
                "gas": format_tgas(m.gas),
            })),
            _ => None,
        };
        StepResult {
            index: step.index,
            kind: step.kind.kind_name().into(),
            method,
            by,
            account,
            wasm,
            migrate,
            passed,
            expected: step.expect_raw.clone(),
            actual,
            reason,
            state_before: None,
            access_probe: None,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct InitResult {
    pub method: String,
    pub passed: bool,
    pub actual: Actual,
}

#[derive(Debug, Clone, Serialize)]
pub struct ScenarioResult {
    pub name: String,
    pub file: String,
    pub scenario_sha256: String,
    pub wasm: String,
    pub wasm_sha256: Option<String>,
    /// FORMATS section 2a: resolved wasm of the upgrade step(s); `null` without an upgrade step.
    pub upgrade_wasm: Option<String>,
    /// Shared sha256 of the upgrade wasm(s); `null` without an upgrade step (or, with several
    /// upgrade steps, if their wasm files differ: see each step's `actual.code_sha256`).
    pub upgrade_wasm_sha256: Option<String>,
    pub passed: bool,
    pub duration_s: f64,
    /// Infrastructure error (sandbox start, account creation, deploy, missing wasm).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    /// alias -> sandbox account id
    pub accounts: BTreeMap<String, String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub init: Option<InitResult>,
    /// The contract's key-value state after `init` (after the deploy when there is no `init`),
    /// before the first step. Absent when it could not be read.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub state_after_init: Option<StateDigest>,
    pub steps: Vec<StepResult>,
}

impl ScenarioResult {
    /// Fill steps that were never executed (infra/init failure) with `skipped`, then set
    /// `passed`. Every step of the file therefore appears in the report.
    pub fn finish(&mut self, sc: &Scenario, aliases: &BTreeMap<String, String>, duration_s: f64) {
        let reason = match (&self.error, &self.init) {
            (Some(e), _) => format!("not executed: {e}"),
            (None, Some(i)) if !i.passed => "not executed: init failed".to_string(),
            _ => "not executed".to_string(),
        };
        for step in sc.steps.iter().skip(self.steps.len()) {
            self.steps.push(StepResult::new(step, Actual::skipped(reason.clone()), aliases));
        }
        self.passed = self.error.is_none()
            && self.init.as_ref().is_none_or(|i| i.passed)
            && self.steps.iter().all(|s| s.passed);
        self.duration_s = (duration_s * 10.0).round() / 10.0;
    }
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Summary {
    pub scenarios: usize,
    pub passed: usize,
    pub failed: usize,
    pub steps: usize,
    pub steps_failed: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct SandboxInfo {
    pub near_workspaces: &'static str,
    pub near_sandbox: &'static str,
}

#[derive(Debug, Clone, Serialize)]
pub struct Report {
    pub tool: &'static str,
    pub version: &'static str,
    pub runner: String,
    /// Shared wasm hash of all scenarios; `null` if they use different wasm files or none was read.
    pub wasm_sha256: Option<String>,
    /// Shared upgrade wasm hash of the scenarios that have an upgrade step; `null` if none has
    /// one or they differ. Scenarios without an upgrade step do not count.
    pub upgrade_wasm_sha256: Option<String>,
    /// See [`combined_sha256`].
    pub scenario_sha256: String,
    pub sandbox: SandboxInfo,
    pub scenarios: Vec<ScenarioResult>,
    pub summary: Summary,
}

pub fn summarize(results: &[ScenarioResult]) -> Summary {
    let passed = results.iter().filter(|r| r.passed).count();
    Summary {
        scenarios: results.len(),
        passed,
        failed: results.len() - passed,
        steps: results.iter().map(|r| r.steps.len()).sum(),
        steps_failed: results.iter().map(|r| r.steps.iter().filter(|s| !s.passed).count()).sum(),
    }
}

pub fn build_report(results: Vec<ScenarioResult>) -> Report {
    let mut wasm_hashes: Vec<Option<&String>> = results.iter().map(|r| r.wasm_sha256.as_ref()).collect();
    wasm_hashes.dedup();
    let wasm_sha256 = match wasm_hashes.as_slice() {
        [Some(h)] => Some((*h).clone()),
        _ => None,
    };
    let file_hashes: Vec<String> = results.iter().map(|r| r.scenario_sha256.clone()).collect();
    let upgrade_wasm_sha256 = shared_hash(results.iter().filter_map(|r| r.upgrade_wasm_sha256.as_deref()));
    Report {
        tool: TOOL,
        version: RUNNER_VERSION,
        runner: format!("bumm-sim {RUNNER_VERSION}"),
        wasm_sha256,
        upgrade_wasm_sha256,
        scenario_sha256: combined_sha256(&file_hashes),
        sandbox: SandboxInfo { near_workspaces: NEAR_WORKSPACES_VERSION, near_sandbox: NEAR_SANDBOX_VERSION },
        summary: summarize(&results),
        scenarios: results,
    }
}

// ---------------------------------------------------------------------------------------
// Hashing, paths, CLI
// ---------------------------------------------------------------------------------------

/// The one hash all items share; `None` for no items or differing hashes.
pub fn shared_hash<'a>(mut hashes: impl Iterator<Item = &'a str>) -> Option<String> {
    let first = hashes.next()?;
    hashes.all(|h| h == first).then(|| first.to_string())
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes).iter().map(|b| format!("{b:02x}")).collect()
}

/// Report-level `scenario_sha256`: the file's sha256 for one file; for several files the
/// sha256 of their hex digests, sorted, joined with `\n` (order of `--scenario` irrelevant).
/// `bumm_guard/simulate.py` computes the same value.
pub fn combined_sha256(file_hashes: &[String]) -> String {
    match file_hashes {
        [one] => one.clone(),
        many => {
            let mut v: Vec<&str> = many.iter().map(String::as_str).collect();
            v.sort_unstable();
            sha256_hex(v.join("\n").as_bytes())
        }
    }
}

/// Locate the wasm: `--wasm` override; absolute path; relative to the current directory
/// (normally the repository root); else relative to the scenario file's directory.
pub fn resolve_wasm(spec: &str, scenario_file: &Path, override_path: Option<&Path>) -> PathBuf {
    if let Some(p) = override_path {
        return p.to_path_buf();
    }
    let p = Path::new(spec);
    if p.is_absolute() || p.exists() {
        return p.to_path_buf();
    }
    if let Some(dir) = scenario_file.parent() {
        let q = dir.join(p);
        if q.exists() {
            return q;
        }
    }
    p.to_path_buf()
}

pub const USAGE: &str = "usage: bumm-sim run --scenario <file.json> [--scenario <file.json> ...] --out <report.json> [--wasm <file.wasm>] [--upgrade-wasm <file.wasm>]

Runs each scenario file (docs/FORMATS.md section 2) in its own fresh near-sandbox and writes
the section 3 report. --wasm replaces contract.wasm; --upgrade-wasm replaces the wasm of every
upgrade step (section 2a). Exit: 0 all scenarios passed, 1 a scenario failed, 2 usage/parse error.";

#[derive(Debug, Clone, PartialEq)]
pub enum Command {
    Help,
    Version,
    Run { scenarios: Vec<PathBuf>, out: PathBuf, wasm: Option<PathBuf>, upgrade_wasm: Option<PathBuf> },
}

pub fn parse_args(args: &[String]) -> Result<Command, String> {
    let mut it = args.iter();
    match it.next().map(String::as_str) {
        None => return Err("missing command".into()),
        Some("-h" | "--help" | "help") => return Ok(Command::Help),
        Some("-V" | "--version") => return Ok(Command::Version),
        Some("run") => {}
        Some(other) => return Err(format!("unknown command {other:?}")),
    }
    let (mut scenarios, mut out, mut wasm, mut upgrade_wasm) = (Vec::new(), None, None, None);
    while let Some(a) = it.next() {
        let mut value = |flag: &str| it.next().cloned().ok_or_else(|| format!("{flag} needs a value"));
        match a.as_str() {
            "--scenario" => scenarios.push(PathBuf::from(value("--scenario")?)),
            "--out" => out = Some(PathBuf::from(value("--out")?)),
            "--wasm" => wasm = Some(PathBuf::from(value("--wasm")?)),
            "--upgrade-wasm" => upgrade_wasm = Some(PathBuf::from(value("--upgrade-wasm")?)),
            "-h" | "--help" => return Ok(Command::Help),
            other => return Err(format!("unknown argument {other:?}")),
        }
    }
    if scenarios.is_empty() {
        return Err("at least one --scenario is required".into());
    }
    let out = out.ok_or("--out is required")?;
    Ok(Command::Run { scenarios, out, wasm, upgrade_wasm })
}

// ---------------------------------------------------------------------------------------
// Tests (no sandbox)
// ---------------------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const N06: &str = r#"{
      "name": "N06 capacity 2, third refused, cancel frees a seat",
      "accounts": {"alice": "10 NEAR", "bob": "10 NEAR", "carol": "10 NEAR"},
      "contract": {"wasm": "target/near/event_registration.wasm",
                   "init": {"method": "new", "args": {"capacity": 2}}},
      "steps": [
        {"call": "register", "by": "alice", "args": {}, "deposit": "0", "gas": "30 Tgas", "expect": "ok"},
        {"call": "register", "by": "bob", "expect": "ok"},
        {"call": "register", "by": "carol", "expect": {"fail_contains": "Event is full"}},
        {"view": "get_registered_count", "expect": {"equals": 2}},
        {"call": "cancel", "by": "alice", "expect": "ok"},
        {"call": "register", "by": "carol", "expect": "ok"},
        {"view": "get_registered", "expect": {"contains": "carol"}},
        {"balance": "bob", "expect": {"at_least": "9 NEAR"}}
      ]
    }"#;

    fn aliases() -> BTreeMap<String, String> {
        [("alice", "alice.test.near"), ("carol", "carol.test.near"), ("contract", "contract.test.near")]
            .into_iter()
            .map(|(a, b)| (a.to_string(), b.to_string()))
            .collect()
    }

    #[test]
    fn near_amounts() {
        assert_eq!(parse_near_amount("1 NEAR").unwrap(), YOCTO_PER_NEAR);
        assert_eq!(parse_near_amount("10 NEAR").unwrap(), 10 * YOCTO_PER_NEAR);
        assert_eq!(parse_near_amount("1.5 NEAR").unwrap(), 15 * YOCTO_PER_NEAR / 10);
        assert_eq!(parse_near_amount("0.000000000000000000000001 NEAR").unwrap(), 1);
        assert_eq!(parse_near_amount(" 2near ").unwrap(), 2 * YOCTO_PER_NEAR);
        assert_eq!(parse_near_amount("0").unwrap(), 0);
        assert_eq!(parse_near_amount("1000").unwrap(), 1000);
        for bad in ["", "NEAR", "-1", "1e3", "1.5", "1,5 NEAR", "abc", "0.0000000000000000000000001 NEAR", "1 ETH"] {
            assert!(parse_near_amount(bad).is_err(), "{bad:?} must be rejected");
        }
        assert!(parse_near_amount("999999999999999999999999 NEAR").is_err(), "overflow");
    }

    #[test]
    fn gas_values() {
        assert_eq!(parse_gas("30 Tgas").unwrap(), DEFAULT_GAS);
        assert_eq!(parse_gas("2.5 Tgas").unwrap(), 2_500_000_000_000);
        assert_eq!(parse_gas("300TGAS").unwrap(), 300 * GAS_PER_TGAS);
        assert_eq!(parse_gas("1000").unwrap(), 1000);
        assert!(parse_gas("30 Ggas").is_err());
        assert!(parse_gas("99999999999 Tgas").is_err(), "u64 overflow");
        assert_eq!(gas_to_tgas(2_912_345_678_901), 2.912);
        assert_eq!(format_tgas(DEFAULT_MIGRATE_GAS), "100 Tgas");
        assert_eq!(format_tgas(2_500_000_000_000), "2.5 Tgas");
        assert_eq!(format_tgas(1), "0.000000000001 Tgas");
        for g in [DEFAULT_GAS, 2_500_000_000_000, 1, 123_456_789_012_345] {
            assert_eq!(parse_gas(&format_tgas(g)).unwrap(), g, "round trip");
        }
    }

    #[test]
    fn format_near_readable() {
        assert_eq!(format_near(10 * YOCTO_PER_NEAR), "10");
        assert_eq!(format_near(15 * YOCTO_PER_NEAR / 10), "1.5");
        assert_eq!(format_near(1), "0.000000000000000000000001");
    }

    #[test]
    fn parses_the_formats_example() {
        let sc = parse_scenario(N06).unwrap();
        assert_eq!(sc.accounts.len(), 3);
        assert_eq!(sc.accounts["alice"], 10 * YOCTO_PER_NEAR);
        assert_eq!(sc.init, Some(Init { method: "new".into(), args: json!({"capacity": 2}) }));
        assert_eq!(sc.steps.len(), 8);
        assert_eq!(
            sc.steps[1].kind,
            StepKind::Call { method: "register".into(), by: "bob".into(), args: json!({}), deposit: 0, gas: DEFAULT_GAS }
        );
        assert_eq!(sc.steps[2].expect, Expect::FailContains("Event is full".into()));
        assert_eq!(sc.steps[2].expect_raw, json!({"fail_contains": "Event is full"}));
        assert_eq!(sc.steps[7].kind, StepKind::Balance { account: "bob".into() });
        assert_eq!(sc.steps[7].expect, Expect::AtLeast(9 * YOCTO_PER_NEAR));
        assert_eq!(sc.steps[3].index, 3);
    }

    fn with_step(step: &str) -> String {
        format!(
            r#"{{"name":"t","accounts":{{"alice":"1 NEAR"}},"contract":{{"wasm":"x.wasm"}},"steps":[{step}]}}"#
        )
    }

    #[test]
    fn parse_errors_are_specific() {
        let cases = [
            (r#"{"transfer": "alice"}"#, "step 0: unknown step kind"),
            (r#"{"call": "register", "by": "dave"}"#, "unknown account \"dave\""),
            (r#"{"balance": "dave"}"#, "unknown account \"dave\""),
            (r#"{"call": "register", "by": "alice", "deposit": "1 ETH"}"#, "deposit: bad NEAR amount"),
            (r#"{"call": "register", "by": "alice", "gas": "lots"}"#, "gas: bad gas"),
            (r#"{"call": "register", "by": "alice", "expcet": "ok"}"#, "unknown field \"expcet\""),
            (r#"{"call": "register", "view": "x", "by": "alice"}"#, "more than one step kind"),
            (r#"{"view": "x", "expect": {"greater": 1}}"#, "unknown expectation \"greater\""),
            (r#"{"view": "x", "expect": "fine"}"#, "expect must be"),
            (r#"{"view": "x", "args": [1]}"#, "\"args\" must be a JSON object"),
            (r#"{"balance": "alice", "expect": {"contains": "x"}}"#, "balance step supports"),
            (r#"{"call": "register"}"#, "missing \"by\""),
        ];
        for (step, want) in cases {
            let err = parse_scenario(&with_step(step)).unwrap_err();
            assert!(err.contains(want), "{step}: got {err:?}, want {want:?}");
        }
        let err = parse_scenario(r#"{"name":"t","accounts":{"alice":"lots"},"contract":{"wasm":"x"},"steps":[]}"#).unwrap_err();
        assert!(err.contains("accounts.alice: bad NEAR amount"), "{err}");
        let err = parse_scenario(r#"{"name":"t","accounts":{"contract":"1 NEAR"},"contract":{"wasm":"x"},"steps":[]}"#).unwrap_err();
        assert!(err.contains("reserved"), "{err}");
        let err = parse_scenario(r#"{"name":"t","accounts":{"Bob.x":"1 NEAR"},"contract":{"wasm":"x"},"steps":[]}"#).unwrap_err();
        assert!(err.contains("bad account name"), "{err}");
        assert!(parse_scenario("{not json").unwrap_err().starts_with("invalid JSON"));
        let err = parse_scenario(r#"{"name":"t","accounts":{},"contract":{"wasm":"x"},"steps":[],"loop":1}"#).unwrap_err();
        assert!(err.contains("unknown field \"loop\""), "{err}");
    }

    #[test]
    fn contract_is_a_valid_caller() {
        let sc = parse_scenario(&with_step(r#"{"call": "new", "by": "contract"}"#)).unwrap();
        assert!(matches!(&sc.steps[0].kind, StepKind::Call { by, .. } if by == "contract"));
        assert_eq!(sc.steps[0].expect, Expect::Ok, "missing expect defaults to ok");
    }

    #[test]
    fn aliases_resolve_recursively() {
        let v = resolve_aliases(&json!({"account_id": "carol", "list": ["alice", "zed"], "n": 1}), &aliases());
        assert_eq!(v, json!({"account_id": "carol.test.near", "list": ["alice.test.near", "zed"], "n": 1}));
    }

    fn failure(text: &str) -> Actual {
        Actual::failure(format!("Action #0: ExecutionError(\"Smart contract panicked: {text}\")"))
    }

    #[test]
    fn expectations() {
        let a = aliases();
        let ok = Actual::success(Some(Value::Null));
        assert_eq!(evaluate(&Expect::Ok, &ok, &a), (true, None));
        assert!(!evaluate(&Expect::Ok, &failure("boom"), &a).0);

        let full = failure("Event is full: capacity 2 reached, registration refused");
        assert!(evaluate(&Expect::FailContains("Event is full".into()), &full, &a).0);
        assert!(!evaluate(&Expect::FailContains("already registered".into()), &full, &a).0);
        let (p, reason) = evaluate(&Expect::FailContains("Event is full".into()), &ok, &a);
        assert!(!p && reason.unwrap().contains("succeeded"));
        let mut dbg = Actual::failure("Execution");
        dbg.error_debug = Some("... Event is full ...".into());
        assert!(evaluate(&Expect::FailContains("Event is full".into()), &dbg, &a).0, "falls back to debug text");

        let two = Actual::success(Some(json!(2)));
        assert!(evaluate(&Expect::Equals(json!(2)), &two, &a).0);
        assert!(evaluate(&Expect::Equals(json!(2.0)), &two, &a).0, "2 == 2.0");
        assert!(!evaluate(&Expect::Equals(json!(3)), &two, &a).0);
        assert!(!evaluate(&Expect::Equals(json!(2)), &failure("x"), &a).0);

        let list = Actual::success(Some(json!(["alice.test.near", "carol.test.near"])));
        assert!(evaluate(&Expect::Contains(json!("carol")), &list, &a).0, "alias resolved");
        assert!(evaluate(&Expect::Contains(json!(["alice", "carol"])), &list, &a).0);
        assert!(!evaluate(&Expect::Contains(json!("bob")), &list, &a).0);
        assert!(evaluate(&Expect::Equals(json!(["alice", "carol"])), &list, &a).0);
        let s = Actual::success(Some(json!("hello world")));
        assert!(evaluate(&Expect::Contains(json!("world")), &s, &a).0);

        let bal = Actual::success(Some(json!("9999000000000000000000000")));
        assert!(evaluate(&Expect::AtLeast(9 * YOCTO_PER_NEAR), &bal, &a).0);
        assert!(!evaluate(&Expect::AtLeast(10 * YOCTO_PER_NEAR), &bal, &a).0);
        assert!(evaluate(&Expect::AtMost(10 * YOCTO_PER_NEAR), &bal, &a).0);
        assert!(evaluate(&Expect::AtMost(2), &two, &a).0);
        assert!(!evaluate(&Expect::AtLeast(1), &Actual::success(Some(json!("x"))), &a).0);
        assert!(!evaluate(&Expect::Ok, &Actual::skipped("not executed"), &a).0);
    }

    #[test]
    fn view_failure_exposes_the_contract_error() {
        // Debug form of the near-workspaces error for a view on code that cannot read the state.
        let debug = r#"Error { repr: Custom { kind: Execution, error: ServerError(HandlerError(ContractExecutionError { vm_error: "wasm execution failed with error: HostError(GuestPanic { panic_msg: \"Cannot deserialize the contract state.\" })", block_height: 12, block_hash: 4YxC })) } }"#;
        let a = Actual::view_failure("unable to fulfill the query request".into(), debug.into(), vec![]);
        assert_eq!(a.status, "failure");
        assert_eq!(
            a.error_detail.as_deref(),
            Some("wasm execution failed with error: HostError(GuestPanic { panic_msg: \"Cannot deserialize the contract state.\" })")
        );
        let err = a.error.clone().unwrap();
        assert!(err.starts_with("unable to fulfill the query request: "), "{err}");
        assert!(err.contains("Cannot deserialize the contract state."), "{err}");
        let v = serde_json::to_value(&a).unwrap();
        assert!(v["error"].as_str().unwrap().contains("Cannot deserialize"));
        assert!(v["error_detail"].as_str().unwrap().contains("Cannot deserialize"));
        assert!(v.get("error_debug").is_none());
        let al = aliases();
        assert!(evaluate(&Expect::FailContains("Cannot deserialize".into()), &a, &al).0);

        // No vm_error in the debug text: the innermost source() text is used.
        let b = Actual::view_failure("outer".into(), "Error { .. }".into(), vec!["mid".into(), "inner text".into()]);
        assert_eq!(b.error.as_deref(), Some("outer: inner text"));
        assert_eq!(b.error_detail.as_deref(), Some("inner text"));
        // Neither: nothing is invented and the field is not serialized.
        let c = Actual::view_failure("outer".into(), "Error { .. }".into(), vec![]);
        assert_eq!(c.error.as_deref(), Some("outer"));
        assert!(c.error_detail.is_none());
        assert!(serde_json::to_value(&c).unwrap().get("error_detail").is_none());
        // Already in the wrapper text: not repeated.
        let d = Actual::view_failure("x: boom".into(), r#"vm_error: "boom""#.into(), vec![]);
        assert_eq!(d.error.as_deref(), Some("x: boom"));
        assert_eq!(d.error_detail.as_deref(), Some("boom"));
        // Unterminated string: ignored.
        assert_eq!(inner_error_text(r#"vm_error: "no end"#, &[]), None);
        assert_eq!(debug_field(r#"a: "x\\y\n""#, "a"), Some("x\\y\n".to_string()));
    }

    #[test]
    fn return_bytes() {
        assert_eq!(Actual::from_return_bytes(b"").value, Some(Value::Null));
        assert_eq!(Actual::from_return_bytes(b"2").value, Some(json!(2)));
        let raw = Actual::from_return_bytes(b"\xffnot json");
        assert!(raw.value_not_json && raw.is_success());
    }

    #[test]
    fn report_shape_and_skips() {
        let sc = parse_scenario(N06).unwrap();
        let a = aliases();
        let mut r = ScenarioResult {
            name: sc.name.clone(),
            file: "scenarios/n06/x.json".into(),
            scenario_sha256: sha256_hex(N06.as_bytes()),
            wasm: sc.wasm.clone(),
            wasm_sha256: Some("ab".into()),
            upgrade_wasm: None,
            upgrade_wasm_sha256: None,
            passed: false,
            duration_s: 0.0,
            error: None,
            accounts: a.clone(),
            init: Some(InitResult { method: "new".into(), passed: true, actual: Actual::success(Some(Value::Null)) }),
            state_after_init: None,
            steps: vec![],
        };
        r.steps.push(StepResult::new(&sc.steps[0], Actual::success(Some(Value::Null)), &a));
        r.steps.push(StepResult::new(&sc.steps[1], Actual::success(Some(Value::Null)), &a));
        r.steps.push(StepResult::new(&sc.steps[2], failure("Event is full: capacity 2 reached"), &a));
        r.error = Some("sandbox died".into());
        r.finish(&sc, &a, 12.44);
        assert_eq!(r.steps.len(), 8);
        assert!(!r.passed);
        assert_eq!(r.duration_s, 12.4);
        assert_eq!(r.steps[3].actual.status, "skipped");
        let report = build_report(vec![r]);
        assert_eq!(report.summary, Summary { scenarios: 1, passed: 0, failed: 1, steps: 8, steps_failed: 5 });
        let v = serde_json::to_value(&report).unwrap();
        assert_eq!(v["tool"], "bumm-guard");
        assert_eq!(v["wasm_sha256"], "ab");
        assert_eq!(v["upgrade_wasm_sha256"], Value::Null, "present and null without an upgrade step");
        assert_eq!(v["scenarios"][0]["upgrade_wasm_sha256"], Value::Null);
        assert_eq!(v["sandbox"]["near_workspaces"], "0.24.0");
        let s2 = &v["scenarios"][0]["steps"][2];
        assert_eq!(s2["index"], 2);
        assert_eq!(s2["kind"], "call");
        assert_eq!(s2["by"], "carol");
        assert_eq!(s2["passed"], true);
        assert_eq!(s2["expected"], json!({"fail_contains": "Event is full"}));
        assert_eq!(s2["actual"]["status"], "failure");
        assert!(s2["actual"].get("error_debug").is_none());
        assert_eq!(v["scenarios"][0]["steps"][7]["account"], "bob");
    }

    /// The repository's acceptance scenarios for FORMATS section 2a must parse.
    #[test]
    fn parses_the_repo_upgrade_scenarios() {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../scenarios/upgrade");
        let with = parse_scenario(&std::fs::read_to_string(dir.join("n06_to_n10_with_migrate.json")).unwrap()).unwrap();
        let code_only = parse_scenario(&std::fs::read_to_string(dir.join("n06_to_n10_code_only.json")).unwrap()).unwrap();
        let wasm = "tests/fixtures/near/target/wasm32-unknown-unknown/release/event_registration_waitlist.wasm";
        assert_eq!(
            with.steps[3].kind,
            StepKind::Upgrade {
                wasm: wasm.into(),
                migrate: Some(Migrate { method: "migrate".into(), args: json!({}), gas: DEFAULT_MIGRATE_GAS })
            }
        );
        assert_eq!(code_only.steps[3].kind, StepKind::Upgrade { wasm: wasm.into(), migrate: None });
        assert_eq!(with.steps.len(), code_only.steps.len());
        let upgrades = |sc: &Scenario| sc.steps.iter().filter(|s| matches!(s.kind, StepKind::Upgrade { .. })).count();
        assert_eq!((upgrades(&with), upgrades(&code_only)), (1, 1));
    }

    #[test]
    fn upgrade_step_parsing() {
        let sc = parse_scenario(&with_step(
            r#"{"upgrade": "new.wasm", "migrate": {"method": "migrate", "args": {"x": "alice"}, "gas": "200 Tgas"}, "expect": {"fail_contains": "No old state"}}"#,
        ))
        .unwrap();
        let step = &sc.steps[0];
        assert_eq!(step.kind.kind_name(), "upgrade");
        assert_eq!(
            step.kind,
            StepKind::Upgrade {
                wasm: "new.wasm".into(),
                migrate: Some(Migrate { method: "migrate".into(), args: json!({"x": "alice"}), gas: 200 * GAS_PER_TGAS })
            }
        );
        assert_eq!(step.expect, Expect::FailContains("No old state".into()));

        let sc = parse_scenario(&with_step(r#"{"upgrade": "new.wasm", "migrate": {"method": "m"}}"#)).unwrap();
        assert!(
            matches!(&sc.steps[0].kind, StepKind::Upgrade { migrate: Some(m), .. } if m.args == json!({}) && m.gas == DEFAULT_MIGRATE_GAS),
            "args default {{}}, gas default 100 Tgas"
        );
        assert_eq!(sc.steps[0].expect, Expect::Ok, "missing expect defaults to ok");
        let sc = parse_scenario(&with_step(r#"{"upgrade": "new.wasm", "migrate": null}"#)).unwrap();
        assert_eq!(sc.steps[0].kind, StepKind::Upgrade { wasm: "new.wasm".into(), migrate: None });
    }

    #[test]
    fn upgrade_step_errors() {
        let cases = [
            (r#"{"upgrade": ""}"#, "\"upgrade\" must be a non-empty string"),
            (r#"{"upgrade": 1}"#, "\"upgrade\" must be a non-empty string"),
            (r#"{"upgrade": "n.wasm", "by": "alice"}"#, "unknown field \"by\""),
            (r#"{"upgrade": "n.wasm", "call": "x", "by": "alice"}"#, "more than one step kind"),
            (r#"{"upgrade": "n.wasm", "migrate": "migrate"}"#, "migrate must be an object"),
            (r#"{"upgrade": "n.wasm", "migrate": {}}"#, "step 0.migrate: missing \"method\""),
            (r#"{"upgrade": "n.wasm", "migrate": {"method": "m", "deposit": "1"}}"#, "step 0.migrate: unknown field \"deposit\""),
            (r#"{"upgrade": "n.wasm", "migrate": {"method": "m", "args": [1]}}"#, "step 0.migrate: \"args\" must be a JSON object"),
            (r#"{"upgrade": "n.wasm", "migrate": {"method": "m", "gas": "lots"}}"#, "step 0.migrate: gas: bad gas"),
            (r#"{"upgrade": "n.wasm", "migrate": {"method": "m", "gas": 5}}"#, "step 0.migrate: gas must be a string"),
            (r#"{"upgrade": "n.wasm", "expect": {"equals": 1}}"#, "upgrade step supports ok, fail_contains"),
            (r#"{"upgrade": "n.wasm", "expect": {"at_least": "1 NEAR"}}"#, "upgrade step supports ok, fail_contains"),
        ];
        for (step, want) in cases {
            let err = parse_scenario(&with_step(step)).unwrap_err();
            assert!(err.contains(want), "{step}: got {err:?}, want {want:?}");
        }
        let err = parse_scenario(&with_step(r#"{"transfer": "alice"}"#)).unwrap_err();
        assert!(err.contains("call, view, balance, upgrade"), "{err}");
    }

    #[test]
    fn upgrade_report_fields() {
        let sc = parse_scenario(&with_step(r#"{"upgrade": "new.wasm", "migrate": {"method": "migrate"}}"#)).unwrap();
        let a = aliases();
        let ok = Actual { code_sha256: Some("cd".into()), gas_burnt_tgas: Some(5.1), ..Actual::success(Some(Value::Null)) };
        let step = StepResult::new(&sc.steps[0], ok, &a);
        assert!(step.passed);
        let v = serde_json::to_value(&step).unwrap();
        assert_eq!(v["kind"], "upgrade");
        assert_eq!(v["method"], "migrate");
        assert_eq!(v["by"], "contract");
        assert_eq!(v["wasm"], "new.wasm");
        assert_eq!(v["actual"]["code_sha256"], "cd");
        assert_eq!(v["migrate"], json!({"method": "migrate", "args": {}, "gas": "100 Tgas"}));
        let aliased = parse_scenario(&with_step(
            r#"{"upgrade": "n.wasm", "migrate": {"method": "m", "args": {"owner": "alice"}, "gas": "2.5 Tgas"}}"#,
        ))
        .unwrap();
        let v = serde_json::to_value(StepResult::new(&aliased.steps[0], Actual::success(None), &a)).unwrap();
        assert_eq!(v["migrate"], json!({"method": "m", "args": {"owner": "alice"}, "gas": "2.5 Tgas"}), "args as written");
        let code_only = parse_scenario(&with_step(r#"{"upgrade": "new.wasm"}"#)).unwrap();
        let v = serde_json::to_value(StepResult::new(&code_only.steps[0], Actual::failure("x"), &a)).unwrap();
        assert!(v.get("method").is_none());
        assert!(v.get("migrate").is_none());
        assert_eq!(v["passed"], false);

        let result = |name: &str, up: Option<&str>| ScenarioResult {
            name: name.into(),
            file: format!("{name}.json"),
            scenario_sha256: sha256_hex(name.as_bytes()),
            wasm: "old.wasm".into(),
            wasm_sha256: Some("aa".into()),
            upgrade_wasm: up.map(|_| "new.wasm".into()),
            upgrade_wasm_sha256: up.map(str::to_string),
            passed: true,
            duration_s: 1.0,
            error: None,
            accounts: BTreeMap::new(),
            init: None,
            state_after_init: None,
            steps: vec![],
        };
        let r = build_report(vec![result("a", Some("bb")), result("b", None), result("c", Some("bb"))]);
        assert_eq!(r.wasm_sha256.as_deref(), Some("aa"));
        assert_eq!(r.upgrade_wasm_sha256.as_deref(), Some("bb"), "scenarios without upgrade do not count");
        let r = build_report(vec![result("a", Some("bb")), result("c", Some("cc"))]);
        assert_eq!(r.upgrade_wasm_sha256, None, "differing upgrade wasm");
        let v = serde_json::to_value(build_report(vec![result("b", None)])).unwrap();
        assert_eq!(v["upgrade_wasm_sha256"], Value::Null);
        assert_eq!(v["scenarios"][0]["upgrade_wasm"], Value::Null);
        assert_eq!(shared_hash(["x", "x"].into_iter()).as_deref(), Some("x"));
        assert_eq!(shared_hash(std::iter::empty()), None);
    }

    #[test]
    fn hashes() {
        assert_eq!(sha256_hex(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        let h = vec!["bb".to_string(), "aa".to_string()];
        assert_eq!(combined_sha256(&h), sha256_hex(b"aa\nbb"));
        assert_eq!(combined_sha256(&h[..1]), "bb");
        let mut r = vec!["aa".to_string(), "bb".to_string()];
        r.reverse();
        assert_eq!(combined_sha256(&r), combined_sha256(&h), "order-independent");
    }

    #[test]
    fn cli_args() {
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        assert_eq!(
            parse_args(&s(&["run", "--scenario", "a.json", "--scenario", "b.json", "--out", "r.json"])).unwrap(),
            Command::Run {
                scenarios: vec!["a.json".into(), "b.json".into()],
                out: "r.json".into(),
                wasm: None,
                upgrade_wasm: None
            }
        );
        assert!(matches!(
            parse_args(&s(&["run", "--scenario", "a.json", "--out", "r.json", "--wasm", "c.wasm"])).unwrap(),
            Command::Run { wasm: Some(_), .. }
        ));
        assert_eq!(parse_args(&s(&["--help"])).unwrap(), Command::Help);
        assert!(parse_args(&s(&[])).is_err());
        assert!(parse_args(&s(&["run", "--out", "r.json"])).unwrap_err().contains("--scenario"));
        assert!(parse_args(&s(&["run", "--scenario", "a.json"])).unwrap_err().contains("--out"));
        assert!(parse_args(&s(&["run", "--scenario"])).unwrap_err().contains("needs a value"));
        assert!(parse_args(&s(&["walk"])).is_err());
        assert!(matches!(
            parse_args(&s(&["run", "--scenario", "a.json", "--out", "r.json", "--upgrade-wasm", "n.wasm"])).unwrap(),
            Command::Run { wasm: None, upgrade_wasm: Some(p), .. } if p == Path::new("n.wasm")
        ));
        assert!(parse_args(&s(&["run", "--scenario", "a.json", "--out", "r.json", "--upgrade-wasm"]))
            .unwrap_err()
            .contains("needs a value"));
    }

    #[test]
    fn wasm_resolution() {
        let dir = std::env::temp_dir().join(format!("bumm-sim-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("c.wasm"), b"\0asm").unwrap();
        let scen = dir.join("s.json");
        assert_eq!(resolve_wasm("c.wasm", &scen, None), dir.join("c.wasm"), "scenario-relative fallback");
        assert_eq!(resolve_wasm("c.wasm", &scen, Some(Path::new("o.wasm"))), PathBuf::from("o.wasm"));
        assert_eq!(resolve_wasm("missing.wasm", &scen, None), PathBuf::from("missing.wasm"));
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn state_digest_is_order_independent_and_length_prefixed() {
        let a: Vec<(&[u8], &[u8])> = vec![(&b"k1"[..], &b"v1"[..]), (&b"k2"[..], &b"value2"[..])];
        let b: Vec<(&[u8], &[u8])> = vec![(&b"k2"[..], &b"value2"[..]), (&b"k1"[..], &b"v1"[..])];
        let da = state_digest(a.into_iter());
        let db = state_digest(b.into_iter());
        assert_eq!(da, db, "the order the node returns the pairs in does not matter");
        assert_eq!((da.keys, da.bytes), (2, 2 + 2 + 2 + 6));
        assert_eq!(da.sha256.len(), 64);
        // Moving a byte from a value to the next key changes the digest (lengths are hashed).
        let c: Vec<(&[u8], &[u8])> = vec![(&b"k1"[..], &b"v"[..]), (&b"1k2"[..], &b"value2"[..])];
        assert_ne!(state_digest(c.into_iter()).sha256, da.sha256);
        let empty = state_digest(std::iter::empty::<(&[u8], &[u8])>());
        assert_eq!((empty.keys, empty.bytes), (0, 0));
        assert_eq!(empty.sha256, sha256_hex(b""));
        let v = serde_json::to_value(&da).unwrap();
        assert_eq!(v["keys"], 2);
        assert!(v["sha256"].is_string());
    }

    #[test]
    fn access_probe_success_fails_the_upgrade_step() {
        let sc = parse_scenario(&with_step(r#"{"upgrade": "new.wasm", "migrate": {"method": "migrate"}}"#)).unwrap();
        let al = aliases();
        let probe = |status: &str, error: Option<&str>| AccessProbe {
            method: "migrate".into(),
            by: "probe.test.near".into(),
            status: status.into(),
            error: error.map(str::to_string),
        };

        let mut refused = StepResult::new(&sc.steps[0], Actual::success(Some(Value::Null)), &al);
        assert!(refused.passed);
        refused.apply_access_probe(probe("failure", Some("Smart contract panicked: Method migrate is private")));
        assert!(refused.passed, "a refused probe leaves the step as it was");
        assert!(refused.reason.is_none());
        let v = serde_json::to_value(&refused).unwrap();
        assert_eq!(v["access_probe"]["status"], "failure");
        assert!(v["access_probe"]["error"].as_str().unwrap().contains("is private"));
        assert!(v.get("state_before").is_none(), "absent fields are not serialized");

        let mut open = StepResult::new(&sc.steps[0], Actual::success(Some(Value::Null)), &al);
        open.apply_access_probe(probe("success", None));
        assert!(!open.passed, "a migration method anyone can call fails the upgrade step");
        assert!(open.reason.as_deref().unwrap().contains("access probe"));
        assert!(serde_json::to_value(&open).unwrap()["access_probe"].get("error").is_none());

        let mut not_run = StepResult::new(&sc.steps[0], Actual::success(Some(Value::Null)), &al);
        not_run.apply_access_probe(probe("not-run", Some("create account failed")));
        assert!(not_run.passed);

        // Without a probe nothing is added to the report.
        let plain = StepResult::new(&sc.steps[0], Actual::success(Some(Value::Null)), &al);
        assert!(serde_json::to_value(&plain).unwrap().get("access_probe").is_none());
    }
}
