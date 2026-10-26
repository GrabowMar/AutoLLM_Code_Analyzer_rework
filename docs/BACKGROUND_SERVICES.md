# Background services

Which work runs where: the executor process, threads inside the web process, Celery workers and beat, and the Redis-backed realtime stream.

## Who runs what

| Work | Runs in | Survives a `django` restart? |
| --- | --- | --- |
| Generation jobs (agent, scaffolding, custom) and their evaluation | `executor` service, claimed from Postgres | Yes |
| Experiment schedules (top up, add replicates, clone and run) | `executor`, checked every 30 s | Yes |
| Experiment post-runs (analyses over completed jobs, then the report) | `executor` post-run pool | Yes; a lost lease is re-claimed |
| Internal LLM proxy and direct-endpoint pumps for local models | `executor` threads | Yes |
| Interactive analysis runs | Daemon thread in `django` (`analysis.services.runner.dispatch`) | No |
| Analyzer workspace provisioning, tool installs | Daemon threads in `django` | No |
| Report generation, container start/stop/build actions | Daemon threads in `django` | No |
| Catalog enrichment after a model sync | Celery `enrich_catalog_task` | Yes (queued in Redis) |
| Outbound webhook deliveries | Celery `deliver_webhook`, retried with backoff | Yes |
| Webhook sweep for missed status transitions | Celery beat, every 60 s | Yes |

The web process never runs generation jobs. Views write rows (`queued_at`, lane, priority) and, after the transaction commits, push a token onto the Redis list `executor:wake` (the doorbell) so the executor claims them without waiting for its next tick. See [Execution engine](/docs/EXECUTION) for the scheduler, slots, leases and drain procedure.

Threads in `django` die with the process. In the local stack that includes every `uvicorn --reload` triggered by a backend edit. A killed analysis run stays `running`; cancel it, or use **System → Maintenance → Clear Stuck Analysis Tasks** (staff) to fail runs untouched for an hour.

## The executor

One instance, compose service `executor`, command `python manage.py run_executor` after `python manage.py wait_for_migrations` (it never migrates itself). It uses the same image and env files as `django` and talks to the apps daemon for sandboxes and evaluation containers.

| Thread or pool | Job |
| --- | --- |
| Main loop | Wait on `executor:wake` up to `EXECUTOR_TICK_SECONDS` (2 s), requeue lost jobs, claim work, reconcile every 15 s, schedules and post-runs every 30 s, purge old LLM request rows daily |
| Heartbeat | Every `EXECUTOR_HEARTBEAT_SECONDS` (20 s): instance `last_seen_at`, lease renewal for jobs it is running |
| `sandbox`, `eval`, `llm`, `post`, `schedule` pools | Agent runs, evaluations, scaffolding/custom jobs, post-runs, schedule actions |
| LLM proxy | `inference.proxy_server` on `LLM_PROXY_BIND` (`172.30.99.2:8090` on the internal `llmproxy` network) |
| Direct pumps | Workers that forward queued LLM requests to direct local endpoints |

It has `restart: unless-stopped` and exits with code 1 when it loses the Postgres leader lock, when the LLM proxy cannot bind, or when migrations are still pending after 10 minutes. It does not autoreload; restart it after backend edits. Check it with:

```bash
just manage executor status     # online, draining, running jobs, safe_to_restart
just manage executor drain      # stop claiming before a restart
just manage executor resume
```

Staff can also see and change its slots in the capacity strip and `GET`/`PATCH /api/execution/settings/`.

## Celery services

Three compose services share the django image and the Redis broker (`REDIS_URL`, also the result backend):

| Service | Command | Role |
| --- | --- | --- |
| `celeryworker` | `celery -A config.celery_app worker` | Runs the tasks below. Locally `CELERY_WORKER_CONCURRENCY=2` |
| `celerybeat` | `celery -A config.celery_app beat` | Periodic tasks from `django_celery_beat`'s database scheduler, editable in the Django admin |
| `flower` | `celery ... flower` | Task dashboard on :5555, login from `CELERY_FLOWER_USER` / `CELERY_FLOWER_PASSWORD` |

| Task | Module | Triggered by |
| --- | --- | --- |
| `enrich_catalog_task` | `backend/llm_models/tasks.py` | `POST /api/models/sync/`, after the commit. Also available synchronously as `manage.py enrich_models` |
| `deliver_webhook` | `backend/integrations/tasks.py` | A webhook event for an enabled subscription. Up to 3 retries at 15, 60 and 240 s on 5xx, 429 or network errors |
| `sweep_webhook_events` | `backend/integrations/tasks.py` | Beat, every 60 s. Registered as the periodic task "integrations: sweep webhook events" after every migrate. Emits events for terminal transitions from the last 30 minutes that bypassed `post_save`, and re-dispatches deliveries that never got a first attempt |
| `run_pipeline_task`, `run_scheduler_tick` | `backend/automation/tasks.py` | Legacy automation; nothing calls or schedules them any more |
| `get_users_count` | `backend/users/tasks.py` | Template example, unused |

Without a worker the app still works: model sync succeeds without enrichment, and webhook deliveries wait in Redis (and stay `pending` in the database) until a worker starts. Without beat the sweep stops, so deliveries that could not be queued (Redis down) are never retried.

> [!NOTE]
> Experiment schedules do not need Celery beat. The old `run_scheduler_tick` / `runautomationscheduler` path only served legacy automation pipelines; schedules now fire inside the executor. See [Experiments](/docs/EXPERIMENTS#automation) and [Automation guide](/docs/AUTOMATION_GUIDE).

## Realtime events (SSE)

Progress reaches the browser over server-sent events, not polling.

- Publishers call `backend.realtime.events.publish(channel, event)`, which pushes JSON into Redis pub/sub. Helpers wrap the experiment, endpoint and capacity channels.
- `GET /api/realtime/stream?channels=a,b` (or repeated `channels=`) is an async Django view mounted outside ninja in `config/urls.py`. It authorizes every channel, closes its database connection, then streams from Redis on the event loop, so an open tab holds neither a worker thread nor a Postgres connection.
- Responses: 401 unauthenticated, 400 with no channels or more than 20, 403 when any channel is malformed, unknown or not yours.

| Channel | Who may subscribe | Published by |
| --- | --- | --- |
| `generation:<uuid>` | The job's creator or staff | Job status and results |
| `experiment:<uuid>` | The experiment's creator or staff | Executor tick events, job transitions |
| `endpoint:<id>` | Users who may use the endpoint; non-owners get `endpoint:<id>:public` | Local endpoint state |
| `analysis:<id>` | The run's creator or staff | Analysis run status |
| `runtime:<id>` | The container's creator, the owner of its generation job, or staff | Container actions |
| `capacity` | Any authenticated user | Executor slot usage, at most every 5 s |

## Redis roles

| Use | Keys |
| --- | --- |
| Celery broker and results | Celery defaults on `REDIS_URL` |
| Realtime pub/sub | The channels above |
| Executor doorbell | List `executor:wake` |
| LLM request queue doorbells | `backend/inference/doorbell.py` keys (prefix `INFERENCE_REDIS_PREFIX`, empty by default) |
| Django cache | Production only; local settings use an in-memory cache |

Postgres stays the source of truth: a Redis outage delays the executor by at most one tick and drops live updates, but loses no jobs.

## Runtime housekeeping

Two management commands keep the container fleet honest, useful from cron in production:

- `just manage sync_container_status`: updates `ContainerInstance.status` from the live Docker state.
- `just manage reconcile_app_routes`: rewrites the per-app Traefik route files and drops stale ones ([Deployment guide](/docs/deployment-guide)).

The executor's reconciler separately reaps lost agent sandboxes and evaluation containers on the apps daemon. **System → Maintenance** offers "Purge Orphan Containers" for container rows whose Docker container is gone, and "Clear Stuck Generation Jobs" as a manual backstop for executor-owned jobs whose lease expired long ago.
