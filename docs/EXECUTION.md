# Execution engine

Every generation job (agent, scaffolding, custom), its evaluation, experiment
schedules and post-runs run in one process: the **executor**
(`python manage.py run_executor`, compose service `executor`). The web process
(`django`) only writes rows and rings a Redis doorbell after the transaction
commits. Code lives in `backend/execution/`.

## Process

| Part | What it does |
|---|---|
| Leader lock | `pg_try_advisory_lock(EXECUTOR_LEADER_LOCK_KEY)` on a dedicated connection at start; a second instance exits with code 1. The lock is re-checked in `pg_locks` every tick; if it is gone (Postgres restart, dropped connection) the executor stops claiming, expires its own leases and exits with code 1; the compose service has `restart: unless-stopped`, so Docker starts it again. It is a guard, not high availability: exactly one executor runs. |
| Main loop | `BLPOP executor:wake` for at most `EXECUTOR_TICK_SECONDS` (2 s), then: lost-job check → `Scheduler.tick` → submit claims to pools. `reconcile()` every 15 s, schedules and post-runs every 30 s, LLM request purge daily. `ExecutorSettings` is re-read every tick. A Redis error sleeps one tick (never busy-spins); Postgres stays the only source of truth. |
| Heartbeat | Every `EXECUTOR_HEARTBEAT_SECONDS` (20 s): `ExecutorInstance.last_seen_at` + status blob (`config`: effective slots, open circuits, blocked reasons). Renews `lease_expires_at` (+`EXECUTOR_LEASE_SECONDS`, 90 s) **only for jobs in the in-process registry**, and leases of running post-runs. |
| Pools | `sandbox` (agent runs), `eval` (evaluations), `llm` (scaffolding / custom), `post` (post-runs and schedule actions). Admission uses `min(slots − used in DB, really free threads)`. |
| Live events | Every 2 s at most, only when changed: `tick` per active experiment, `endpoint:<id>` state per local endpoint (owner payload plus the redacted `:public` copy). `capacity` at most every 5 s; its `executor.code_stale` is true once the code on disk differs from what this executor started with. |
| LLM proxy, direct pumps | `inference.proxy_server.serve` and `inference.pump.run_direct_pumps` threads, started after leadership is won (see `docs/LOCAL_MODELS.md`). The proxy is required: if it cannot bind `LLM_PROXY_BIND` or stops, the executor exits with code 1 (a restart loop in `docker compose ps`/logs, not a silently "online" executor whose local agent runs all fail). The pumps supervise their own threads. |

The executor runs with `CONN_MAX_AGE=0`; every worker closes its DB connection
when it finishes, so idle threads hold none.

### Restart policy and exits

The `executor` service (both compose files) has `restart: unless-stopped`. It
exits with code 1, and Docker restarts it, when: a second instance holds the
leader lock; the lock is lost (Postgres restart, dropped lock connection); the
LLM proxy cannot bind; or `wait_for_migrations` gives up after 600 s (the
executor never migrates: locally django's `/start` does, which on dev1 only runs
once VS Code attaches; production `/start` does not migrate at all, so run
`manage.py migrate` after a deploy). Nothing is lost while it is down: jobs stay
`PENDING` and its leases are requeued as `executor_lost` on the next start.

### Compose commands

Commands below use bare `docker compose`. There is no default compose file in
the repo: run them through `just` (which exports
`COMPOSE_FILE=docker-compose.local.yml`), set `COMPOSE_FILE` yourself, or pass
the same `-f` files the stack was started with (dev1: the local file plus the
devcontainer overlays). Mixing a different file set recreates containers.

Locally the executor runs `/app/compose/local/django/start-executor` from the
bind-mounted repo, so it starts on an existing `llmlab/django:local` image. The
image's baked-in `/start` (django) picks up changes such as
`--timeout-graceful-shutdown 5` only after a rebuild (`just build`, or
`docker compose build django`); a rebuild marks django, celeryworker,
celerybeat and flower for recreation on the next `up`.

### Drain and restart

Backend edits do not reload the executor (no `--reload`). The capacity strip
shows "Executor runs older code" when the web process's code version differs
from the running executor's. To restart safely:

```bash
docker compose exec django python manage.py executor drain     # stop claiming jobs, post-runs and schedules
docker compose exec django python manage.py executor status    # wait for "safe_to_restart": true
docker compose restart executor
docker compose exec django python manage.py executor resume
```

`SIGTERM` (a plain restart) gives LLM calls 20 s, then expires every lease the
instance holds (jobs, post-runs, pump-leased LLM requests) and sets
`stopped_at`. Running sandboxes are not awaited: the next start reaps their
containers and requeues the jobs as `executor_lost` (same snapshot and seed;
this does not consume the experiment's infra retries).

## Job lifecycle

```
PENDING (queued_at set) ──claim──► RUNNING/running (lease) ──agent: sandbox done──► RUNNING/eval_queued (no lease)
   ▲                                  │ llm lane (or no evaluation)                    │ eval claim
   │  infra failure / executor_lost   ▼                                                ▼
   └───────── attempt+1, run_after ── COMPLETED / FAILED ◄─────────────────── RUNNING/evaluating (lease)
```

- `queued_at IS NOT NULL` means the executor owns the row. Thread-era rows
  (`queued_at IS NULL`) are never touched by the reconciler; requeue them
  explicitly (`execution.api.requeue_legacy_jobs`).
- Lanes: `(experiment or adhoc:<user>, endpoint)`, strict head-of-line in the
  frozen `sequence` (experiments) or enqueue order (ad-hoc). Across lanes the
  scheduler is work-conserving: priority, then fair share per user, then per
  experiment.
- Admission rules (blocked reason in brackets): free sandbox / llm thread
  (`sandbox_full`, `llm_threads_full`); endpoint enabled, online, circuit
  closed and below its job cap (`endpoint_offline`, `circuit_open`,
  `endpoint_full`); the live session serves the frozen `(name, digest)`
  (`model_not_served`); residency K (`residency_wait`, with a starvation guard
  after `EXECUTOR_STARVATION_S` that drains the endpoint for a swap); the
  experiment's `max_concurrency` (`experiment_cap`); Ollama prefetch
  (`warming_model`); `draining`. `retry_backoff` marks requeued jobs waiting
  for `run_after`.
- Every write a worker makes goes through `transitions.guarded_save`
  (`WHERE status='running' AND lease_owner=me AND attempt=a`); a 0-row result
  raises `LeaseLost` and the worker stops quietly. `finish_stage` runs as
  separate autocommit steps (job → LLM responses → batch → events →
  experiment completion → wake).
- Cancel (`transitions.cancel_jobs`): PENDING and `phase=running` become
  CANCELLED at once; the running worker keeps its `lease_owner` (and its slot)
  until it notices and exits. Jobs waiting for or in evaluation finish.

### Slots and the memory rule

Staff edit slots at `/api/execution/settings/` (or the admin). The rule:

```
sandbox_slots × (AGENT_SANDBOX_MEM_LIMIT + 256m) + eval_slots × EVAL_SLOT_MEM
  + post_run_slots × ANALYSIS_WORKSPACE_MEM + APPS_DIND_APP_RESERVE ≤ 0.95 × APPS_DIND_MEM_LIMIT
```

`APPS_DIND_MEM_LIMIT` is authoritative (keep it equal to apps-dind's
`mem_limit`). A PATCH that breaks the rule is refused with the arithmetic,
except for the minimal one-sandbox / one-evaluation configuration. When even
that does not fit (the default 4 g apps-dind), sandbox runs and evaluations
**take turns** (`exclusive_eval`), as they did before the executor, and
post-runs wait while either is busy. A rule broken only by the post-run term
is allowed; post-runs then wait while sandbox and evaluation are busy.

`ANALYSIS_WORKSPACE_MEM` (1g; the name predates the v2 engine) is the share the
rule reserves for a post-run's analysis. The analysis runs themselves execute in the
analysis executor ([Analysis pipeline](/docs/ANALYSIS_PIPELINE#durable-execution)),
and each tool container waits for memory on its own: the sum of the limits of every
running container on apps-dind plus the tool must fit `ANALYSIS_DIND_MEMORY_BUDGET`.
The two checks are independent, so keep both budgets below the apps-dind cap.

## Leases and reconciliation

`reconcile()` runs at leader start and every 15 s (each step isolated):

0. **Lost jobs** (every tick): owned by this executor, not in the registry,
   stage entered > 30 s ago → `infra_retry(executor_lost)`; a lost evaluation
   goes back to `eval_queued`.
1. **Expired leases** (and every lease owned by another executor id: only one
   leader exists): sandbox runs have their containers reaped, then
   `executor_lost`; evaluations go back to `eval_queued` at most
   `EVAL_MAX_REQUEUES` (2) times, then end ungraded (`evaluation.status =
   infra_error`); cancelled rows just release the lease.
2. **Orphan containers** (every 4th pass): containers labelled
   `llmlab.agent-run` / `llmlab.evaluation` whose job is not running under a
   live lease and not in the registry, older than 2 minutes (`llmlab.started`).
3. LLM request leases (`inference.requests.requeue_expired`), and pump leases
   of dead instances (`requeue_pump_orphans`).
4. Stale relay sessions end (`timeout`, `inference.tokens.reap_stale_sessions`:
   acked leases use an attempt); a standby session is promoted.
5. Sessions of expired relay tokens end (`token_expired`,
   `inference.tokens.reap_expired_tokens`).
6. Executor instances silent for 90 s are marked stopped.
7. Completion sweep: running experiments with no active job complete
   (`maybe_complete_experiment`, idempotent: one post-run per completion);
   pending jobs and generation runs of cancelled experiments are cancelled
   (rows an experiment cancel skipped while a claim held them).
8. Schedules more than one period overdue are fast-forwarded (no burst;
   `experiment_automation.fast_forward_schedules`).

## Retries

| Reason | When | Budget |
|---|---|---|
| `executor_lost` | lease expired, lost-job check, executor restart | `EXECUTOR_LOST_RETRIES` (10), counted in `execution.executor_lost_count`; does not consume `infra_retries`; retried after 5 s |
| `submit_failed` | the claimed job could not be handed to a pool | `infra_retries` |
| `infra` | sandbox/apps-dind failures; no successful LLM call and a transport error (gateway 502, proxy 503/504, relay lease expiry); scaffolding `LLMAccessError` with a retryable code before any successful call | `experiment.infra_retries` (default 2; ad-hoc 2), backoff `30 s × 2^n` |
| `infra_interrupted` | local endpoints only: the run ended after the proxy returned an infra error, or a scaffolding call failed after a successful one | as `infra`; exhausted → COMPLETED with validity `invalid: infra_interrupted` |

A retry requeues the **same row** (`attempt + 1`, same snapshot, seed and
cell): pairing survives. Artifacts of the attempt are deleted; agent evidence
of attempt `n > 0` lives under `GENERATION_RUNS_ROOT/<job_id>/a<n>/`; sandbox
names carry `-a<attempt>`. Anything else (model 4xx, OpenRouter runs with a
successful call) is a sample or a failure, never retried. Three infra
failures on one endpoint within 5 minutes open its circuit for 60 s.

The executor only claims jobs of **running** experiments. A manual Retry of
a failed or cancelled cell (`POST /jobs/{id}/retry/`) therefore reopens a
completed or cancelled experiment, as a top-up does; a paused one stays
paused until it is resumed.

Validity notes added by the executor (kept through the evaluation stage): `infra_retried` (warn, attempt > 0),
`relay_interruptions` (info), `contended` (warn, LLM queue wait > 10 % of the
wall clock), `infra_interrupted` (exclude).

## Timeout chain (local models)

Per endpoint, from `request_timeout_s` (default 900 s; `inference.timeouts.chain`):
request deadline = timeout × 3 + 600 s; gateway socket timeout = timeout + 300 s;
proxy non-stream wait = gateway − 60 s (then 504, the gateway retries and
re-attaches by dedupe key); harness client timeout = gateway × 5 + 60 s.

## Network settings

The compose files derive the executor's `LLM_PROXY_BIND`
(`${LLM_PROXY_ADDR}:8090`), `LLM_PROXY_UPSTREAM` and `LLM_GATEWAY_EGRESS_SUBNET`
from the same variables apps-dind's firewall uses. To move the networks (e.g.
a subnet collision on the host), set them together in the shell or `.env`
used by compose: `LLMPROXY_SUBNET`, `LLM_PROXY_ADDR` and
`APPS_DIND_LLMPROXY_ADDR` (both inside that subnet), and optionally
`LLM_GATEWAY_EGRESS_SUBNET`. Recreate apps-dind and the executor afterwards.

## Production compose

`docker-compose.production.yml` has no apps-dind: sandboxes and gateways run on
the host daemon, which cannot reach the internal `llmproxy` network. Local-model
**agent** runs are therefore refused there (`LLM_LOCAL_AGENT_RUNS`, default
on only when `APPS_DOCKER_HOST` is set; the job fails at once with a
non-retryable error). OpenRouter agent runs and local-model scaffolding runs
(the executor calls the relay/endpoint itself) work. Traefik routes `/api/`
straight to django, so the adapter-node `BODY_SIZE_LIMIT` does not apply there.

## apps-dind firewall exception

Gateways of local-model agent runs join the nested network `llmlab-gw-egress`
(bridge `llmlab-gwegr`, `10.213.0.0/24`, inter-container traffic off). The only
new path out of apps-dind is `llmlab-gwegr → 172.30.99.2:8090` (the executor's
proxy on the internal `llmproxy` network); the rest of that subnet and
apps-dind's own `llmproxy` address are rejected (`compose/local/apps-dind/entrypoint.sh`).

## API

- `GET /api/execution/capacity/`: executor status (online, leader, stale code,
  draining and what is left), slots, memory limits and warnings, lane usage,
  visible endpoints (other tenants' models only as counts), own experiments
  (staff: all) with an ETA from the last 30 minutes' throughput.
- `GET /api/execution/queue/?experiment_id&endpoint&limit`: the caller's next
  pending jobs with `blocked_reason` (a dry run of the scheduler, no locks)
  and `others_ahead` per endpoint (a count only).
- `GET/PATCH /api/execution/settings/` (staff).

## Test patch targets

- Views keep `backend.generation.api.views.custom.dispatch_job`,
  `…custom.has_resolvable_key`, `…jobs.dispatch_job` and
  `…jobs.dispatch_evaluation` (all now enqueue: nothing runs in the web process).
- To keep the doorbell out of Redis: `backend.execution.api.wake`.
- To assert ordering, read `GenerationJob.sequence` / `queued_at` (there is no
  `run_queue` any more); experiment launches set `sequence` from
  `execution.sequence.cell_sequence`.
- To run jobs in a test: `Executor(sync_pools=True, start_proxy=False,
  start_pumps=False, redis_client=<fake>)`, then `acquire_leadership()`,
  `startup()` and `tick()` (or `run()` with `max_ticks` / `stop_when_idle`);
  or `GenerationService().execute(job)` (inline claim, evaluation inline).
- LLM calls: `backend.inference.chat.chat` and
  `backend.inference.access.resolve_access`; agent sandboxes:
  `backend.generation.services.agent.runner.AgentSandbox` and
  `backend.inference.gateway_wiring.for_job`; evaluation:
  `backend.generation.services.evaluation.runner.evaluate_job` (takes a
  `save=` keyword).

## Manual smoke (dev1; needs the user's go-ahead to recreate apps-dind and start `executor`)

1. `docker compose up -d executor` (see "Compose commands" for the file set;
   the executor starts on the existing image, no `--build` needed);
   `manage.py executor status` shows
   `online: true`; `/api/execution/capacity/` shows the leader and no stale code.
2. Launch a 1-model × 1-app OpenRouter agent experiment: the job goes
   pending → running → eval_queued → evaluating → completed; the sandbox slot
   frees before evaluation starts.
3. `docker compose restart executor` during a run: the job is requeued as
   `executor_lost` (attempt 1) and completes; no orphan containers remain.
4. Local endpoint: see `docs/LOCAL_MODELS.md` (relay, fake Ollama), then run
   one agent experiment per harness and check `calls.jsonl`, lanes and evaluation.
5. In apps-dind: a container on `bridge` cannot reach `172.30.99.2:8090`; one on
   `llmlab-gw-egress` can; from that network `postgres:5432`, `django:8000` and
   `172.30.99.3:2376` are refused, and another gateway's `:8080` is unreachable.
