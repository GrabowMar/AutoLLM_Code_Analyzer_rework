# Execution engine

Every generation run, its evaluation, study schedules and post-runs run in one leader process, the executor, which claims work from Postgres under leases. Budget admission (blocks, reservations, spend caps) happens inside the claim; see [Budget](/docs/BUDGET).

The executor is `python manage.py run_executor` (compose service `executor`). The web process (`django`) only writes rows and rings a Redis doorbell (`executor:wake`) after the transaction commits; nothing runs in the web process. Code lives in `backend/execution/`. How a study decides the run order is in [Studies](/docs/STUDIES); local endpoints and the relay are in [Local models](/docs/LOCAL_MODELS). The other background services (Celery, beat) are in [Background services](/docs/BACKGROUND_SERVICES).

## Process

| Part | What it does |
|---|---|
| Leader lock | `pg_try_advisory_lock(EXECUTOR_LEADER_LOCK_KEY)` on a dedicated connection at start; a second instance exits with code 1. The lock is re-checked in `pg_locks` every tick; if it is gone (Postgres restart, dropped connection) the executor stops claiming, expires its own leases and exits with code 1, and Docker starts it again. It is a guard, not high availability: exactly one executor runs. |
| Main loop | `BLPOP executor:wake` for at most `EXECUTOR_TICK_SECONDS` (2 s), then: lost-run check → `Scheduler.tick` (evaluations first, then generation claims with budget admission) → submit claims to pools. `reconcile()` every 15 s, the study stop rules every 60 s, schedules and post-runs every 30 s, a daily LLM request purge. `ExecutorSettings` is re-read every tick. A Redis error sleeps one tick (never busy-spins); Postgres stays the only source of truth. |
| Heartbeat | Every `EXECUTOR_HEARTBEAT_SECONDS` (20 s): `ExecutorInstance.last_seen_at` + status blob (`config`: effective slots, open circuits, blocked reasons). Renews `lease_expires_at` (+`EXECUTOR_LEASE_SECONDS`, 90 s) **only for runs in the in-process registry**, and leases of running post-runs. |
| Pools | `sandbox` (agentic runs and restricted slot agents), `eval` (evaluations and preview builds), `llm` (single-shot slot calls and batch submissions), `post` (post-runs) and `schedule` (schedule actions). Admission uses `min(slots − used in DB, really free threads)`. |
| Live events | Every 2 s at most, only when changed: `tick` per active study on `study:<id>`, `endpoint:<id>` state per local endpoint. `capacity` at most every 5 s; its `executor.code_stale` is true once the code on disk differs from what this executor started with. |
| LLM proxy, direct pumps | `inference.proxy_server.serve` and `inference.pump.run_direct_pumps` threads, started after leadership is won. The proxy is required: if it cannot bind `LLM_PROXY_BIND` or stops, the executor exits with code 1. |

The executor runs with `CONN_MAX_AGE=0`; every worker closes its DB connection when it finishes. `ExecutorInstance.code_version` is a sha1 over path, mtime and size of `backend/**/*.py`; the capacity API compares the live leader's value with the web process's (`code_stale`).

### Restart policy and exits

The `executor` service has `restart: unless-stopped`. It exits with code 1, and Docker restarts it, when: a second instance holds the leader lock; the lock is lost; the LLM proxy cannot bind; or `wait_for_migrations` gives up after 600 s. The executor never migrates. Nothing is lost while it is down: runs stay `PENDING`, their reservations stay in the ledger, and its leases are requeued as `executor_lost` on the next start.

### Drain and restart

Backend edits do not reload the executor. To restart safely:

```bash
docker compose exec django /entrypoint python manage.py executor drain     # stop claiming runs, post-runs and schedules
docker compose exec django /entrypoint python manage.py executor status    # wait for "safe_to_restart": true
docker compose restart executor
docker compose exec django /entrypoint python manage.py executor resume
```

`docker compose exec` skips the image's entrypoint, which builds `DATABASE_URL`, so call `/entrypoint` explicitly (or `just manage executor status`).

## Run lifecycle

```
PENDING (queued_at set) ──claim + budget──► RUNNING/running (lease, reservation) ──generation done──► RUNNING/eval_queued
   ▲                                          │ settle the attempt's spend                             │ eval claim (eval_parallel)
   │  infra failure / executor_lost           ▼                                                        ▼
   └──── attempt+1, run_after, fresh ──── COMPLETED / FAILED / CANCELLED ◄──────────────────── RUNNING/evaluating (lease)
         reservation
```

- `queued_at IS NOT NULL` means the executor owns the row (`execution.api.enqueue_runs`, called on commit by launch, top-up and retry). Reference runs have no generation stage: `execution.api.enqueue_evaluation` puts them straight into `running/eval_queued`, where the evaluation claim (by phase, never by lane) picks them up.
- **Lanes** follow the run kind: `sandbox` for `app_agentic` and `slot_agent`, `llm` for `slot_single_shot`. Lane queues are keyed `(study or adhoc:<user>, endpoint)`, strict head-of-line in the plan's claim `sequence` (repeat-major blocks, see [Budget](/docs/BUDGET#blocks-and-reservations)). Across lanes the scheduler is work-conserving; heads are ordered by priority, fair share per user and per study, endpoint load, model affinity, a warm model and launch time.
- Runs of a study are claimed while it is `piloting` or `running`. Runs of a **paused condition** (`StudyCondition.paused`, set by a `pause_condition` stop rule) are invisible to the lanes: they never block the other conditions.
- Only the chosen rows are locked (`FOR UPDATE SKIP LOCKED`). Inside the same transaction the budget admission (`generation.budget.claims.admit`) locks each study row, reserves blocks and moves each run's slice, and may refuse a run; a refused run stops its lane for the tick (head-of-line), and the rest are claimed with one conditional UPDATE.
- Every write a worker makes goes through `transitions.guarded_save`; a 0-row result raises `LeaseLost` and the worker stops quietly. `finish_stage` runs as separate autocommit steps: run row → LLM responses → **settle the attempt's spend** (when the generation stage ended) → the cell's current run → events → cost priors and stop rules (terminal) → study completion → wake.
- Cancel (`transitions.cancel_runs`): PENDING and `phase=running` become `cancelled` with outcome `excluded:cancelled`; the running worker keeps its `lease_owner` (and its slot) until it exits. Runs waiting for or in evaluation finish.
- A study ends `completed` when it is `running` with no active run, or `stopped` (from `running` or `piloting`) when a stop is pending (`Study.stop_reason`, set by a balanced budget stop or a `stop` rule). Both publish `state`; a stop also publishes `stopped {reason}`.

### Blocked reasons

A pending run that is not admitted reports why (tick events, `GET /api/execution/queue/`):

| Reason | Meaning |
|---|---|
| `sandbox_full`, `llm_threads_full` | No free sandbox slot / LLM job thread. |
| `endpoint_offline` | The endpoint is disabled or has no live relay session / fresh inventory. |
| `circuit_open` | Three infra failures on the endpoint within 5 minutes; closed again after 60 s. |
| `endpoint_full` | The endpoint is at its job cap (`max_active_jobs`, else `max_concurrency`). |
| `model_not_served` | The live session does not serve the frozen `(name, digest)`. |
| `residency_wait` | Starting this model would exceed the resident-model limit (the endpoint's, or the study's `max_resident_models`). After `EXECUTOR_STARVATION_S` the starvation guard drains the endpoint for a swap. Residency applies only to endpoints that host models themselves (`execution.sequence.hosts_models_locally`: Ollama, a relay, `cost_basis` free, or an endpoint that declares its own K); a remote API such as OpenCode Go or Anthropic never waits for residency, only for its job cap and the study's `per_endpoint`/`per_model`. |
| `warming_model` | An Ollama prefetch (warm-up) of the model is in progress. |
| `study_cap` | `Study.max_parallel` (`concurrency.study_max_parallel`) is reached. |
| `study_endpoint_cap`, `study_model_cap`, `study_provider_cap` | The study's `concurrency.per_endpoint` / `per_model` / `per_provider` (pinned `routing.only` provider) limit is reached. |
| `budget` | The block cannot start under the study cap (USD, tokens, runs or wall hours); `on_budget_exhausted` was applied. |
| `cell_budget`, `budget_stop` | The run was excluded (cell cap reached; or a balanced stop). |
| `spend_cap`, `quota` | A blocking SpendCap would be exceeded; the study is paused (quota for 5 h / weekly model caps) and resumes when the window frees. |
| `draining` | The executor is draining. |
| `retry_backoff` | A requeued run waiting for its `run_after`. |

### Per-study concurrency

`Study.max_parallel` and `Study.execution_options` are copied from the effective budget preset at launch (`budget.policy.execution_options`): `per_endpoint`, `per_model`, `per_provider`, `rpm_per_model` (enforced by the gateway's shared token bucket, `GatewayConfig.rate_limit`), `max_resident_models`, `eval_parallel` (a study's evaluations in flight; enforced in the evaluation claim) and `warmup`.

### Slots and the memory rule

`ExecutorSettings` (singleton, re-read every tick):

| Setting | Default | Bounds |
|---|---|---|
| `sandbox_slots` | `AGENT_MAX_CONCURRENT_RUNS` (else 1) | 0..8 |
| `eval_slots` | 1 | 0..4 |
| `llm_job_threads` | 16 | 1..32 |
| `post_run_slots` | 1 | 0..2 |
| `draining` | false | |

```
sandbox_slots × (AGENT_SANDBOX_MEM_LIMIT_MAX + 256m) + eval_slots × max(EVAL_SLOT_MEM, SAMPLE_BUILD_MEMORY)
  + post_run_slots × ANALYSIS_WORKSPACE_MEM + APPS_DIND_APP_RESERVE ≤ 0.95 × APPS_DIND_MEM_LIMIT
```

`AGENT_SANDBOX_MEM_LIMIT_MAX` is the largest sandbox any budget preset may ask for: effective policies clamp `sandbox.memory` to it (so the generous preset's 4g becomes 3g on the default host, note `sandbox_clamped_to_machine`). A PATCH that breaks the rule is refused with the arithmetic, except for the minimal one-sandbox / one-evaluation configuration; when even that does not fit, sandbox runs and evaluations take turns (`exclusive_eval`).

## Leases and reconciliation

`reconcile()` runs at leader start and every 15 s (each step isolated):

0. **Lost runs** (every tick): owned by this executor, not in the registry, stage entered > 30 s ago → `infra_retry(executor_lost)`; a lost evaluation goes back to `eval_queued`.
1. **Expired leases**: sandbox runs have their containers reaped, then `executor_lost`; evaluations go back to `eval_queued` at most `EVAL_MAX_REQUEUES` times, then end `infra_error:eval_lost`.
2. **Orphan containers** (every 4th pass).
3. Pump leases of dead instances and LLM request leases.
4. Stale relay sessions end; a standby session is promoted.
5. Sessions of expired relay tokens end.
6. Executor instances silent for 90 s are marked stopped.
7. **Completion sweep**: running studies with no active run complete; stopping ones stop; stray runs of cancelled studies are cancelled.
8. **Ledger repair** (`budget.ledger.reconcile`): every attempt that ended but still holds a reservation is settled; block reservations whose cells all finished are released.
9. **Spend-cap resume** (`budget.claims.resume_paused_for_caps`): studies paused for `quota` / `spend_cap` resume once their next run fits every cap.

Every 60 s the executor also evaluates the stop rules of every piloting or running study (`budget.stop_rules.evaluate_active`); they also run after every terminal transition.

## Retries

| Reason | When | Budget |
|---|---|---|
| `executor_lost` | lease expired, lost-run check, executor restart | `EXECUTOR_LOST_RETRIES` (10), counted in `execution.executor_lost_count`; does not consume `infra_retries` |
| `submit_failed` | the claimed run could not be handed to a pool | `retries.infra_retries` |
| `infra`, `infra_interrupted` | sandbox or gateway failures, zero successful calls for an infra reason, transport errors | `retries.infra_retries` of the run's budget preset (2 without one), backoff `30 s × 2^n` |

A retry requeues the **same row** (`attempt + 1`, same manifest and seed). The attempt's spend is settled first and kept in `execution.attempts[]` / `prior_attempts_*`; the next attempt reserves fresh budget from what is left (never from its block) and is refused as `excluded:cell_budget` when the cell's spend plus the need exceeds `cell.max_cost_usd`. An exhausted run ends `FAILED` with `outcome=infra_error` (not usable).

## API

All under `/api/execution/`:

| Route | Who | What |
|---|---|---|
| `GET /capacity/` | any user | Executor status, effective slots, memory limits and warnings, lane usage, visible endpoints, own studies (staff: all). |
| `GET /queue/?study_id&endpoint&limit` | any user | The caller's next pending runs with `blocked_reason` (a dry run of the scheduler: no locks, no budget admission). |
| `GET/PATCH /settings/` | staff | Slot settings and drain. |

## Test patch targets

- To keep the doorbell out of Redis: `backend.execution.api.wake`; SSE: `backend.realtime.events.publish`.
- To run claims in a test: `Scheduler(owner).tick(EffectiveSlots(...), {"sandbox": n, "eval": n, "llm": n})`; wrap it in `django_capture_on_commit_callbacks(execute=True)` to run the after-commit work (events, block closing). `backend/generation/tests/wp11_budget/conftest.py` has `make_study` (a launched study tree with blocks) and a `tick` fixture.
- Stages: `backend.generation.agentic.dispatch.run_stage` (generation) and `backend.generation.evaluation.suite.run_eval_stage` (evaluation); post-runs: `backend.generation.study.automation.run_post_run`.
- To run runs end to end: `Executor(sync_pools=True, start_proxy=False, start_pumps=False, redis_client=<fake>)`, then `acquire_leadership()`, `startup()` and `tick()`.
