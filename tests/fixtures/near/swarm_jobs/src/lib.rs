//! Guarded jobs for agent swarms: a job's reward is paid to the agent only after the reviewer
//! approves exactly the wasm that BUMM Guard's review checked.
//!
//! Flow: an agent joins the swarm (`join_swarm`, the deposit is its inference budget), a poster
//! posts a job with a reward (`post_job`, reward = attached deposit), a registered agent submits the
//! sha256 of its wasm and of its scenario report (`submit`), the reviewer runs `bumm-guard
//! review-job` off-chain and signs either `approve` (with the same wasm hash and the hash of the
//! gate report it produced; the reward is transferred) or `reject` (the agent may resubmit). The
//! poster can `cancel` a job that is not under review and gets the reward back.
//!
//! What this contract does not do: it cannot verify a sandbox proof by itself; it trusts the
//! `reviewer` account. There is no inference and no agent runtime here: the budget is only
//! tracked. One reviewer, no DAO. See docs/SWARM_JOBS.md.
//!
//! Build: `cargo build --release --target wasm32-unknown-unknown -p swarm_jobs`
//! from tests/fixtures/near (rustc 1.85.0, pinned there). Scenarios: scenarios/swarm_jobs/.

use near_sdk::store::{IterableMap, LookupMap};
use near_sdk::{env, near, require, AccountId, Gas, NearToken, PanicOnDefault, Promise};

const MAX_DESCRIPTION: usize = 1000;
const MAX_PROFILE: usize = 200;
const MAX_REASON: usize = 500;
const MAX_LIST: u64 = 100;
const CALLBACK_GAS: Gas = Gas::from_tgas(10);
/// Deposit required on an account's first `join_swarm` (0.1 NEAR). It raises the cost of spam
/// submissions (a free account could otherwise lock any Open job until the reviewer acts); while
/// the agent has a job under review (Submitted or Paying) its budget cannot drop below it.
pub const MIN_AGENT_DEPOSIT: NearToken = NearToken::from_millinear(100);

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
}

#[near(serializers = [borsh])]
pub struct Agent {
    profile: String,
    budget: NearToken,
    jobs_paid: u64,
    /// Jobs of this agent in status Submitted or Paying.
    jobs_in_review: u64,
}

/// What `get_job` / `list_jobs` return (JSON; `reward` is a yoctoNEAR string).
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
}

/// What `get_agent` returns (JSON; `budget` is a yoctoNEAR string).
#[near(serializers = [json])]
pub struct AgentView {
    pub account: AccountId,
    pub profile: String,
    pub budget: NearToken,
    pub jobs_paid: u64,
    pub jobs_in_review: u64,
}

fn require_sha256(name: &str, value: &str) {
    require!(
        value.len() == 64 && value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        format!("{} must be 64 lowercase hex characters", name)
    );
}

#[near]
impl SwarmJobs {
    /// `reviewer` is the account that runs BUMM Guard review and signs `approve` / `reject`.
    #[init]
    pub fn new(reviewer: AccountId) -> Self {
        Self {
            reviewer,
            jobs: IterableMap::new(b"j"),
            agents: LookupMap::new(b"a"),
            agent_count: 0,
            next_id: 0,
        }
    }

    /// Registers the caller as an agent (or updates its profile). The attached deposit is added to
    /// its inference budget; the first join needs at least `MIN_AGENT_DEPOSIT` (0.1 NEAR), top-ups
    /// may be any amount. Nothing here runs inference; the budget is only tracked.
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
                self.agents.insert(caller, Agent { profile, budget: deposit, jobs_paid: 0, jobs_in_review: 0 });
                self.agent_count += 1;
            }
        }
    }

    /// The agent takes `amount` (yoctoNEAR string) of its own budget back. While it has a job under
    /// review (Submitted or Paying) the budget cannot drop below `MIN_AGENT_DEPOSIT`.
    pub fn withdraw_budget(&mut self, amount: NearToken) -> Promise {
        let caller = env::predecessor_account_id();
        require!(!amount.is_zero(), "Amount must be positive");
        let agent = self
            .agents
            .get_mut(&caller)
            .unwrap_or_else(|| env::panic_str("Not a registered agent"));
        require!(amount <= agent.budget, "Amount exceeds the agent's budget");
        let left = agent.budget.saturating_sub(amount);
        require!(
            agent.jobs_in_review == 0 || left >= MIN_AGENT_DEPOSIT,
            "Budget must stay at least 0.1 NEAR while the agent has a job under review"
        );
        agent.budget = left;
        Promise::new(caller).transfer(amount)
    }

    /// Posts a job; the attached deposit is the reward (must be positive). Returns the job id.
    #[payable]
    pub fn post_job(&mut self, description: String, target_account: Option<AccountId>) -> u64 {
        let reward = env::attached_deposit();
        require!(!reward.is_zero(), "Attach a positive deposit as the reward");
        require!(description.len() <= MAX_DESCRIPTION, "Description is too long (max 1000 bytes)");
        let id = self.next_id;
        self.next_id += 1;
        self.jobs.insert(
            id,
            Job {
                poster: env::predecessor_account_id(),
                description,
                target_account,
                reward,
                status: JobStatus::Open,
                agent: None,
                wasm_sha256: None,
                report_sha256: None,
                gate_report_sha256: None,
                reject_reason: None,
            },
        );
        id
    }

    /// A registered agent submits its work as two hashes: the wasm it built and the scenario
    /// report (`bumm-guard simulate`) that proves it. Allowed while the job is Open or Rejected.
    pub fn submit(&mut self, job_id: u64, wasm_sha256: String, report_sha256: String) {
        let caller = env::predecessor_account_id();
        require!(
            self.agents.contains_key(&caller),
            "Only a registered agent can submit; call join_swarm first"
        );
        require_sha256("wasm_sha256", &wasm_sha256);
        require_sha256("report_sha256", &report_sha256);
        let job = self.job_mut(job_id);
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

    /// Reviewer only: the gate passed for exactly `wasm_sha256` (must equal the submitted hash), so
    /// the reward goes to the agent. `gate_report_sha256` is the sha256 of the review report JSON.
    /// The job is `Paying` until the transfer's callback marks it `Paid` (or, if the transfer
    /// failed, `Submitted` again with the reward still held by this contract).
    pub fn approve(&mut self, job_id: u64, wasm_sha256: String, gate_report_sha256: String) -> Promise {
        require!(env::predecessor_account_id() == self.reviewer, "Only the reviewer can approve");
        require_sha256("gate_report_sha256", &gate_report_sha256);
        let job = self.job_mut(job_id);
        require!(
            job.status == JobStatus::Submitted,
            format!("Job {} is not Submitted (status {})", job_id, job.status.as_str())
        );
        require!(
            job.wasm_sha256.as_deref() == Some(wasm_sha256.as_str()),
            format!("wasm_sha256 does not match the submission of job {}", job_id)
        );
        let agent = job.agent.clone().unwrap_or_else(|| env::panic_str("Job has no agent"));
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

    /// Reviewer only: the gate blocked. The reason is stored; an agent may submit again.
    pub fn reject(&mut self, job_id: u64, reason: String) {
        require!(env::predecessor_account_id() == self.reviewer, "Only the reviewer can reject");
        require!(reason.len() <= MAX_REASON, "Reason is too long (max 500 bytes)");
        let job = self.job_mut(job_id);
        require!(
            job.status == JobStatus::Submitted,
            format!("Job {} is not Submitted (status {})", job_id, job.status.as_str())
        );
        job.reject_reason = Some(reason);
        job.status = JobStatus::Rejected;
        if let Some(agent) = job.agent.clone() {
            self.review_count(&agent, false);
        }
    }

    /// Poster only, while the job is Open or Rejected: the reward goes back to the poster.
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
        })
    }

    pub fn agent_count(&self) -> u64 {
        self.agent_count
    }

    pub fn get_reviewer(&self) -> AccountId {
        self.reviewer.clone()
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
    const REPORT: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const GATE: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

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

    fn zero() -> NearToken {
        NearToken::from_yoctonear(0)
    }

    /// reviewer bob, agent alice (budget 1 NEAR), job 0 by poster carol (reward 2 NEAR).
    fn setup() -> SwarmJobs {
        as_account("owner.near", zero());
        let mut c = SwarmJobs::new(acc("bob.near"));
        as_account("alice.near", near(1));
        c.join_swarm("rust agent".into());
        as_account("carol.near", near(2));
        assert_eq!(c.post_job("upgrade escrow".into(), Some(acc("escrow.near"))), 0);
        c
    }

    fn submitted() -> SwarmJobs {
        let mut c = setup();
        as_account("alice.near", zero());
        c.submit(0, WASM.into(), REPORT.into());
        c
    }

    fn callback(c: &mut SwarmJobs, result: PromiseResult) -> bool {
        testing_env!(
            ctx("jobs.near", zero()).build(),
            near_sdk::test_vm_config(),
            near_sdk::RuntimeFeesConfig::test(),
            Default::default(),
            vec![result]
        );
        c.on_reward_paid(0)
    }

    fn status(c: &SwarmJobs) -> String {
        c.get_job(0).unwrap().status
    }

    #[test]
    fn happy_path_pays_the_agent() {
        let mut c = submitted();
        assert_eq!(status(&c), "Submitted");
        as_account("bob.near", zero());
        c.approve(0, WASM.into(), GATE.into());
        assert_eq!(status(&c), "Paying");
        assert!(callback(&mut c, PromiseResult::Successful(vec![])));
        let j = c.get_job(0).unwrap();
        assert_eq!(j.status, "Paid");
        assert_eq!(j.agent, Some(acc("alice.near")));
        assert_eq!(j.gate_report_sha256.as_deref(), Some(GATE));
        assert_eq!(j.reward, near(2));
        assert_eq!(c.get_agent(acc("alice.near")).unwrap().jobs_paid, 1);
    }

    #[test]
    fn failed_transfer_does_not_leave_the_job_paid() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.approve(0, WASM.into(), GATE.into());
        assert!(!callback(&mut c, PromiseResult::Failed));
        let j = c.get_job(0).unwrap();
        assert_eq!(j.status, "Submitted");
        assert_eq!(j.gate_report_sha256, None);
        assert_eq!(c.get_agent(acc("alice.near")).unwrap().jobs_paid, 0);
        // The reviewer can approve again.
        as_account("bob.near", zero());
        c.approve(0, WASM.into(), GATE.into());
        assert!(callback(&mut c, PromiseResult::Successful(vec![])));
        assert_eq!(status(&c), "Paid");
    }

    #[test]
    #[should_panic(expected = "Only the reviewer can approve")]
    fn approve_by_non_reviewer_refused() {
        let mut c = submitted();
        as_account("carol.near", zero());
        c.approve(0, WASM.into(), GATE.into());
    }

    #[test]
    #[should_panic(expected = "wasm_sha256 does not match the submission of job 0")]
    fn approve_with_other_wasm_hash_refused() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.approve(0, OTHER.into(), GATE.into());
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
        as_account("bob.near", zero());
        c.approve(0, WASM.into(), GATE.into());
        callback(&mut c, PromiseResult::Successful(vec![]));
        as_account("bob.near", zero());
        c.approve(0, WASM.into(), GATE.into());
    }

    #[test]
    #[should_panic(expected = "Job 0 is not Submitted (status Paying)")]
    fn approve_while_paying_refused() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.approve(0, WASM.into(), GATE.into());
        c.approve(0, WASM.into(), GATE.into());
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
    fn cancel_while_open_refunds_and_closes() {
        let mut c = setup();
        as_account("carol.near", zero());
        c.cancel(0);
        assert_eq!(status(&c), "Cancelled");
    }

    #[test]
    fn reject_then_resubmit_then_approve() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.reject(0, "no upgrade proof for this pair".into());
        let j = c.get_job(0).unwrap();
        assert_eq!(j.status, "Rejected");
        assert_eq!(j.reject_reason.as_deref(), Some("no upgrade proof for this pair"));
        as_account("alice.near", zero());
        c.submit(0, WASM.into(), REPORT.into());
        assert_eq!(c.get_job(0).unwrap().reject_reason, None);
        as_account("bob.near", zero());
        c.approve(0, WASM.into(), GATE.into());
        assert!(callback(&mut c, PromiseResult::Successful(vec![])));
        assert_eq!(status(&c), "Paid");
    }

    #[test]
    fn cancel_after_reject_is_allowed() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.reject(0, "blocked".into());
        as_account("carol.near", zero());
        c.cancel(0);
        assert_eq!(status(&c), "Cancelled");
    }

    #[test]
    #[should_panic(expected = "Only the reviewer can reject")]
    fn reject_by_non_reviewer_refused() {
        let mut c = submitted();
        as_account("alice.near", zero());
        c.reject(0, "mine".into());
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
    #[should_panic(expected = "Attach a positive deposit as the reward")]
    fn post_without_reward_refused() {
        let mut c = setup();
        as_account("carol.near", zero());
        c.post_job("free work".into(), None);
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
        callback(&mut c, PromiseResult::Successful(vec![]));
    }

    #[test]
    fn budget_is_tracked_and_withdrawn() {
        let mut c = setup();
        as_account("alice.near", near(2));
        c.join_swarm("rust agent v2".into());
        let a = c.get_agent(acc("alice.near")).unwrap();
        assert_eq!((a.profile.as_str(), a.budget), ("rust agent v2", near(3)));
        assert_eq!(c.agent_count(), 1);
        as_account("alice.near", zero());
        c.withdraw_budget(near(1));
        assert_eq!(c.get_agent(acc("alice.near")).unwrap().budget, near(2));
    }

    #[test]
    #[should_panic(expected = "Amount exceeds the agent's budget")]
    fn withdraw_more_than_budget_refused() {
        let mut c = setup();
        as_account("alice.near", zero());
        c.withdraw_budget(near(2));
    }

    #[test]
    #[should_panic(expected = "Attach at least 0.1 NEAR to join the swarm")]
    fn first_join_without_deposit_refused() {
        let mut c = setup();
        as_account("dave.near", zero());
        c.join_swarm("free rider".into());
    }

    #[test]
    #[should_panic(expected = "Attach at least 0.1 NEAR to join the swarm")]
    fn first_join_below_minimum_refused() {
        let mut c = setup();
        as_account("dave.near", NearToken::from_millinear(99));
        c.join_swarm("almost".into());
    }

    #[test]
    fn first_join_at_minimum_and_free_top_up_allowed() {
        let mut c = setup();
        as_account("dave.near", MIN_AGENT_DEPOSIT);
        c.join_swarm("minimal".into());
        as_account("dave.near", zero());
        c.join_swarm("renamed".into());
        let a = c.get_agent(acc("dave.near")).unwrap();
        assert_eq!((a.profile.as_str(), a.budget), ("renamed", MIN_AGENT_DEPOSIT));
    }

    #[test]
    #[should_panic(expected = "Budget must stay at least 0.1 NEAR while the agent has a job under review")]
    fn withdraw_below_minimum_while_submitted_refused() {
        let mut c = submitted();
        assert_eq!(c.get_agent(acc("alice.near")).unwrap().jobs_in_review, 1);
        as_account("alice.near", zero());
        c.withdraw_budget(NearToken::from_millinear(901));
    }

    #[test]
    #[should_panic(expected = "Budget must stay at least 0.1 NEAR while the agent has a job under review")]
    fn withdraw_below_minimum_while_paying_refused() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.approve(0, WASM.into(), GATE.into());
        as_account("alice.near", zero());
        c.withdraw_budget(near(1));
    }

    #[test]
    fn withdraw_floor_follows_the_review() {
        let mut c = submitted();
        as_account("alice.near", zero());
        c.withdraw_budget(NearToken::from_millinear(900)); // down to exactly the minimum: allowed
        assert_eq!(c.get_agent(acc("alice.near")).unwrap().budget, MIN_AGENT_DEPOSIT);
        as_account("bob.near", zero());
        c.reject(0, "blocked".into());
        assert_eq!(c.get_agent(acc("alice.near")).unwrap().jobs_in_review, 0);
        as_account("alice.near", zero());
        c.submit(0, WASM.into(), REPORT.into());
        as_account("bob.near", zero());
        c.approve(0, WASM.into(), GATE.into());
        assert!(callback(&mut c, PromiseResult::Successful(vec![])));
        let a = c.get_agent(acc("alice.near")).unwrap();
        assert_eq!((a.jobs_in_review, a.jobs_paid), (0, 1));
        as_account("alice.near", zero());
        c.withdraw_budget(MIN_AGENT_DEPOSIT); // nothing under review: the whole budget may go
        assert!(c.get_agent(acc("alice.near")).unwrap().budget.is_zero());
    }

    #[test]
    fn failed_transfer_keeps_the_job_under_review() {
        let mut c = submitted();
        as_account("bob.near", zero());
        c.approve(0, WASM.into(), GATE.into());
        callback(&mut c, PromiseResult::Failed);
        assert_eq!(c.get_agent(acc("alice.near")).unwrap().jobs_in_review, 1);
    }

    #[test]
    fn views_list_and_count() {
        let mut c = setup();
        as_account("carol.near", near(1));
        c.post_job("second".into(), None);
        assert_eq!(c.job_count(), 2);
        assert_eq!(c.get_reviewer(), acc("bob.near"));
        let all = c.list_jobs(None, None);
        assert_eq!(all.iter().map(|j| j.id).collect::<Vec<_>>(), vec![0, 1]);
        assert_eq!(c.list_jobs(Some(1), Some(5)).len(), 1);
        assert!(c.get_job(2).is_none());
        assert!(c.get_agent(acc("dave.near")).is_none());
    }
}
