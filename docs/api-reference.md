# API reference

Authentication, conventions, and a router-by-router tour of the REST API at `/api`.

The complete, always-current schema is the interactive OpenAPI UI at `/api/docs` (staff accounts only). This page covers what the schema can't tell you.

## Authentication

Two interchangeable schemes on every endpoint (except where noted):

- **Bearer token** — create one under Settings → Personal Tokens (`POST /api/tokens/`), then send `Authorization: Bearer <token>`. Token usage (including client IP) is tracked.
- **Session cookie** — what the frontend uses; sign-in flows are handled by allauth headless under `/_allauth/`.

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8001/api/models/
```

## Conventions

- Errors are `{"detail": "<message>"}` with a fitting status code.
- IDs are UUIDs; reports additionally have a shareable public ID with an `rpt_` prefix.
- List endpoints paginate with `page` / `per_page` query params and return the total alongside the items.
- The experiments API answers refusals with `400 {"detail", "code", "errors": [...]}`: `code` is machine-readable (e.g. `missing_api_key`, `digest_drift`, `not_draft`) and `errors` lists every blocking issue.
- Owner-scoped resources (experiments, their conditions and schedules, jobs, reports) answer 404 for other users, never 403.

## Routers

| Router | What it covers |
| --- | --- |
| `/users/`, `/tokens/`, `/credentials/` | Profile, API token CRUD, encrypted provider keys (OpenRouter) |
| `/models/` | Model catalog list/filter and OpenRouter sync — [Models reference](/docs/MODELS_REFERENCE) |
| `/generation/` | Jobs, batches, run presets, app specs, profiles, stacks, packages — [Generation process](/docs/GENERATION_PROCESS), [Template packages](/docs/TEMPLATE_PACKAGES) |
| `/generation/experiments/` | Experiments: one-call design with a condition cross-product, stateless preview, launch / top-up, pause / resume / cancel / clone, status, cell grid, results (app-blocked statistics), export, schedules and post-run — [Experiments](/docs/EXPERIMENTS#api-summary) |
| `/inference/` | LLM endpoints (OpenRouter, Ollama, OpenAI-compatible): CRUD, relay tokens, sessions, models, probes, metrics, install commands — [Local models](/docs/LOCAL_MODELS) |
| `/execution/` | Executor capacity and lanes, the pending-job queue with blocked reasons, staff-only slot settings and drain — [Execution](/docs/EXECUTION) |
| `/analysis/`, `/analyzers/` | Runs and findings; the tool catalog, workspace, and tool install/test — [Analyzer guide](/docs/ANALYZER_GUIDE) |
| `/statistics/` | Dashboard aggregates |
| `/rankings/` | Model scores and benchmarks, including a refresh action |
| `/reports/` | Saved report CRUD and generation |
| `/runtime/` | Containers for generated apps: build, start/stop, status |
| `/export/` | File downloads — see below |
| `/docs/`, `/system/` | This docs viewer's tree/page/search; staff-only system snapshots |

## Export endpoints

`GET /api/export/` endpoints return files: `findings.csv`, `findings.json`, `findings.sarif`, `generation-jobs.csv`, `generation-jobs.json`, `analysis-tasks.csv`, `analysis-tasks.json`, `reports.csv`, `reports.json`. Rows are filterable by query params and capped at `limit` (default 10 000, hard cap 50 000); large CSV exports stream.

> [!WARNING]
> Export endpoints authenticate via **session cookie only** — Bearer tokens do not work here. They opt out of the standard auth stack and check the session directly, so download them from a logged-in browser (or with the session cookie in `curl`).

The SARIF export makes findings importable into GitHub code scanning and most security tooling.

## Relay protocol

`/api/relay/v1/` (outside the ninja API) is the HTTPS protocol a relay on a GPU box speaks: `hello`, `inventory`, a long-poll `poll` that claims queued LLM requests, `requests/<id>/result` and `bye`. It authenticates with an endpoint-scoped relay token (`llr_…`), not a user token. The relay script and its install steps are in [Local models](/docs/LOCAL_MODELS).

## Realtime stream

`GET /api/realtime/stream?channels=experiment:<id>,capacity` is a server-sent-events endpoint living outside the ninja API (it streams). It is an async view: it checks every channel, closes its database connection, and then holds no worker thread or database connection while the stream is open. A `: heartbeat` comment goes out every 15 s.

- `401` unauthenticated, `400` without channels or with more than 20, `403` when any channel is malformed, unknown or not yours.
- Channels and who may subscribe:

| Channel | Allowed for | Events |
|---|---|---|
| `generation:<uuid>` | the job's creator or staff | job status and phase |
| `experiment:<uuid>` | the experiment's creator or staff | `job` (every transition), `tick` (rollups, at most every 2 s), `state`, `post_run` |
| `endpoint:<id>` | anyone who may use the endpoint; non-owners are moved to `endpoint:<id>:public` (counts only, no IPs, hostnames or errors) | `endpoint`, `session` |
| `analysis:<uuid>` | the analysis run's creator or staff | run status |
| `runtime:<uuid>` | the container's creator or staff | container status |
| `capacity`, `dashboard` | any signed-in user (aggregates only) | `capacity`, dashboard updates |

The publishing side is described in [Background services](/docs/BACKGROUND_SERVICES).
