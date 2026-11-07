//! Guarded jobs for agent swarms, v2. Same flow as v1 (`tests/fixtures/near/swarm_jobs`): an agent
//! joins the swarm with a budget, a poster posts a job with a reward, a registered agent submits the
//! sha256 of its wasm and of its scenario report, the reviewer runs `bumm-guard review-job` off-chain
//! and signs `approve` (the reward is transferred) or `reject`. What v2 changes, each a weakness listed
//! in docs/SWARM_JOBS.md of v1:
//!
//! - **Storage fee.** `post_job` takes the reward as an argument; the attached deposit must cover the
//!   reward plus the storage cost of the new job record (measured with `env::storage_usage()` before
//!   and after the insert, times `env::storage_byte_cost()`). The fee stays in the contract (it is not
//!   refunded by `cancel`), the excess over reward + fee goes back to the poster in the same call.
//!   An agent's first deposit pays the storage of its own record: that part of the budget cannot be
//!   withdrawn while the agent is registered; `leave_swarm` deletes the record and refunds the whole
//!   remaining budget once no job of the agent is under review. Finished jobs (`Paid`, `Cancelled`)
//!   are kept: they are the history `jobs_paid` and the reviewer's `gate_report_sha256` point to.
//! - **Per-job floor.** `submit` needs `budget >= 0.1 NEAR * (jobs_in_review + 1)`; `withdraw_budget`
//!   keeps `budget >= 0.1 NEAR * jobs_in_review` plus the storage part.
//! - **Bindings in `approve`.** The reviewer names the agent, the wasm hash and the report hash; all
//!   three must equal the current submission, so an approve printed for one submission cannot pay a
//!   later resubmission of the same wasm by another agent or with another report.
//! - **Poster-pinned acceptance.** `post_job` may carry `scenario_sha256`, the sha256 of the scenario
//!   file the poster accepts as proof. The contract only stores and shows it; `bumm-guard review-job`
//!   rejects a report whose scenarios do not all have that hash.
//! - **Assigned agent.** `post_job` may name `assigned_agent`; then only that account may `submit`
//!   (otherwise the first submitter owns the job until the reviewer acts).
//! - **Reject and close.** `reject(job_id, reason, close)`: with `close = true` the job is `Cancelled`
//!   and the reward goes back to the poster in the same transaction, so a rejected agent cannot keep a
//!   job locked by resubmitting before the poster's `cancel`.
//!
//! **State.** The root struct keeps the v1 shape (`reviewer`, `jobs`, `agents`, `agent_count`,
//! `next_id`), so a code-only upgrade from v1 still answers `job_count`, `agent_count`, `get_agent`
//! and `get_reviewer` (the smoke view lies) and panics with the map's own `Cannot deserialize
//! element` as soon as a job is read. Only the `Job` record changed (two new fields); `migrate`
//! rewrites every stored job into the v2 layout under a new prefix (see `migrate` for why) with
//! `reward_storage_fee = 0` and `scenario_sha256 = None`; agents and counters carry over untouched.
//! The agent's storage part is kept under its own raw key (`s` + account id), not as a root field, so
//! that the root layout stays the v1 one; an agent registered on v1 has no such key (storage part 0:
//! its record was paid by the contract).
//!
//! What this contract still does not do: it cannot verify a sandbox proof by itself; it trusts the
//! `reviewer` account. There is no inference and no agent runtime; the budget is only tracked. One
//! reviewer, no DAO. See docs/SWARM_JOBS.md, "Honest limits".
//!
//! Build: `cargo build --release --target wasm32-unknown-unknown -p swarm_jobs_v2` from
//! tests/fixtures/near (rustc 1.85.0, pinned there). Scenarios: scenarios/swarm_jobs_v2/ (fresh
//! deploy) and scenarios/upgrade/swarm_jobs_v1_to_v2_*.json (the upgrade from v1).

use near_sdk::borsh::{BorshDeserialize, BorshSerialize};
use near_sdk::store::{IterableMap, LookupMap};
use near_sdk::{env, near, require, AccountId, Gas, NearToken, PanicOnDefault, Promise};

const MAX_DESCRIPTION: usize = 1000;
const MAX_PROFILE: usize = 200;
const MAX_REASON: usize = 500;
const MAX_LIST: u64 = 100;
const CALLBACK_GAS: Gas = Gas::from_tgas(20);
/// Deposit required on an account's first `join_swarm` (0.1 NEAR), and the floor per job under review
/// (Submitted or Paying): `submit` needs `budget >= MIN_AGENT_DEPOSIT * (jobs_in_review + 1)` and
/// `withdraw_budget` keeps `budget >= MIN_AGENT_DEPOSIT * jobs_in_review` plus the storage part.
pub const MIN_AGENT_DEPOSIT: NearToken = NearToken::from_millinear(100);

/// Storage prefix of the v1 jobs map (`swarm_jobs` uses `b"j"`); `migrate` reads it from the old
/// state itself, the code needs it only in the tests.
#[cfg(test)]
const JOBS_PREFIX_V1: &[u8] = b"j";
/// Storage prefix of the v2 jobs map. Different from v1 on purpose, see `migrate`.
const JOBS_PREFIX: &[u8] = b"k";
/// Storage prefix of the agents map, the same as in v1: the `Agent` record did not change.
const AGENTS_PREFIX: &[u8] = b"a";
/// Prefix of the raw key `s` + account id that holds an agent's storage part (a Borsh `NearToken`).
const STORAGE_PART_PREFIX: &[u8] = b"s";

#[near(contract_state)]
#[derive(PanicOnDefault)]
pub struct SwarmJobs {
    reviewer: AccountId,
    jobs: IterableMap<u64, Job>,
    agents: LookupMap<AccountId, Agent>,
    agent_count: u64,
    next_id: u64,
}

#[near(serializers = [borsh])]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum JobStatus {
    /// Posted, nobody has submitted yet.
    Open,
    /// An agent submitted hashes; waiting for the reviewer.
    Submitted,
    /// The reviewer refused the submission; an agent may submit again.
    Rejected,
    /// Approved; the reward transfer is in flight (between `approve` and its callback).
    Paying,
    /// The reward reached the agent.
    Paid,
    /// The poster took the reward back.
    Cancelled,
}

impl JobStatus {
    fn as_str(self) -> &'static str {
        match self {
            JobStatus::Open => "Open",
            JobStatus::Submitted => "Submitted",
            JobStatus::Rejected => "Rejected",
            JobStatus::Paying => "Paying",
            JobStatus::Paid => "Paid",
            JobStatus::Cancelled => "Cancelled",
        }
    }
}

/// Stored record of one job (Borsh, inside `jobs`). The first ten fields are the v1 record.
#[near(serializers = [borsh])]
pub struct Job {
    poster: AccountId,
    description: String,
    target_account: Option<AccountId>,
    reward: NearToken,
    status: JobStatus,
    agent: Option<AccountId>,
    wasm_sha256: Option<String>,
    report_sha256: Option<String>,
    gate_report_sha256: Option<String>,
    reject_reason: Option<String>,
    /// Storage fee the poster paid for this record on `post_job` (kept by the contract); 0 for a job
    /// migrated from v1, whose record was paid by the contract.
    reward_storage_fee: NearToken,
    /// sha256 of the scenario file the poster accepts as proof (`None`: any scenario). Stored and
    /// shown only; `bumm-guard review-job` enforces it.
    scenario_sha256: Option<String>,
    /// The only account that may `submit` to this job (`None`: any registered agent); `None` for a
    /// job migrated from v1.
    assigned_agent: Option<AccountId>,
}

/// Stored record of one agent (Borsh, inside `agents`); unchanged from v1.
#[near(serializers = [borsh])]
pub struct Agent {
    profile: String,
    budget: NearToken,
    jobs_paid: u64,
    /// Jobs of this agent in status Submitted or Paying.
    jobs_in_review: u64,
}

/// What `get_job` / `list_jobs` return (JSON; amounts are yoctoNEAR strings). The v1 fields keep
/// their names; `reward_storage_fee` and `scenario_sha256` are new.
#[near(serializers = [json])]
pub struct JobView {
    pub id: u64,
    pub poster: AccountId,
    pub description: String,
    pub target_account: Option<AccountId>,
    pub reward: NearToken,
    pub status: String,
    pub agent: Option<AccountId>,
    pub wasm_sha256: Option<String>,
    pub report_sha256: Option<String>,
    pub gate_report_sha256: Option<String>,
    pub reject_reason: Option<String>,
    pub reward_storage_fee: NearToken,
    pub scenario_sha256: Option<String>,
    pub assigned_agent: Option<AccountId>,
}

/// What `get_agent` returns (JSON; amounts are yoctoNEAR strings). `storage_cost` is new: the part of
/// `budget` that cannot be withdrawn while the agent is registered (0 for an agent registered on v1).
#[near(serializers = [json])]
pub struct AgentView {
    pub account: AccountId,
    pub profile: String,
    pub budget: NearToken,
    pub jobs_paid: u64,
    pub jobs_in_review: u64,
    pub storage_cost: NearToken,
}

/// Layout of the v1 state (read by `migrate`). Same root fields; the old job record.
#[derive(BorshDeserialize, BorshSerialize)]
#[borsh(crate = "near_sdk::borsh")]
struct SwarmJobsV1 {
    reviewer: AccountId,
    jobs: IterableMap<u64, JobV1>,
    agents: LookupMap<AccountId, Agent>,
    agent_count: u64,
    next_id: u64,
}

#[derive(BorshDeserialize, BorshSerialize)]
#[borsh(crate = "near_sdk::borsh")]
struct JobV1 {
    poster: AccountId,
    description: String,
    target_account: Option<AccountId>,
    reward: NearToken,
    status: JobStatus,
    agent: Option<AccountId>,
    wasm_sha256: Option<String>,
    report_sha256: Option<String>,
    gate_report_sha256: Option<String>,
    reject_reason: Option<String>,
}

fn require_sha256(name: &str, value: &str) {
    require!(
        value.len() == 64 && value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        format!("{} must be 64 lowercase hex characters", name)
    );
}

fn zero() -> NearToken {
    NearToken::from_yoctonear(0)
}

#[near]
impl SwarmJobs {
    /// `reviewer` is the account that runs BUMM Guard review and signs `approve` / `reject`.
    #[init]
    pub fn new(reviewer: AccountId) -> Self {
        Self {
            reviewer,
            jobs: IterableMap::new(JOBS_PREFIX),
            agents: LookupMap::new(AGENTS_PREFIX),
            agent_count: 0,
            next_id: 0,
        }
    }

    /// Upgrade an already-deployed `swarm_jobs` v1 contract in place (keeps every job and agent).
    /// Call once, in the same batch as the deploy: `migrate` (contract account only).
    ///
    /// Every v1 job is re-inserted in the v2 layout with `reward_storage_fee = 0` and
    /// `scenario_sha256 = None`; status, agent and hashes carry over, so a `Submitted` job stays
    /// under review and the v2 `approve` pays it. The agents map and the counters are moved as they
    /// are (`Agent` did not change). Why a new prefix for the jobs: an `IterableMap` keeps its key
    /// vector, its values (each with its index into that vector) and a write-back cache under its
    /// prefix; re-inserting the new records into a map with the old prefix while the old map object
    /// still holds its own key vector and cache would let whichever map flushes last win. Building
    /// the new map under `k` and then clearing the old one under `j` keeps them independent and
    /// leaves no v1 bytes behind.
    #[private]
    #[init(ignore_state)]
    pub fn migrate() -> Self {
        let old: SwarmJobsV1 =
            env::state_read().unwrap_or_else(|| env::panic_str("No old state to migrate"));
        let SwarmJobsV1 { reviewer, mut jobs, agents, agent_count, next_id } = old;
        let mut new_jobs = IterableMap::new(JOBS_PREFIX);
        for (id, j) in jobs.iter() {
            new_jobs.insert(
                *id,
                Job {
                    poster: j.poster.clone(),
                    description: j.description.clone(),
                    target_account: j.target_account.clone(),
                    reward: j.reward,
                    status: j.status,
                    agent: j.agent.clone(),
                    wasm_sha256: j.wasm_sha256.clone(),
                    report_sha256: j.report_sha256.clone(),
                    gate_report_sha256: j.gate_report_sha256.clone(),
                    reject_reason: j.reject_reason.clone(),
                    reward_storage_fee: zero(),
                    scenario_sha256: None,
                    assigned_agent: None,
                },
            );
        }
        jobs.clear();
        Self { reviewer, jobs: new_jobs, agents, agent_count, next_id }
    }

    /// Registers the caller as an agent (or updates its profile). The attached deposit is added to
    /// its inference budget; the first join needs at least `MIN_AGENT_DEPOSIT` (0.1 NEAR), top-ups
    /// may be any amount. On the first join the storage cost of the new record is measured and
    /// recorded as the agent's storage part: it stays in the budget but cannot be withdrawn while the
    /// agent is registered (`leave_swarm` refunds it). A later, longer profile is not re-measured
    /// (at most 200 bytes, paid by the contract). Nothing here runs inference; the budget is only
    /// tracked.
    #[payable]
    pub fn join_swarm(&mut self, profile: String) {
        let caller = env::predecessor_account_id();
        require!(caller != self.reviewer, "The reviewer cannot join the swarm as an agent");
        require!(profile.len() <= MAX_PROFILE, "Profile is too long (max 200 bytes)");
        let deposit = env::attached_deposit();
        match self.agents.get_mut(&caller) {
            Some(a) => {
                a.profile = profile;
                a.budget = a.budget.saturating_add(deposit);
            }
            None => {
                require!(
                    deposit >= MIN_AGENT_DEPOSIT,
                    "Attach at least 0.1 NEAR to join the swarm (minimum agent deposit)"
                );
                let before = env::storage_usage();
                self.agents.insert(
                    caller.clone(),
                    Agent { profile, budget: deposit, jobs_paid: 0, jobs_in_review: 0 },
                );
                self.agents.flush();
                // A placeholder of the final size, so the storage-part entry counts itself.
                Self::write_storage_part(&caller, zero());
                let cost = Self::storage_cost_since(before);
                Self::write_storage_part(&caller, cost);
                self.agent_count += 1;
            }
        }
    }

    /// The agent takes `amount` (yoctoNEAR string) of its own budget back. What stays must be at
    /// least `MIN_AGENT_DEPOSIT` per job of the agent under review (Submitted or Paying) plus the
    /// agent's storage part.
    pub fn withdraw_budget(&mut self, amount: NearToken) -> Promise {
        let caller = env::predecessor_account_id();
        require!(!amount.is_zero(), "Amount must be positive");
        let storage_part = Self::storage_part(&caller);
        let agent = self
            .agents
            .get_mut(&caller)
            .unwrap_or_else(|| env::panic_str("Not a registered agent"));
        require!(amount <= agent.budget, "Amount exceeds the agent's budget");
        let left = agent.budget.saturating_sub(amount);
        let floor = Self::budget_floor(agent.jobs_in_review).saturating_add(storage_part);
        require!(
            left >= floor,
            format!(
                "Budget must stay at least 0.1 NEAR per job under review plus the storage part ({} \
                 yoctoNEAR with {} job(s) under review)",
                floor, agent.jobs_in_review
            )
        );
        agent.budget = left;
        Promise::new(caller).transfer(amount)
    }

    /// The agent leaves the swarm: its record is deleted (the storage it used is freed) and its whole
    /// remaining budget, storage part included, is transferred back. Refused while the agent has a
    /// job in Submitted or Paying. Returns the refunded amount. Finished jobs that name the agent
    /// (`Paid`, `Cancelled`, `Rejected`) are kept; `jobs_paid` starts again at 0 on a later join.
    pub fn leave_swarm(&mut self) -> NearToken {
        let caller = env::predecessor_account_id();
        let agent = self
            .agents
            .get(&caller)
            .unwrap_or_else(|| env::panic_str("Not a registered agent"));
        require!(
            agent.jobs_in_review == 0,
            format!(
                "Cannot leave the swarm with {} job(s) under review (Submitted or Paying)",
                agent.jobs_in_review
            )
        );
        let budget = agent.budget;
        self.agents.remove(&caller);
        env::storage_remove(&Self::storage_part_key(&caller));
        self.agent_count = self.agent_count.saturating_sub(1);
        if !budget.is_zero() {
            Promise::new(caller).transfer(budget);
        }
        budget
    }

    /// Posts a job with `reward` (yoctoNEAR string, > 0). The attached deposit must be at least the
    /// reward plus the storage fee of the new record (measured here: bytes added times
    /// `storage_byte_cost`); the fee stays in the contract, the excess goes back to the poster in this
    /// call. `scenario_sha256`, when given, is the sha256 of the one scenario file the poster accepts
    /// as proof (64 lowercase hex); `assigned_agent`, when given, is the only account that may
    /// `submit`. Returns the job id.
    #[payable]
    pub fn post_job(
        &mut self,
        description: String,
        target_account: Option<AccountId>,
        reward: NearToken,
        scenario_sha256: Option<String>,
        assigned_agent: Option<AccountId>,
    ) -> u64 {
        let deposit = env::attached_deposit();
        require!(!reward.is_zero(), "Reward must be positive");
        require!(description.len() <= MAX_DESCRIPTION, "Description is too long (max 1000 bytes)");
        if let Some(s) = &scenario_sha256 {
            require_sha256("scenario_sha256", s);
        }
        let poster = env::predecessor_account_id();
        let id = self.next_id;
        self.next_id += 1;
        let before = env::storage_usage();
        self.jobs.insert(
            id,
            Job {
                poster: poster.clone(),
                description,
                target_account,
                reward,
                status: JobStatus::Open,
                agent: None,
                wasm_sha256: None,
                report_sha256: None,
                gate_report_sha256: None,
                reject_reason: None,
                reward_storage_fee: zero(), // placeholder of the final size (16 bytes)
                scenario_sha256,
                assigned_agent,
            },
        );
        self.jobs.flush();
        let fee = Self::storage_cost_since(before);
        let needed = reward.saturating_add(fee);
        require!(
            deposit >= needed,
            format!(
                "Deposit {} yoctoNEAR is less than the reward plus the storage fee ({} + {} = {})",
                deposit, reward, fee, needed
            )
        );
        self.job_mut(id).reward_storage_fee = fee;
        let excess = deposit.saturating_sub(needed);
        if !excess.is_zero() {
            Promise::new(poster).transfer(excess);
        }
        id
    }

    /// A registered agent submits its work as two hashes: the wasm it built and the scenario
    /// report (`bumm-guard simulate`) that proves it. Allowed while the job is Open or Rejected, by
    /// the job's `assigned_agent` if it has one, and only if the agent's budget covers
    /// `MIN_AGENT_DEPOSIT` for every job it would then have under review plus its storage part.
    pub fn submit(&mut self, job_id: u64, wasm_sha256: String, report_sha256: String) {
        let caller = env::predecessor_account_id();
        let (budget, in_review) = match self.agents.get(&caller) {
            Some(a) => (a.budget, a.jobs_in_review),
            None => env::panic_str("Only a registered agent can submit; call join_swarm first"),
        };
        let needed = Self::budget_floor(in_review + 1).saturating_add(Self::storage_part(&caller));
        require!(
            budget >= needed,
            format!(
                "Budget must be at least 0.1 NEAR per job under review plus the storage part: {} job(s) \
                 would need {} yoctoNEAR, the budget is {}",
                in_review + 1,
                needed,
                budget
            )
        );
        require_sha256("wasm_sha256", &wasm_sha256);
        require_sha256("report_sha256", &report_sha256);
        let job = self.job_mut(job_id);
        if let Some(assigned) = &job.assigned_agent {
            require!(
                assigned == &caller,
                format!("Job {} is assigned to {}; only that agent may submit", job_id, assigned)
            );
        }
        require!(
            job.status == JobStatus::Open || job.status == JobStatus::Rejected,
            format!("Job {} is not open for submissions (status {})", job_id, job.status.as_str())
        );
        job.agent = Some(caller.clone());
        job.wasm_sha256 = Some(wasm_sha256);
        job.report_sha256 = Some(report_sha256);
        job.reject_reason = None;
        job.status = JobStatus::Submitted;
        self.review_count(&caller, true);
    }

    /// Reviewer only: the gate passed for exactly this submission. `agent`, `wasm_sha256` and
    /// `report_sha256` must all equal the job's current submission; `gate_report_sha256` is the
    /// sha256 of the review report JSON. The job is `Paying` until the transfer's callback marks it
    /// `Paid` (or, if the transfer failed, `Submitted` again with the reward still held here).
    pub fn approve(
        &mut self,
        job_id: u64,
        agent: AccountId,
        wasm_sha256: String,
        report_sha256: String,
        gate_report_sha256: String,
    ) -> Promise {
        require!(env::predecessor_account_id() == self.reviewer, "Only the reviewer can approve");
        require_sha256("gate_report_sha256", &gate_report_sha256);
        let job = self.job_mut(job_id);
        require!(
            job.status == JobStatus::Submitted,
            format!("Job {} is not Submitted (status {})", job_id, job.status.as_str())
        );
        require!(
            job.agent.as_ref() == Some(&agent),
            format!("agent does not match the submission of job {}", job_id)
        );
        require!(
            job.wasm_sha256.as_deref() == Some(wasm_sha256.as_str()),
            format!("wasm_sha256 does not match the submission of job {}", job_id)
        );
        require!(
            job.report_sha256.as_deref() == Some(report_sha256.as_str()),
            format!("report_sha256 does not match the submission of job {}", job_id)
        );
        job.gate_report_sha256 = Some(gate_report_sha256);
        job.status = JobStatus::Paying;
        let reward = job.reward;
        Promise::new(agent)
            .transfer(reward)
            .then(Self::ext(env::current_account_id()).with_static_gas(CALLBACK_GAS).on_reward_paid(job_id))
    }

    /// Callback of `approve`'s transfer (this contract only). Success: `Paid`. Failure: the reward
    /// came back to this contract, the job returns to `Submitted` and the gate report hash is cleared.
    #[private]
    pub fn on_reward_paid(&mut self, job_id: u64) -> bool {
        let ok = near_sdk::is_promise_success();
        let job = self.job_mut(job_id);
        if job.status != JobStatus::Paying {
            env::panic_str("Job is not Paying");
        }
        if ok {
            job.status = JobStatus::Paid;
            let agent = job.agent.clone();
            if let Some(a) = agent.and_then(|acc| self.agents.get_mut(&acc)) {
                a.jobs_paid += 1;
                a.jobs_in_review = a.jobs_in_review.saturating_sub(1);
            }
        } else {
            job.status = JobStatus::Submitted;
            job.gate_report_sha256 = None;
            env::log_str(&format!("reward transfer for job {} failed; job is Submitted again", job_id));
        }
        ok
    }

    /// Reviewer only: the gate blocked. The reason is stored. With `close = false` the job is
    /// `Rejected` and an agent may submit again; with `close = true` it is `Cancelled` and the reward
    /// goes back to the poster in this transaction (the storage fee stays), so a rejected agent cannot
    /// keep the job locked by resubmitting before the poster's `cancel`.
    pub fn reject(&mut self, job_id: u64, reason: String, close: bool) {
        require!(env::predecessor_account_id() == self.reviewer, "Only the reviewer can reject");
        require!(reason.len() <= MAX_REASON, "Reason is too long (max 500 bytes)");
        let job = self.job_mut(job_id);
        require!(
            job.status == JobStatus::Submitted,
            format!("Job {} is not Submitted (status {})", job_id, job.status.as_str())
        );
        job.reject_reason = Some(reason);
        job.status = if close { JobStatus::Cancelled } else { JobStatus::Rejected };
        let (agent, poster, reward) = (job.agent.clone(), job.poster.clone(), job.reward);
        if let Some(agent) = agent {
            self.review_count(&agent, false);
        }
        if close {
            Promise::new(poster).transfer(reward);
        }
    }

    /// Poster only, while the job is Open or Rejected: the reward goes back to the poster. The
    /// storage fee is not refunded; the record stays.
    pub fn cancel(&mut self, job_id: u64) -> Promise {
        let caller = env::predecessor_account_id();
        let job = self.job_mut(job_id);
        require!(job.poster == caller, format!("Only the poster can cancel job {}", job_id));
        require!(
            job.status == JobStatus::Open || job.status == JobStatus::Rejected,
            format!("Job {} cannot be cancelled (status {})", job_id, job.status.as_str())
        );
        job.status = JobStatus::Cancelled;
        Promise::new(caller).transfer(job.reward)
    }

    pub fn get_job(&self, id: u64) -> Option<JobView> {
        self.jobs.get(&id).map(|j| Self::view(id, j))
    }

    /// Jobs with ids `from_index ..` (default 0), at most `limit` (default and maximum 100).
    pub fn list_jobs(&self, from_index: Option<u64>, limit: Option<u64>) -> Vec<JobView> {
        let from = from_index.unwrap_or(0);
        let limit = limit.unwrap_or(MAX_LIST).min(MAX_LIST);
        (from..self.next_id)
            .filter_map(|id| self.jobs.get(&id).map(|j| Self::view(id, j)))
            .take(limit as usize)
            .collect()
    }

    pub fn job_count(&self) -> u64 {
        self.next_id
    }

    pub fn get_agent(&self, account: AccountId) -> Option<AgentView> {
        self.agents.get(&account).map(|a| AgentView {
            account: account.clone(),
            profile: a.profile.clone(),
            budget: a.budget,
            jobs_paid: a.jobs_paid,
            jobs_in_review: a.jobs_in_review,
            storage_cost: Self::storage_part(&account),
        })
    }

    pub fn agent_count(&self) -> u64 {
        self.agent_count
    }

    pub fn get_reviewer(&self) -> AccountId {
        self.reviewer.clone()
    }

    /// `MIN_AGENT_DEPOSIT` per job under review.
    fn budget_floor(jobs_in_review: u64) -> NearToken {
        NearToken::from_yoctonear(MIN_AGENT_DEPOSIT.as_yoctonear().saturating_mul(u128::from(jobs_in_review)))
    }

    /// Cost of the storage added since `before` (bytes times `storage_byte_cost`).
    fn storage_cost_since(before: u64) -> NearToken {
        let added = env::storage_usage().saturating_sub(before);
        NearToken::from_yoctonear(u128::from(added).saturating_mul(env::storage_byte_cost().as_yoctonear()))
    }

    fn storage_part_key(account: &AccountId) -> Vec<u8> {
        let mut key = STORAGE_PART_PREFIX.to_vec();
        key.extend_from_slice(account.as_bytes());
        key
    }

    /// The agent's storage part; 0 when no key is stored (an agent registered on v1).
    fn storage_part(account: &AccountId) -> NearToken {
        env::storage_read(&Self::storage_part_key(account))
            .and_then(|bytes| NearToken::try_from_slice(&bytes).ok())
            .unwrap_or_else(zero)
    }

    fn write_storage_part(account: &AccountId, value: NearToken) {
        let bytes = near_sdk::borsh::to_vec(&value).unwrap_or_else(|_| env::panic_str("Cannot serialize"));
        env::storage_write(&Self::storage_part_key(account), &bytes);
    }

    /// One more (`up`) or one fewer job of `agent` in Submitted/Paying.
    fn review_count(&mut self, agent: &AccountId, up: bool) {
        if let Some(a) = self.agents.get_mut(agent) {
            a.jobs_in_review = if up { a.jobs_in_review + 1 } else { a.jobs_in_review.saturating_sub(1) };
        }
    }

    fn job_mut(&mut self, id: u64) -> &mut Job {
        self.jobs
            .get_mut(&id)
            .unwrap_or_else(|| env::panic_str(&format!("Job {} does not exist", id)))
    }

    fn view(id: u64, j: &Job) -> JobView {
        JobView {
            id,
            poster: j.poster.clone(),
            description: j.description.clone(),
            target_account: j.target_account.clone(),
            reward: j.reward,
            status: j.status.as_str().to_string(),
            agent: j.agent.clone(),
            wasm_sha256: j.wasm_sha256.clone(),
            report_sha256: j.report_sha256.clone(),
            gate_report_sha256: j.gate_report_sha256.clone(),
            reject_reason: j.reject_reason.clone(),
            reward_storage_fee: j.reward_storage_fee,
            scenario_sha256: j.scenario_sha256.clone(),
            assigned_agent: j.assigned_agent.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use near_sdk::test_utils::VMContextBuilder;
    use near_sdk::{testing_env, PromiseResult};

    const WASM: &str = "13425a042dec55eec364240e890a1645c7332f2898149353ebcb5830a5886013";
    const OTHER: &str = "ecd8a805ea3252f1ffa2a285964d3e035d6e3c6be16e6974c33c479c3e034443";
    const REPORT: &str = "f305461fe47aa2d15d2361038b7ed010be51b2c53219fee1352eac59a24eb420";
    const REPORT2: &str = "e7cfcfd8712d2dc31c1fb2c951894b367da9dc34a93376b45e1f61e249ac55c5";
    const GATE: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const SCENARIO: &str = "7e7d87ce7e6b46feeb6ac43ac345dd2f1c906c6b63c683e12f322f72871fe0bd";

    fn ctx(name: &str, deposit: NearToken) -> VMContextBuilder {
        let mut b = VMContextBuilder::new();
        b.current_account_id("jobs.near".parse().unwrap())
            .predecessor_account_id(name.parse().unwrap())
            .attached_deposit(deposit);
        b
    }

    fn as_account(name: &str, deposit: NearToken) {
        testing_env!(ctx(name, deposit).build());
    }

    fn acc(name: &str) -> AccountId {
        name.parse().unwrap()
    }

    fn near(n: u128) -> NearToken {
        NearToken::from_near(n)
    }

    fn milli(n: u128) -> NearToken {
        NearToken::from_millinear(n)
    }

    /// reviewer bob, agent alice (budget 1 NEAR), job 0 by poster carol (reward 2 NEAR, 3 NEAR
    /// attached: the fee is taken, the rest refunded).
    fn setup() -> SwarmJobs {
        as_account("owner.near", zero());
        let mut c = SwarmJobs::new(acc("bob.near"));
        as_account("alice.near", near(1));
        c.join_swarm("rust agent".into());
        as_account("carol.near", near(3));
        assert_eq!(c.post_job("upgrade escrow".into(), Some(acc("escrow.near")), near(2), None, None), 0);
        c
    }

    fn submitted() -> SwarmJobs {
        let mut c = setup();
        as_account("alice.near", zero());
        c.submit(0, WASM.into(), REPORT.into());
        c
    }

    fn approve(c: &mut SwarmJobs, job: u64) {
        as_account("bob.near", zero());
        c.approve(job, acc("alice.near"), WASM.into(), REPORT.into(), GATE.into());
    }

    fn callback(c: &mut SwarmJobs, job: u64, result: PromiseResult) -> bool {
        testing_env!(
            ctx("jobs.near", zero()).build(),
            near_sdk::test_vm_config(),
            near_sdk::RuntimeFeesConfig::test(),
            Default::default(),
            vec![result]
        );
        c.on_reward_paid(job)
    }

    fn status(c: &SwarmJobs, job: u64) -> String {
        c.get_job(job).unwrap().status
    }

    fn agent(c: &SwarmJobs, name: &str) -> AgentView {
        c.get_agent(acc(name)).unwrap()
    }

    // --- v1 behaviour kept ---------------------------------------------------------------------

    #[test]
    fn happy_path_pays_the_agent() {
        let mut c = submitted();
        assert_eq!(status(&c, 0), "Submitted");
        approve(&mut c, 0);
        assert_eq!(status(&c, 0), "Paying");
        assert!(callback(&mut c, 0, PromiseResult::Successful(vec![])));
        let j = c.get_job(0).unwrap();
        assert_eq!(j.status, "Paid");
        assert_eq!(j.agent, Some(acc("alice.near")));
        assert_eq!(j.gate_report_sha256.as_deref(), Some(GATE));
        assert_eq!(j.reward, near(2));
        let a = agent(&c, "alice.near");
        assert_eq!((a.jobs_paid, a.jobs_in_review), (1, 0));
    }

    #[test]
    fn failed_transfer_does_not_leave_the_job_paid() {
        let mut c = submitted();
        approve(&mut c, 0);
        assert!(!callback(&mut c, 0, PromiseResult::Failed));
        let j = c.get_job(0).unwrap();
        assert_eq!(j.status, "Submitted");
        assert_eq!(j.gate_report_sha256, None);
        assert_eq!(agent(&c, "alice.near").jobs_paid, 0);
        assert_eq!(agent(&c, "alice.near").jobs_in_review, 1);
        // The reviewer can approve again.
        approve(&mut c, 0);
        assert!(callback(&mut c, 0, PromiseResult::Successful(vec![])));
        assert_eq!(status(&c, 0), "Paid");
    }

    #[test]
    #[should_panic(expected = "Only the reviewer can approve")]
    fn approve_by_non_reviewer_refused() {
        let mut c = submitted();
        as_account("carol.near", zero());
        c.approve(0, acc("alice.near"), WASM.into(), REPORT.into(), GATE.into());
    }

    #[test]
    #[should_panic(expected = "wasm_sha256 does not match the submission of job 0")]
    fn approve_with_other_wasm_hash_refused() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.approve(0, acc("alice.near"), OTHER.into(), REPORT.into(), GATE.into());
    }

    #[test]
    #[should_panic(expected = "Only a registered agent can submit")]
    fn submit_by_unregistered_agent_refused() {
        let mut c = setup();
        as_account("dave.near", zero());
        c.submit(0, WASM.into(), REPORT.into());
    }

    #[test]
    #[should_panic(expected = "Job 0 is not Submitted (status Paid)")]
    fn double_approve_refused() {
        let mut c = submitted();
        approve(&mut c, 0);
        callback(&mut c, 0, PromiseResult::Successful(vec![]));
        approve(&mut c, 0);
    }

    #[test]
    #[should_panic(expected = "Job 0 is not Submitted (status Paying)")]
    fn approve_while_paying_refused() {
        let mut c = submitted();
        approve(&mut c, 0);
        approve(&mut c, 0);
    }

    #[test]
    #[should_panic(expected = "Only the poster can cancel job 0")]
    fn cancel_by_non_poster_refused() {
        let mut c = setup();
        as_account("alice.near", zero());
        c.cancel(0);
    }

    #[test]
    #[should_panic(expected = "Job 0 cannot be cancelled (status Submitted)")]
    fn cancel_after_submit_refused() {
        let mut c = submitted();
        as_account("carol.near", zero());
        c.cancel(0);
    }

    #[test]
    fn cancel_while_open_refunds_the_reward_and_keeps_the_fee() {
        let mut c = setup();
        as_account("carol.near", zero());
        c.cancel(0);
        let j = c.get_job(0).unwrap();
        assert_eq!(j.status, "Cancelled");
        assert!(!j.reward_storage_fee.is_zero()); // the record and its fee stay
    }

    #[test]
    fn reject_then_resubmit_then_approve() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.reject(0, "no upgrade proof for this pair".into(), false);
        let j = c.get_job(0).unwrap();
        assert_eq!(j.status, "Rejected");
        assert_eq!(j.reject_reason.as_deref(), Some("no upgrade proof for this pair"));
        as_account("alice.near", zero());
        c.submit(0, WASM.into(), REPORT.into());
        assert_eq!(c.get_job(0).unwrap().reject_reason, None);
        approve(&mut c, 0);
        assert!(callback(&mut c, 0, PromiseResult::Successful(vec![])));
        assert_eq!(status(&c, 0), "Paid");
    }

    #[test]
    #[should_panic(expected = "Only the reviewer can reject")]
    fn reject_by_non_reviewer_refused() {
        let mut c = submitted();
        as_account("alice.near", zero());
        c.reject(0, "mine".into(), false);
    }

    #[test]
    #[should_panic(expected = "Job 0 is not open for submissions (status Submitted)")]
    fn submit_twice_refused() {
        let mut c = submitted();
        as_account("alice.near", zero());
        c.submit(0, WASM.into(), REPORT.into());
    }

    #[test]
    #[should_panic(expected = "wasm_sha256 must be 64 lowercase hex characters")]
    fn submit_with_uppercase_hash_refused() {
        let mut c = setup();
        as_account("alice.near", zero());
        c.submit(0, WASM.to_uppercase(), REPORT.into());
    }

    #[test]
    #[should_panic(expected = "The reviewer cannot join the swarm as an agent")]
    fn reviewer_cannot_be_an_agent() {
        let mut c = setup();
        as_account("bob.near", near(1));
        c.join_swarm("reviewer".into());
    }

    #[test]
    #[should_panic(expected = "Job is not Paying")]
    fn callback_without_approve_refused() {
        let mut c = submitted();
        callback(&mut c, 0, PromiseResult::Successful(vec![]));
    }

    #[test]
    #[should_panic(expected = "Attach at least 0.1 NEAR to join the swarm")]
    fn first_join_below_minimum_refused() {
        let mut c = setup();
        as_account("dave.near", milli(99));
        c.join_swarm("almost".into());
    }

    #[test]
    fn views_list_and_count() {
        let mut c = setup();
        as_account("carol.near", near(2));
        c.post_job("second".into(), None, near(1), Some(SCENARIO.into()), None);
        assert_eq!(c.job_count(), 2);
        assert_eq!(c.get_reviewer(), acc("bob.near"));
        let all = c.list_jobs(None, None);
        assert_eq!(all.iter().map(|j| j.id).collect::<Vec<_>>(), vec![0, 1]);
        assert_eq!(c.list_jobs(Some(1), Some(5)).len(), 1);
        assert!(c.get_job(2).is_none());
        assert!(c.get_agent(acc("dave.near")).is_none());
    }

    // --- storage fee on post_job -----------------------------------------------------------------

    #[test]
    fn post_job_takes_the_storage_fee_and_records_it() {
        let c = setup();
        let j = c.get_job(0).unwrap();
        assert_eq!(j.reward, near(2));
        assert_eq!(j.scenario_sha256, None);
        let fee = j.reward_storage_fee;
        // A record of a few hundred bytes at 1e19 yocto per byte: between 0.001 and 0.01 NEAR.
        assert!(fee > milli(1) && fee < milli(10), "fee {}", fee);
        assert_eq!(fee.as_yoctonear() % env::storage_byte_cost().as_yoctonear(), 0);
    }

    #[test]
    fn longer_description_costs_a_higher_fee() {
        let mut c = setup();
        as_account("carol.near", near(3));
        c.post_job("x".repeat(1000), Some(acc("escrow.near")), near(2), None, None);
        let short = c.get_job(0).unwrap().reward_storage_fee;
        let long = c.get_job(1).unwrap().reward_storage_fee;
        assert!(long > short, "{} > {}", long, short);
        // Same poster, target and key size: the difference is exactly the 1000 - 14 description bytes.
        let diff = long.as_yoctonear() - short.as_yoctonear();
        assert_eq!(diff, (1000 - "upgrade escrow".len()) as u128 * env::storage_byte_cost().as_yoctonear());
    }

    #[test]
    #[should_panic(expected = "is less than the reward plus the storage fee")]
    fn post_job_with_exactly_the_reward_refused() {
        let mut c = setup();
        as_account("carol.near", near(1));
        c.post_job("no fee attached".into(), None, near(1), None, None);
    }

    #[test]
    #[should_panic(expected = "is less than the reward plus the storage fee")]
    fn post_job_with_less_than_the_reward_refused() {
        let mut c = setup();
        as_account("carol.near", milli(500));
        c.post_job("underfunded".into(), None, near(1), None, None);
    }

    #[test]
    #[should_panic(expected = "Reward must be positive")]
    fn post_job_without_reward_refused() {
        let mut c = setup();
        as_account("carol.near", near(1));
        c.post_job("free work".into(), None, zero(), None, None);
    }

    #[test]
    fn post_job_with_the_exact_amount_is_accepted() {
        let mut c = setup();
        let fee = c.get_job(0).unwrap().reward_storage_fee;
        // The same description, target and poster give the same record size, so the same fee.
        as_account("carol.near", near(2).saturating_add(fee));
        assert_eq!(c.post_job("upgrade escrow".into(), Some(acc("escrow.near")), near(2), None, None), 1);
        assert_eq!(c.get_job(1).unwrap().reward_storage_fee, fee);
    }

    // --- poster-pinned acceptance scenario ------------------------------------------------------

    #[test]
    fn scenario_sha256_is_stored_and_shown() {
        let mut c = setup();
        as_account("carol.near", near(2));
        c.post_job("pinned".into(), None, near(1), Some(SCENARIO.into()), None);
        assert_eq!(c.get_job(1).unwrap().scenario_sha256.as_deref(), Some(SCENARIO));
        assert_eq!(c.list_jobs(Some(1), None)[0].scenario_sha256.as_deref(), Some(SCENARIO));
    }

    #[test]
    #[should_panic(expected = "scenario_sha256 must be 64 lowercase hex characters")]
    fn scenario_sha256_must_be_lowercase_hex() {
        let mut c = setup();
        as_account("carol.near", near(2));
        c.post_job("pinned".into(), None, near(1), Some(SCENARIO.to_uppercase()), None);
    }

    #[test]
    #[should_panic(expected = "scenario_sha256 must be 64 lowercase hex characters")]
    fn scenario_sha256_must_be_64_characters() {
        let mut c = setup();
        as_account("carol.near", near(2));
        c.post_job("pinned".into(), None, near(1), Some(SCENARIO[..63].into()), None);
    }

    // --- approve binds agent, wasm and report ---------------------------------------------------

    #[test]
    #[should_panic(expected = "agent does not match the submission of job 0")]
    fn approve_with_other_agent_refused() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.approve(0, acc("dave.near"), WASM.into(), REPORT.into(), GATE.into());
    }

    #[test]
    #[should_panic(expected = "report_sha256 does not match the submission of job 0")]
    fn approve_with_other_report_hash_refused() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.approve(0, acc("alice.near"), WASM.into(), REPORT2.into(), GATE.into());
    }

    #[test]
    #[should_panic(expected = "agent does not match the submission of job 0")]
    fn approve_printed_for_one_submission_does_not_pay_a_resubmission_by_another_agent() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.reject(0, "blocked".into(), false);
        as_account("dave.near", milli(200));
        c.join_swarm("copycat".into());
        as_account("dave.near", zero());
        c.submit(0, WASM.into(), REPORT.into()); // same hashes, other agent
        approve(&mut c, 0); // the approve for alice
    }

    // --- per-job floor ---------------------------------------------------------------------------

    #[test]
    #[should_panic(expected = "Budget must be at least 0.1 NEAR per job under review plus the storage part: 2 job(s) would need")]
    fn second_submit_with_one_floor_of_budget_refused() {
        let mut c = setup();
        as_account("carol.near", near(2));
        c.post_job("second".into(), None, near(1), None, None);
        as_account("dave.near", milli(150));
        c.join_swarm("one job at a time".into());
        as_account("dave.near", zero());
        c.submit(0, WASM.into(), REPORT.into()); // 0.15 >= 0.1 * 1
        c.submit(1, WASM.into(), REPORT2.into()); // 0.15 < 0.1 * 2
    }

    #[test]
    fn budget_of_two_floors_allows_two_jobs_under_review() {
        let mut c = setup();
        as_account("carol.near", near(2));
        c.post_job("second".into(), None, near(1), None, None);
        as_account("dave.near", milli(210)); // 0.2 NEAR for two jobs plus the storage part
        c.join_swarm("two jobs".into());
        as_account("dave.near", zero());
        c.submit(0, WASM.into(), REPORT.into());
        c.submit(1, WASM.into(), REPORT2.into());
        assert_eq!(agent(&c, "dave.near").jobs_in_review, 2);
    }

    #[test]
    #[should_panic(expected = "Budget must stay at least 0.1 NEAR per job under review plus the storage part")]
    fn withdraw_below_the_floor_while_submitted_refused() {
        let mut c = submitted();
        assert_eq!(agent(&c, "alice.near").jobs_in_review, 1);
        as_account("alice.near", zero());
        c.withdraw_budget(milli(900)); // 0.1 left: below 0.1 + the storage part
    }

    #[test]
    #[should_panic(expected = "Budget must stay at least 0.1 NEAR per job under review plus the storage part")]
    fn withdraw_below_the_floor_while_paying_refused() {
        let mut c = submitted();
        approve(&mut c, 0);
        as_account("alice.near", zero());
        c.withdraw_budget(near(1));
    }

    #[test]
    #[should_panic(expected = "Budget must stay at least 0.1 NEAR per job under review plus the storage part")]
    fn withdraw_of_the_storage_part_refused_while_registered() {
        let mut c = setup();
        as_account("alice.near", zero());
        c.withdraw_budget(near(1)); // nothing under review, but the storage part must stay
    }

    #[test]
    fn withdraw_floor_follows_the_jobs_under_review() {
        let mut c = setup();
        as_account("carol.near", near(2));
        c.post_job("second".into(), None, near(1), None, None);
        let part = agent(&c, "alice.near").storage_cost;
        assert!(part > milli(1) && part < milli(10), "storage part {}", part);
        as_account("alice.near", zero());
        c.submit(0, WASM.into(), REPORT.into());
        c.submit(1, WASM.into(), REPORT2.into());
        // two jobs under review: 0.2 NEAR + the storage part must stay
        c.withdraw_budget(milli(800).saturating_sub(part));
        assert_eq!(agent(&c, "alice.near").budget, milli(200).saturating_add(part));
        as_account("bob.near", zero());
        c.reject(1, "blocked".into(), false);
        as_account("alice.near", zero());
        c.withdraw_budget(milli(100)); // one job under review: 0.1 + the part may stay
        assert_eq!(agent(&c, "alice.near").budget, milli(100).saturating_add(part));
        approve(&mut c, 0);
        assert!(callback(&mut c, 0, PromiseResult::Successful(vec![])));
        as_account("alice.near", zero());
        c.withdraw_budget(milli(100)); // nothing under review: only the storage part stays
        assert_eq!(agent(&c, "alice.near").budget, part);
    }

    #[test]
    #[should_panic(expected = "Amount exceeds the agent's budget")]
    fn withdraw_more_than_budget_refused() {
        let mut c = setup();
        as_account("alice.near", zero());
        c.withdraw_budget(near(2));
    }

    #[test]
    fn first_join_at_minimum_and_free_top_up_allowed() {
        let mut c = setup();
        as_account("dave.near", MIN_AGENT_DEPOSIT);
        c.join_swarm("minimal".into());
        as_account("dave.near", zero());
        c.join_swarm("renamed".into());
        let a = agent(&c, "dave.near");
        assert_eq!((a.profile.as_str(), a.budget), ("renamed", MIN_AGENT_DEPOSIT));
        assert!(!a.storage_cost.is_zero());
        assert_eq!(c.agent_count(), 2);
    }

    // --- leave_swarm ------------------------------------------------------------------------------

    #[test]
    #[should_panic(expected = "Cannot leave the swarm with 1 job(s) under review (Submitted or Paying)")]
    fn leave_while_submitted_refused() {
        let mut c = submitted();
        as_account("alice.near", zero());
        c.leave_swarm();
    }

    #[test]
    #[should_panic(expected = "Cannot leave the swarm with 1 job(s) under review (Submitted or Paying)")]
    fn leave_while_paying_refused() {
        let mut c = submitted();
        approve(&mut c, 0);
        as_account("alice.near", zero());
        c.leave_swarm();
    }

    #[test]
    #[should_panic(expected = "Not a registered agent")]
    fn leave_by_non_agent_refused() {
        let mut c = setup();
        as_account("carol.near", zero());
        c.leave_swarm();
    }

    #[test]
    fn leave_after_paid_refunds_the_whole_budget_and_frees_the_record() {
        let mut c = submitted();
        approve(&mut c, 0);
        assert!(callback(&mut c, 0, PromiseResult::Successful(vec![])));
        as_account("alice.near", zero());
        let before = env::storage_usage();
        assert_eq!(c.leave_swarm(), near(1)); // the storage part included
        assert!(env::storage_usage() < before, "the agent record and its storage key are gone");
        assert!(c.get_agent(acc("alice.near")).is_none());
        assert_eq!(c.agent_count(), 0);
        assert_eq!(c.get_job(0).unwrap().status, "Paid"); // the finished job is kept
        assert_eq!(c.get_job(0).unwrap().agent, Some(acc("alice.near")));
        // Rejoining starts over: the minimum applies again and jobs_paid is 0.
        as_account("alice.near", milli(100));
        c.join_swarm("back".into());
        let a = agent(&c, "alice.near");
        assert_eq!((a.jobs_paid, a.budget), (0, milli(100)));
        assert!(!a.storage_cost.is_zero());
    }

    #[test]
    fn leave_after_reject_is_allowed() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.reject(0, "blocked".into(), false);
        as_account("alice.near", zero());
        assert_eq!(c.leave_swarm(), near(1));
        assert_eq!(c.get_job(0).unwrap().status, "Rejected");
    }

    // --- submit floor includes the storage part --------------------------------------------------

    #[test]
    #[should_panic(expected = "Budget must be at least 0.1 NEAR per job under review plus the storage part: 1 job(s) would need")]
    fn submit_with_exactly_the_minimum_deposit_refused() {
        let mut c = setup();
        as_account("dave.near", MIN_AGENT_DEPOSIT); // the storage part eats into the 0.1 NEAR
        c.join_swarm("minimal".into());
        as_account("dave.near", zero());
        c.submit(0, WASM.into(), REPORT.into());
    }

    #[test]
    fn submit_with_the_minimum_plus_the_storage_part_allowed() {
        let mut c = setup();
        as_account("dave.near", milli(110));
        c.join_swarm("minimal plus".into());
        let part = agent(&c, "dave.near").storage_cost;
        assert!(milli(110) >= MIN_AGENT_DEPOSIT.saturating_add(part), "storage part {}", part);
        as_account("dave.near", zero());
        c.submit(0, WASM.into(), REPORT.into());
        assert_eq!(agent(&c, "dave.near").jobs_in_review, 1);
    }

    // --- assigned agent ----------------------------------------------------------------------------

    fn assigned_job(c: &mut SwarmJobs) -> u64 {
        as_account("carol.near", near(2));
        c.post_job("for alice only".into(), None, near(1), None, Some(acc("alice.near")))
    }

    #[test]
    #[should_panic(expected = "Job 1 is assigned to alice.near; only that agent may submit")]
    fn submit_by_another_agent_to_an_assigned_job_refused() {
        let mut c = setup();
        assert_eq!(assigned_job(&mut c), 1);
        as_account("dave.near", near(1));
        c.join_swarm("other".into());
        as_account("dave.near", zero());
        c.submit(1, WASM.into(), REPORT.into());
    }

    #[test]
    fn assigned_agent_may_submit_and_is_shown() {
        let mut c = setup();
        assigned_job(&mut c);
        assert_eq!(c.get_job(1).unwrap().assigned_agent, Some(acc("alice.near")));
        assert_eq!(c.get_job(0).unwrap().assigned_agent, None);
        as_account("alice.near", zero());
        c.submit(1, WASM.into(), REPORT.into());
        assert_eq!((status(&c, 1).as_str(), c.get_job(1).unwrap().agent), ("Submitted", Some(acc("alice.near"))));
        // ... and keeps the job after a reject: nobody else may take it over.
        as_account("bob.near", zero());
        c.reject(1, "blocked".into(), false);
        as_account("alice.near", zero());
        c.submit(1, WASM.into(), REPORT2.into());
        assert_eq!(status(&c, 1), "Submitted");
    }

    #[test]
    #[should_panic(expected = "Job 1 is assigned to alice.near; only that agent may submit")]
    fn assignment_holds_after_a_reject() {
        let mut c = setup();
        assigned_job(&mut c);
        as_account("alice.near", zero());
        c.submit(1, WASM.into(), REPORT.into());
        as_account("bob.near", zero());
        c.reject(1, "blocked".into(), false);
        as_account("dave.near", near(1));
        c.join_swarm("other".into());
        as_account("dave.near", zero());
        c.submit(1, WASM.into(), REPORT.into());
    }

    // --- reject and close --------------------------------------------------------------------------

    #[test]
    fn reject_with_close_cancels_the_job_and_frees_the_agent() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.reject(0, "blocked for good".into(), true);
        let j = c.get_job(0).unwrap();
        assert_eq!((j.status.as_str(), j.reject_reason.as_deref()), ("Cancelled", Some("blocked for good")));
        assert_eq!(j.agent, Some(acc("alice.near"))); // the record keeps who submitted
        assert_eq!(agent(&c, "alice.near").jobs_in_review, 0);
        // The poster has nothing left to cancel and the agent cannot resubmit.
        as_account("alice.near", zero());
        assert_eq!(c.leave_swarm(), near(1)); // nothing under review any more
    }

    #[test]
    #[should_panic(expected = "Job 0 is not open for submissions (status Cancelled)")]
    fn resubmit_after_reject_with_close_refused() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.reject(0, "blocked for good".into(), true);
        as_account("alice.near", zero());
        c.submit(0, WASM.into(), REPORT.into());
    }

    #[test]
    #[should_panic(expected = "Job 0 cannot be cancelled (status Cancelled)")]
    fn cancel_after_reject_with_close_refused() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.reject(0, "blocked for good".into(), true);
        as_account("carol.near", zero());
        c.cancel(0);
    }

    #[test]
    #[should_panic(expected = "Only the reviewer can reject")]
    fn reject_with_close_by_non_reviewer_refused() {
        let mut c = submitted();
        as_account("carol.near", zero());
        c.reject(0, "mine".into(), true);
    }

    #[test]
    #[should_panic(expected = "Job 0 is not Submitted (status Rejected)")]
    fn reject_with_close_of_a_rejected_job_refused() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.reject(0, "blocked".into(), false);
        c.reject(0, "blocked for good".into(), true);
    }

    #[test]
    fn reject_without_close_still_allows_a_resubmit() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.reject(0, "blocked".into(), false);
        assert_eq!(status(&c, 0), "Rejected");
        as_account("alice.near", zero());
        c.submit(0, WASM.into(), REPORT2.into());
        assert_eq!(status(&c, 0), "Submitted");
    }

    // --- migrate from the v1 state ---------------------------------------------------------------

    /// Writes the v1 state through the v1 layout (the live testnet state: reviewer bob, alice with
    /// 0.5 NEAR and two jobs of 1 NEAR submitted with the escrow v2 wasm and the two CI reports),
    /// then migrates: everything carries over, the new fields are 0 / None, no v1 record stays, and
    /// the v2 approve pays the migrated job.
    #[test]
    fn migrate_rewrites_v1_jobs_and_keeps_agents() {
        as_account("jobs.near", zero());
        {
            let mut v1 = SwarmJobsV1 {
                reviewer: acc("bob.near"),
                jobs: IterableMap::new(JOBS_PREFIX_V1),
                agents: LookupMap::new(AGENTS_PREFIX),
                agent_count: 1,
                next_id: 0,
            };
            v1.agents.insert(
                acc("alice.near"),
                Agent { profile: "escrow upgrade agent".into(), budget: milli(500), jobs_paid: 0, jobs_in_review: 2 },
            );
            for report in [REPORT, REPORT2] {
                let id = v1.next_id;
                v1.next_id += 1;
                v1.jobs.insert(
                    id,
                    JobV1 {
                        poster: acc("demo.near"),
                        description: "upgrade escrow2 to escrow v2 keeping its deals".into(),
                        target_account: Some(acc("escrow2.near")),
                        reward: near(1),
                        status: JobStatus::Submitted,
                        agent: Some(acc("alice.near")),
                        wasm_sha256: Some(WASM.into()),
                        report_sha256: Some(report.into()),
                        gate_report_sha256: None,
                        reject_reason: None,
                    },
                );
            }
            env::state_write(&v1);
        }
        let mut c = SwarmJobs::migrate();
        assert_eq!(c.job_count(), 2);
        assert_eq!(c.agent_count(), 1);
        assert_eq!(c.get_reviewer(), acc("bob.near"));
        let j = c.get_job(0).unwrap();
        assert_eq!((j.poster, j.target_account), (acc("demo.near"), Some(acc("escrow2.near"))));
        assert_eq!((j.status.as_str(), j.agent), ("Submitted", Some(acc("alice.near"))));
        assert_eq!((j.wasm_sha256.as_deref(), j.report_sha256.as_deref()), (Some(WASM), Some(REPORT)));
        assert_eq!((j.gate_report_sha256, j.reject_reason), (None, None));
        assert_eq!((j.reward, j.reward_storage_fee), (near(1), zero()));
        assert_eq!((j.scenario_sha256, j.assigned_agent), (None, None));
        assert_eq!(c.get_job(1).unwrap().report_sha256.as_deref(), Some(REPORT2));
        assert!(c.get_job(2).is_none());
        let a = agent(&c, "alice.near");
        assert_eq!((a.profile.as_str(), a.budget, a.jobs_paid, a.jobs_in_review), ("escrow upgrade agent", milli(500), 0, 2));
        assert_eq!(a.storage_cost, zero()); // registered on v1: no storage part recorded
        // The v1 map was cleared: nothing under its prefix answers any more.
        let leftover: IterableMap<u64, JobV1> = IterableMap::new(JOBS_PREFIX_V1);
        assert!(leftover.get(&0).is_none() && leftover.get(&1).is_none());
        // The v2 path on the migrated state: approve with the five arguments pays job 0.
        approve(&mut c, 0);
        assert!(callback(&mut c, 0, PromiseResult::Successful(vec![])));
        assert_eq!(status(&c, 0), "Paid");
        assert_eq!(agent(&c, "alice.near").jobs_in_review, 1);
        as_account("bob.near", zero());
        c.reject(1, "blocked".into(), false);
        assert_eq!(agent(&c, "alice.near").jobs_in_review, 0);
        // ... and a new job continues the id sequence with the v2 fields.
        as_account("demo.near", near(2));
        assert_eq!(c.post_job("third".into(), None, near(1), Some(SCENARIO.into()), None), 2);
        assert_eq!(c.job_count(), 3);
        assert!(!c.get_job(2).unwrap().reward_storage_fee.is_zero());
        // A v1 agent may leave once nothing is under review; its budget comes back in full.
        as_account("alice.near", zero());
        assert_eq!(c.leave_swarm(), milli(500));
        assert_eq!(c.agent_count(), 0);
    }

    #[test]
    #[should_panic(expected = "No old state to migrate")]
    fn migrate_without_state_refused() {
        as_account("jobs.near", zero());
        SwarmJobs::migrate();
    }
}
