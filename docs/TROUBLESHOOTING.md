# Troubleshooting

Symptoms, causes and fixes for the most common local and production failures.

Commands below assume the local stack through `just` (or `docker compose` with `COMPOSE_FILE=docker-compose.local.yml`). Subsystem pages have their own troubleshooting sections: [Local models](/docs/LOCAL_MODELS#10-troubleshooting), [Execution engine](/docs/EXECUTION), [Analyzer guide](/docs/ANALYZER_GUIDE).

## Setup and access

### `manage.py` fails with "Set the DATABASE_URL environment variable"

`DATABASE_URL` is assembled from the `POSTGRES_*` variables by the image's `/entrypoint`, which `docker compose exec` skips. Use `just manage <cmd>` (a one-off container that goes through the entrypoint), or `docker compose exec django /entrypoint python manage.py <cmd>`.

### Port already in use

The local stack binds 8000 (frontend), 8001 (django), 8025 (mailpit) and 5555 (flower). Stop whatever holds the port, or override the mapping in a compose override file rather than editing `docker-compose.local.yml`.

### No verification email arrives

Locally nothing sends real email: everything lands in mailpit at http://localhost:8025, and verification is optional (`ACCOUNT_EMAIL_VERIFICATION=optional`). If the link in the email points at the wrong host, set `FRONTEND_PUBLIC_ORIGIN` to the URL you actually open. In production, see the email gap in [Deployment guide](/docs/deployment-guide#path-b-production-compose).

### 403, CSRF or CORS errors from a non-localhost address

`DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` in `.envs/.local/.django` must all include the origin you browse from; restart the stack after changing them. Production reads `DJANGO_CORS_ALLOWED_ORIGINS` and `DJANGO_CSRF_TRUSTED_ORIGINS` instead (note the prefix).

### Bearer token works everywhere except export URLs

By design: `/api/export/*` endpoints check the session user only. Download exports from a logged-in browser session; see [API reference](/docs/api-reference).

### Every API request hangs after a backend edit

`uvicorn --reload` can wait forever for open SSE streams before the old worker exits. The image's `/start` passes `--timeout-graceful-shutdown 5` to prevent this; images built before that change need `just build` and a recreate of `django`. As a stopgap, `docker compose restart django` (this kills running analyses).

## Models and keys

### "No OpenRouter API key is configured for your account"

Key resolution is per user: your own key from **Settings → API keys** wins; otherwise the global `OPENROUTER_API_KEY` applies only while `OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True`. Add a personal key, or set the global one in `.envs/.local/.django` and restart. Details in [Models reference](/docs/MODELS_REFERENCE).

### Model list is empty, or sync returns 403

The catalog is not seeded; it syncs from OpenRouter on demand. Sync is staff-only and always uses the staff user's **personal** key, never the global fallback, so add a key under Settings first. Model metadata enrichment runs afterwards on `celeryworker`; without a worker the models appear but lack models.dev / Hugging Face details (`just manage enrich_models` runs it inline).

### Runs on `:free` OpenRouter models never finish

Free models are rate-limited upstream and often answer 429. Agent runs then make no progress until they hit their wall-clock budget. Use a paid model for anything you need to finish.

## Generation and experiments

### Jobs stay pending

Generation jobs only run in the `executor` service. Check, in order:

1. It is running: `docker compose ps executor`, `just logs executor`.
2. It is not drained: `just manage executor status` shows `"draining": false`; otherwise `just manage executor resume`.
3. It is not waiting for migrations: its log says "Waiting for N migration(s)" until django's `migrate` finishes. In the devcontainer, django's `/start` (and so `migrate`) only runs once an editor attaches.
4. Capacity is free: the capacity strip on Experiments shows the executor state (online, draining, offline, on old code) and used versus total slots per lane. Slots are also limited by the apps-dind memory rule ([Execution engine](/docs/EXECUTION#slots-and-the-memory-rule)).

### The executor keeps restarting

It exits with code 1 when another instance holds the leader lock, when the lock connection drops (for example a Postgres restart), when the LLM proxy cannot bind `LLM_PROXY_BIND`, or when migrations are still pending after 10 minutes. `just logs executor` names the reason. A bind failure usually means the `llmproxy` network or its fixed address is missing; recreate the executor with the same compose files the stack was started with. See [Execution engine](/docs/EXECUTION#restart-policy-and-exits).

### The capacity strip says "Executor on old code"

The executor does not autoreload. Drain it, wait for `safe_to_restart`, restart, resume ([Execution engine](/docs/EXECUTION#drain-and-restart)).

### Local-model agent runs fail but OpenRouter runs work

Agent sandboxes reach a local model through the executor's LLM proxy via apps-dind's `llmproxy` attachment and firewall exception. An `apps-dind` container created before that network existed lacks both; recreate it (`docker compose up -d --force-recreate apps-dind`), which stops every sample app running inside it. In the production compose stack there is no apps-dind, so these runs are refused by design (`LLM_LOCAL_AGENT_RUNS` is off). Scaffolding runs on local models work in both.

### Generation library looks empty

Library data seeds after every migrate. If the UI is still empty, look for "Generation template seeding failed" or "Run preset seeding failed" in `just logs django`: malformed YAML or JSON under `backend/generation/data/` is logged, not raised, so `migrate` still succeeds. Validate with `just manage lint_generation_data`, fix the file, then `just manage seed_generation_templates`.

### Experiment schedules never fire

Schedules fire inside the executor (checked every 30 s), not through Celery beat. They do not fire while the executor is drained or down, they skip a fire while the experiment still has active jobs (when "skip if active" is on), and missed fires are not replayed in a burst. Cron expressions must be at least 15 minutes apart. See [Experiments](/docs/EXPERIMENTS#automation).

## Analysis and apps

### An analysis run is stuck in "running"

Interactive analysis runs execute on threads inside the django container. Restarting it, or a `uvicorn --reload` after a backend edit, kills them. A live run refreshes a heartbeat every 30 seconds, so within about 5 minutes of the restart the orphaned run is marked `failed` ("Interrupted: …") the next time runs are listed or opened, or when staff use **System → Maintenance → Clear Stuck Analysis Tasks**. Start a new run. Cancelling stops a live run before its next tool and kills the tool in flight. Post-run analyses of experiments run in the executor and are re-claimed after a restart.

### Analysis runs fail to start

- Django must reach the `apps-dind` daemon: `just logs apps-dind` should show `API listen on [::]:2376`, and `docker compose exec apps-dind docker ps` should list an `analyzer-*` container once provisioned. Workspaces and sample apps live there, not in the host's `docker ps`.
- The first run builds the `backend/analyzer-base:latest` image, which can take minutes or fail on network hiccups; `just logs django` shows the build.
- Install tools one at a time from the Analyzers page; concurrent installs into a workspace that is still provisioning can race.
- Dynamic tools need the app's container running; start it first. See [Analyzer guide](/docs/ANALYZER_GUIDE).

### A generated app shows a blank page under `/apps/<name>/`

Path-proxy mode (the local default, `APPS_PROXY_PATH=True`) serves the app under a prefix, but most generated SPAs load root-absolute `/assets/...` and call `/api/...`, which hit the platform instead. The app itself is usually fine. Switch to subdomain routing ([Deployment guide](/docs/deployment-guide#sample-app-routing)) to view it properly.

### apps-dind runs out of memory or disk

apps-dind is capped at `APPS_DIND_MEM_LIMIT` (4 GB) and hosts sample apps, agent sandboxes (3 GB each by default) and analyzer workspaces together. Stop apps you no longer need before agent runs. It prunes unused images older than a week every hour; to reclaim more, `docker compose exec apps-dind docker system prune`.

### Webhooks are not delivered

Deliveries run on `celeryworker`; check `just logs celeryworker` and that the subscription is enabled. Deliveries that could not even be queued are re-dispatched by the beat sweep, so `celerybeat` must run too. See [Integrations](/docs/INTEGRATIONS).

## Production

### Production containers start with debug settings or crash on `debug_toolbar`

`docker-compose.production.yml` sets `DJANGO_SETTINGS_MODULE=config.settings.production` on every Django-image service; without it `config/asgi.py`, `manage.py` and the Celery app fall back to `config.settings.local`, which imports `debug_toolbar` (not in the production image). If you run the image some other way, or a service overrides `environment:`, set it there. `DJANGO_SECRET_KEY` is the only other required variable; `SENTRY_DSN` is optional.

### The site returns 502 or 404 behind Traefik

Traefik routes the frontend to `frontend:3000`, so `PORT` in `.envs/.production/.frontend` must stay `3000`. The admin is routed at whatever `DJANGO_ADMIN_URL` is (Traefik reads it from `.django` at start), so restart Traefik after changing it.

### A generated app's subdomain does not resolve

Check, in order: the DNS wildcard `*.APPS_DOMAIN` points at the host; Traefik obtained the wildcard certificate (DNS-01 credentials in `.envs/.production/.traefik`, `TRAEFIK_DNS_PROVIDER` set); the route file exists (`python manage.py reconcile_app_routes` rewrites them). See [Deployment guide](/docs/deployment-guide).

## Start over

```bash
just prune   # removes containers AND volumes: database, apps-dind images and certs
just bootstrap && just build && just up
```
