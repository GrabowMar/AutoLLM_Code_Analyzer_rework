# Execution engine

Every generation job, its evaluation, experiment schedules and post-runs run in one leader process, the executor, which claims work from Postgres under leases.

The executor is `python manage.py run_executor` (compose service `executor`). The web process (`django`) only writes rows and rings a Redis doorbell (`executor:wake`) after the transaction commits; nothing runs in the web process. Code lives in `backend/execution/`. How experiments decide the run order is in [Experiments](/docs/EXPERIMENTS#scheduling-policies); local endpoints and the relay are in [Local models](/docs/LOCAL_MODELS). The other background services (Celery, beat) are in [Background services](/docs/BACKGROUND_SERVICES).

## Process

| Part | What it does |
|---|---|
| Leader lock | `pg_try_advisory_lock(EXECUTOR_LEADER_LOCK_KEY)` on a dedicated connection at start; a second instance exits with code 1. The lock is re-checked in `pg_locks` every tick; if it is gone (Postgres restart, dropped connection) the executor stops claiming, expires its own leases and exits with code 1, and Docker starts it again. It is a guard, not high availability: exactly one executor runs. |
| Main loop | `BLPOP executor:wake` for at most `EXECUTOR_TICK_SECONDS` (2 s), then: lost-job check → `Scheduler.tick` → submit claims to pools. `reconcile()` every 15 s, schedules and post-runs every 30 s, a daily LLM request purge (clears `body`/`response` of rows older than `LLM_REQUEST_RETENTION_DAYS`, 7). `ExecutorSettings` is re-read every tick. A Redis error sleeps one tick (never busy-spins); Postgres stays the only source of truth. |
| Heartbeat | Every `EXECUTOR_HEARTBEAT_SECONDS` (20 s): `ExecutorInstance.last_seen_at` + status blob (`config`: effective slots, open circuits, blocked reasons). Renews `lease_expires_at` (+`EXECUTOR_LEASE_SECONDS`, 90 s) **only for jobs in the in-process registry**, and leases of running post-runs. |
| Pools | `sandbox` (agent runs), `eval` (evaluations), `llm` (scaffolding / custom), `post` (post-runs) and `schedule` (schedule actions: one thread of short DB work). Admission uses `min(slots − used in DB, really free threads)`. |
| Live events | Every 2 s at most, only when changed: `tick` per active experiment, `endpoint:<id>` state per local endpoint (owner payload plus the redacted `endpoint:<id>:public` copy). `capacity` at most every 5 s; its `executor.code_stale` is true once the code on disk differs from what this executor started with. |
| LLM proxy, direct pumps | `inference.proxy_server.serve` and `inference.pump.run_direct_pumps` threads, started after leadership is won (see [Local models](/docs/LOCAL_MODELS#5-how-a-call-flows)). The proxy is required: if it cannot bind `LLM_PROXY_BIND` or stops, the executor exits with code 1 (a restart loop in `docker compose ps`/logs, not a silently "online" executor whose local agent runs all fail). The pumps supervise their own threads. |

The executor runs with `CONN_MAX_AGE=0`; every worker closes its DB connection when it finishes, so idle threads hold none. `ExecutorInstance.code_version` is a sha1 over path, mtime and size of `backend/**/*.py`; the capacity API and experiment status compare the live leader's value with the web process's (`code_stale`).

### Restart policy and exits

The `executor` service (both compose files) has `restart: unless-stopped`. It exits with code 1, and Docker restarts it, when: a second instance holds the leader lock; the lock is lost (Postgres restart, dropped lock connection); the LLM proxy cannot bind; or `wait_for_migrations` gives up after 600 s. The executor never migrates: locally django's `/start` does; production `/start` does not migrate at all, so run `manage.py migrate` after a deploy (see [Deployment guide](/docs/deployment-guide)). Nothing is lost while it is down: jobs stay `PENDING` and its leases are requeued as `executor_lost` on the next start.

### Compose commands

Commands below use bare `docker compose`. There is no default compose file in the repo: run them through `just` (which exports `COMPOSE_FILE=docker-compose.local.yml`), set `COMPOSE_FILE` yourself, or pass the same `-f` files the stack was started with. Mixing a different file set recreates containers.

Locally the executor runs `bash /app/compose/local/django/start-executor` from the bind-mounted repo, so it starts on an existing `llmlab/django:local` image and picks up script changes without a rebuild. Production runs the image's `/start-executor`. The django image's baked-in `/start` picks up changes only after a rebuild (`just build`, or `docker compose build django`); a rebuild marks django, celeryworker, celerybeat and flower for recreation on the next `up`.

### Drain and restart

Backend edits do not reload the executor (no `--reload`). The capacity strip shows "Executor on old code" when the web process's code version differs from the running executor's. To restart safely:

```bash
docker compose exec django /entrypoint python manage.py executor drain     # stop claiming jobs, post-runs and schedules
docker compose exec django /entrypoint python manage.py executor status    # wait for "safe_to_restart": true
docker compose restart executor
docker compose exec django /entrypoint python manage.py executor resume
```

`docker compose exec` skips the image's entrypoint, which is what builds `DATABASE_URL`, so call `/entrypoint` explicitly (or use `just manage executor status`, which starts a one-off container).

`safe_to_restart` is true when no job and no post-run is running. Drain is the `draining` flag of `ExecutorSettings` (also settable through the settings API); pending jobs report `draining` as their blocked reason.

`SIGTERM` (a plain restart) gives LLM calls 20 s, then expires every lease the instance holds (jobs, post-runs, pump-leased LLM requests) and sets `stopped_at`. Running sandboxes are not awaited: the next start reaps their containers and requeues the jobs as `executor_lost` (same snapshot and seed; this does not consume the experiment's infra retries).

## Job lifecycle

```
PENDING (queued_at set) ──claim──► RUNNING/running (lease) ──agent: sandbox done──► RUNNING/eval_queued (no lease)
   ▲                                  │ llm lane (or no evaluation)                    │ eval claim
   │  infra failure / executor_lost   ▼                                                ▼
   └───────── attempt+1, run_after ── COMPLETED / FAILED ◄─────────────────── RUNNING/evaluating (lease)
```

- `queued_at IS NOT NULL` means the executor owns the row. Thread-era rows (`queued_at IS NULL`) are never touched by the reconciler; requeue them explicitly (`execution.api.requeue_legacy_jobs`, or `POST …/jobs/requeue-legacy/` on an experiment).
- Lanes: `(experiment or adhoc:<user>, endpoint)`, strict head-of-line in the frozen `sequence` (experiments) or enqueue order (ad-hoc). Across lanes the scheduler is work-conserving. Lane heads are ordered by: priority, then fair share per user, then per experiment, then endpoint load (active jobs over what the endpoint can take, so the OpenRouter lane does not take every free thread while local endpoints idle), then model affinity (already resident), then a warm model, then launch time.
- Only the chosen rows are locked (`FOR UPDATE SKIP LOCKED`) and claimed with one conditional UPDATE; rows a concurrent cancel or pause holds are reconsidered on the next tick.
- Every write a worker makes goes through `transitions.guarded_save` (`WHERE status='running' AND lease_owner=me AND attempt=a`); a 0-row result raises `LeaseLost` and the worker stops quietly. `finish_stage` runs as separate autocommit steps (job → LLM responses → batch → events → experiment completion → wake).
- Cancel (`transitions.cancel_jobs`): PENDING and `phase=running` become CANCELLED at once; the running worker keeps its `lease_owner` (and its slot) until it notices and exits. Jobs waiting for or in evaluation finish.

### Blocked reasons

A pending job that is not admitted reports why (tick events, `GET /api/execution/queue/`, the Overview tab's endpoint lanes):

| Reason | Meaning |
|---|---|
| `sandbox_full`, `llm_threads_full` | No free sandbox slot / LLM job thread. |
| `endpoint_offline` | The endpoint is disabled or has no live relay session / fresh inventory. |
| `circuit_open` | Three infra failures on the endpoint within 5 minutes; closed again after 60 s. |
| `endpoint_full` | The endpoint is at its job cap (`max_active_jobs`, else `max_concurrency`). |
| `model_not_served` | The live session does not serve the frozen `(name, digest)`. |
| `residency_wait` | Starting this model would exceed the endpoint's resident-model limit K. After `EXECUTOR_STARVATION_S` (600 s) the starvation guard drains the endpoint for a swap. |
| `warming_model` | An Ollama prefetch (warm-up) of the model is in progress. |
| `experiment_cap` | The experiment's `max_concurrency` is reached. |
| `draining` | The executor is draining. |
| `retry_backoff` | A requeued job waiting for its `run_after`. |

### Slots and the memory rule

`ExecutorSettings` (singleton, re-read every tick) holds:

| Setting | Default | Bounds |
|---|---|---|
| `sandbox_slots` | `AGENT_MAX_CONCURRENT_RUNS` (else 1) | 0..8 |
| `eval_slots` | 1 | 0..4 |
| `llm_job_threads` | 16 | 1..32 |
| `post_run_slots` | 1 | 0..1 (the analyzer workspace is shared) |
| `draining` | false | |

Staff edit them on `/endpoints` (capacity card), at `PATCH /api/execution/settings/`, or in the admin. The rule:

```
sandbox_slots × (AGENT_SANDBOX_MEM_LIMIT + 256m) + eval_slots × EVAL_SLOT_MEM
  + post_run_slots × ANALYSIS_WORKSPACE_MEM + APPS_DIND_APP_RESERVE ≤ 0.95 × APPS_DIND_MEM_LIMIT
```

Defaults: `AGENT_SANDBOX_MEM_LIMIT` 3g, `EVAL_SLOT_MEM` 1536m, `ANALYSIS_WORKSPACE_MEM` 1g, `APPS_DIND_APP_RESERVE` 1g, `APPS_DIND_MEM_LIMIT` 4g. `APPS_DIND_MEM_LIMIT` is authoritative (keep it equal to apps-dind's `mem_limit`; `docker info` inside apps-dind reports host RAM). A PATCH that breaks the rule is refused with the arithmetic (`{detail, arithmetic, mem_limit, required}`), except for the minimal one-sandbox / one-evaluation configuration. When even that does not fit (the default 4g apps-dind), sandbox runs and evaluations **take turns** (`exclusive_eval`), and post-runs wait while either is busy. A rule broken only by the post-run term is allowed; post-runs then wait while sandbox and evaluation are busy.

## Leases and reconciliation

`reconcile()` runs at leader start and every 15 s (each step isolated: one failing never stops the rest):

0. **Lost jobs** (every tick): owned by this executor, not in the registry, stage entered > 30 s ago → `infra_retry(executor_lost)`; a lost evaluation goes back to `eval_queued`.
1. **Expired leases** (and every lease owned by another executor id: only one leader exists): sandbox runs have their containers reaped, then `executor_lost`; evaluations go back to `eval_queued` at most `EVAL_MAX_REQUEUES` (2) times, then end ungraded (`evaluation.status = infra_error`); cancelled rows just release the lease.
2. **Orphan containers** (every 4th pass): containers labelled `llmlab.agent-run` / `llmlab.evaluation` whose job is not running under a live lease and not in the registry, older than 2 minutes (`llmlab.started`).
3. Pump leases of dead instances (`requeue_pump_orphans`), and LLM request leases (`inference.requests.requeue_expired`).
4. Stale relay sessions end (`timeout`, `inference.tokens.reap_stale_sessions`: acked leases use an attempt); a standby session is promoted.
5. Sessions of expired relay tokens end (`token_expired`, `inference.tokens.reap_expired_tokens`).
6. Executor instances silent for 90 s are marked stopped.
7. Completion sweep: running experiments with no active job complete (`maybe_complete_experiment`, idempotent: one post-run per completion); pending jobs and generation runs of cancelled experiments are cancelled (rows an experiment cancel skipped while a claim held them).
8. Schedules more than one period overdue are fast-forwarded (no burst; `experiment_automation.fast_forward_schedules`).

## Retries

| Reason | When | Budget |
|---|---|---|
| `executor_lost` | lease expired, lost-job check, executor restart | `EXECUTOR_LOST_RETRIES` (10), counted in `execution.executor_lost_count`; does not consume `infra_retries`; retried after 5 s |
| `submit_failed` | the claimed job could not be handed to a pool | `infra_retries` |
| `infra` | sandbox/apps-dind failures; no successful LLM call and a transport error (gateway 502, proxy 503/504, relay lease expiry); scaffolding `LLMAccessError` with a retryable code before any successful call | `experiment.infra_retries` (default 2; ad-hoc 2), backoff `30 s × 2^n` |
| `infra_interrupted` | local endpoints only: the run ended after the proxy returned an infra error, or a scaffolding call failed after a successful one | as `infra`; exhausted → COMPLETED with validity `invalid: infra_interrupted` |

A retry requeues the **same row** (`attempt + 1`, same snapshot, seed and cell): pairing survives. Artifacts of the attempt are deleted; agent evidence of attempt `n > 0` lives under `GENERATION_RUNS_ROOT/<job_id>/a<n>/`; sandbox names carry `-a<attempt>`. Anything else (model 4xx, OpenRouter runs with a successful call) is a sample or a failure, never retried. `infra` and `infra_interrupted` failures feed the endpoint's circuit breaker (3 within 5 minutes open it for 60 s).

The executor only claims jobs of **running** experiments. A manual Retry of a failed or cancelled cell (`POST /api/generation/jobs/{id}/retry/`) therefore reopens a completed or cancelled experiment, as a top-up does; a paused one stays paused until it is resumed.

Validity notes added by the executor (kept through the evaluation stage): `infra_retried` (warn, attempt > 0), `relay_interruptions` (info), `contended` (warn, LLM queue wait > 10 % of the wall clock), `infra_interrupted` (exclude).

## Timeout chain (local models)

Per endpoint, from `request_timeout_s` (default 900 s, 30..7200; `inference.timeouts.chain`):

| Timeout | Formula | Default |
|---|---|---|
| Request deadline | timeout × 3 (max attempts) + 600 s | 3300 s |
| Gateway socket timeout | timeout + 300 s | 1200 s |
| Proxy non-stream wait | gateway − 60 s (then 504; the gateway retries and re-attaches by dedupe key) | 1140 s |
| Harness client timeout | gateway × 5 (4 gateway retries + 1) + 60 s | 6060 s |

The chain guarantees the harness never gives up before the endpoint does.

## Network settings

The compose files derive the executor's `LLM_PROXY_BIND` (`${LLM_PROXY_ADDR}:8090`, default `172.30.99.2:8090`), `LLM_PROXY_UPSTREAM` and `LLM_GATEWAY_EGRESS_SUBNET` (default `10.213.0.0/24`) from the same variables apps-dind's firewall uses. To move the networks (e.g. a subnet collision on the host), set them together in the shell or `.env` used by compose: `LLMPROXY_SUBNET`, `LLM_PROXY_ADDR` and `APPS_DIND_LLMPROXY_ADDR` (both inside that subnet), and optionally `LLM_GATEWAY_EGRESS_SUBNET`. Recreate apps-dind and the executor afterwards.

## Production compose

`docker-compose.production.yml` has no apps-dind: sandboxes and gateways run on the host daemon, which cannot reach the internal `llmproxy` network. Local-model **agent** runs are therefore refused there (`LLM_LOCAL_AGENT_RUNS`, default on only when `APPS_DOCKER_HOST` is set; the job fails at once with a non-retryable error). OpenRouter agent runs and local-model scaffolding runs (the executor calls the relay/endpoint itself) work. Traefik routes `/api/` straight to django, so the adapter-node `BODY_SIZE_LIMIT` does not apply there.

## apps-dind firewall exception

Gateways of local-model agent runs join the nested network `llmlab-gw-egress` (bridge `llmlab-gwegr`, `10.213.0.0/24`, inter-container traffic off). The only new path out of apps-dind is `llmlab-gwegr → <LLM_PROXY_ADDR>:8090` (the executor's proxy on the internal `llmproxy` network); the rest of that subnet and apps-dind's own `llmproxy` address are rejected (`compose/local/apps-dind/entrypoint.sh`).

## API

All under `/api/execution/`:

| Route | Who | What |
|---|---|---|
| `GET /capacity/` | any user | Executor status (online, leader, `code_stale`, draining and what is left), effective slots, memory limits and warnings, lane usage, visible endpoints (other tenants' models only as counts), own experiments (staff: all) with an ETA from the last 30 minutes' throughput. Patched live by the SSE `capacity` channel. |
| `GET /queue/?experiment_id&endpoint&limit` | any user | The caller's next pending jobs (limit 1..200, default 50) with `blocked_reason` (a dry run of the scheduler, no locks) and `others_ahead` per endpoint (a count only). |
| `GET/PATCH /settings/` | staff | Slot settings and drain; a PATCH that breaks the memory rule is a 400 with the arithmetic. |

## Test patch targets

- Views keep `backend.generation.api.views.custom.dispatch_job`, `…custom.has_resolvable_key`, `…jobs.dispatch_job` and `…jobs.dispatch_evaluation` (all now enqueue: nothing runs in the web process).
- To keep the doorbell out of Redis: `backend.execution.api.wake`.
- To assert ordering, read `GenerationJob.sequence` / `queued_at` (there is no `run_queue` any more); experiment launches set `sequence` from `execution.sequence.cell_sequence`.
- To run jobs in a test: `Executor(sync_pools=True, start_proxy=False, start_pumps=False, redis_client=<fake>)`, then `acquire_leadership()`, `startup()` and `tick()` (or `run()` with `max_ticks` / `stop_when_idle`); or `GenerationService().execute(job)` (inline claim, evaluation inline).
- LLM calls: `backend.inference.chat.chat` and `backend.inference.access.resolve_access`; agent sandboxes: `backend.generation.services.agent.runner.AgentSandbox` and `backend.inference.gateway_wiring.for_job`; evaluation: `backend.generation.services.evaluation.runner.evaluate_job` (takes a `save=` keyword).

More on running the test suite is in the [Development guide](/docs/development-guide).

## Manual smoke test

Recreating apps-dind and starting the executor interrupts running work; do it when the stack is idle.

1. `docker compose up -d executor` (see "Compose commands" for the file set; the executor starts on the existing image, no `--build` needed); `manage.py executor status` (through `/entrypoint`, as above) shows `online: true`; `/api/execution/capacity/` shows the leader and no stale code.
2. Launch a 1-model × 1-app OpenRouter agent experiment: the job goes pending → running → eval_queued → evaluating → completed; the sandbox slot frees before evaluation starts.
3. `docker compose restart executor` during a run: the job is requeued as `executor_lost` (attempt 1) and completes; no orphan containers remain.
4. Local endpoint: see [Local models](/docs/LOCAL_MODELS) (relay, fake Ollama), then run one agent experiment per harness and check `calls.jsonl`, lanes and evaluation.
5. In apps-dind: a container on `bridge` cannot reach `<LLM_PROXY_ADDR>:8090`; one on `llmlab-gw-egress` can; from that network `postgres:5432`, `django:8000` and the apps-dind daemon port are refused, and another gateway's `:8080` is unreachable.
