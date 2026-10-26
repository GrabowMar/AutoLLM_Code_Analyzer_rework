# API reference

Authentication, conventions, and a router-by-router tour of the REST API at `/api/`.

The complete, always-current schema is the interactive OpenAPI UI at `/api/docs` (and `/api/openapi.json`), both staff-only. The API currently has 255 operations. This page covers what the schema can't tell you.

## Authentication

Two interchangeable schemes on most endpoints:

- **Bearer token**: create one under **Settings → Personal tokens** (`/users/settings#tokens`), then send `Authorization: Bearer llml_…`. Each use records the time and client IP.
- **Session cookie**: what the frontend uses. Sign-in flows are handled by allauth headless under `/_allauth/`.

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8001/api/models/
```

Exceptions:

| Endpoints | Auth |
| --- | --- |
| `/api/tokens/*` | Session only: a token cannot mint or revoke tokens |
| `/api/docs/*` (viewer tree/page/search) | Session only |
| `/api/export/*.csv`, `*.json`, `*.sarif` | Session only (see [Export endpoints](#export-endpoints)) |
| `/api/system/*` | Staff only (session or Bearer token) |
| `/api/realtime/stream` | Session only |
| `/api/users/bootstrap-status/`, `/api/users/bootstrap-admin/` | None: first-run superuser creation, `409` once any user exists |
| `/api/relay/v1/*` | Endpoint-scoped relay token (`llr_…`), not a user token |
| `/api/mcp` | Bearer token, or `/api/mcp/t/<token>` for `mcp`-scoped tokens; see [Integrations](/docs/INTEGRATIONS#1-mcp-server) |

> [!WARNING]
> Token scopes (`mcp`, `read`) are enforced only by the MCP endpoint. On the REST API a token with `read` scope still has full read-write access as its user.

## Conventions

- Errors are `{"detail": "<message>"}` with a fitting status code. Missing-OpenRouter-key errors (catalog sync/refresh, custom jobs) add a `remediation` field.
- Most IDs are UUIDs. Reports are addressed by their public ID (`rpt_…`) in `/api/reports/{report_id}/`. Endpoints, webhooks and presets use integer IDs, and catalog models are addressed by slug.
- List endpoints paginate with `page` / `per_page` query params and return the total alongside the items. The exact envelope varies by router.
- The experiments API answers refusals with `400 {"detail", "code", "errors": [...]}`: `code` is machine-readable (e.g. `missing_api_key`, `digest_drift`, `not_draft`) and `errors` lists every blocking issue.
- Owner-scoped resources (generation jobs, experiments and their conditions, schedules and post-runs, analysis runs, reports, endpoints, webhooks, runtime containers and their actions) answer 404 for other users, never 403. Staff see everything.
- Staff-only actions answer 403, for example catalog sync/import/refresh/delete, direct endpoints, cloud provider connections and executor settings.

## Routers

All routers are mounted in `config/api.py`.

| Router | What it covers |
| --- | --- |
| `/users/` | First-run bootstrap, current user (`me/`, `me/activity/`), user list and detail (both scoped to yourself) |
| `/tokens/` | Personal API token list, create (raw token shown once), revoke |
| `/credentials/` | Encrypted per-user keys for `openrouter`, `huggingface`, `github`: status, set, delete, test |
| `/models/` | Catalog list/filter, stats, providers, comparison (up to 6), detail, related, insights, live provider endpoints. Staff-only sync, import, delete. JSON/CSV export. See [Models reference](/docs/MODELS_REFERENCE) |
| `/generation/` | Jobs (agent, scaffolding, custom, legacy copilot) with cancel / retry / replay / rerun / evaluate, artifacts, run records and export. Also batches, agent harnesses and config, run presets, blocks, profiles (bundles), stacks, app specs, packages and starters. See [Generation process](/docs/GENERATION_PROCESS), [Template packages](/docs/TEMPLATE_PACKAGES) |
| `/generation/experiments/` | Experiment design with a condition cross-product, stateless preview, launch / top-up, pause / resume / cancel / archive / clone, status, cell grid, results, batches, export, schedules (plus `run-now`) and post-run; `/generation/cron-preview/`. See [Experiments](/docs/EXPERIMENTS#api-summary) |
| `/inference/` | LLM endpoints (OpenRouter, Ollama, OpenAI-compatible; direct or relay): CRUD, refresh, test probes, unload, per-model settings, relay tokens (create/rotate/revoke), sessions, metrics, install commands. See [Local models](/docs/LOCAL_MODELS) |
| `/execution/` | Executor capacity and lanes, the pending-job queue with blocked reasons (filtered to what you may see), staff-only slot settings and drain. See [Execution](/docs/EXECUTION) |
| `/analysis/` | Analysis runs: create (one, or a batch), preview, list and stats, detail, cancel, re-run, bulk cancel/delete, tool raw output, findings and facets. See [Analysis pipeline](/docs/ANALYSIS_PIPELINE#runs-api) |
| `/analyzers/` | Tool catalog and catalog metadata, tool/technology coverage, your workspace (provision / start / stop / delete) and its installed tools (install, bulk install, update, update-all, config, uninstall, test). See [Analyzer guide](/docs/ANALYZER_GUIDE) |
| `/statistics/` | Dashboard, overview, severity, trends, models, tools, top findings, recent activity, code generation, analyzer health; study tables (`study/breakdowns`, `healing`, `tools`, `accounting`). See [Study tables](/docs/STUDY_TABLES) |
| `/rankings/` | Rankings, top models, status, severity-weight sensitivity, refresh, CSV export |
| `/reports/` | Report list, generate, detail, data, export, delete |
| `/runtime/` | Containers for generated apps: build for a job, smoke test, start / stop / restart / remove, logs, health, inspect, whitelisted diagnostic exec, async action status, Docker info. A container is yours if you built it or own its generation job; build and smoke take only your own jobs |
| `/export/` | File downloads and review samples, see below |
| `/docs/` | This docs viewer: tree, categories, search, page |
| `/system/` | Staff-only snapshots (host, containers, Redis, Celery, DB, app stats) and maintenance actions (clear stuck analysis/generation, purge orphan containers, clear caches) |
| `/integrations/` | Cloud provider presets (test key, connect), webhooks (event types, CRUD, test, deliveries), GitHub status and export, MCP client config. See [Integrations](/docs/INTEGRATIONS) |

There is no `/automation/` router any more: schedules and post-runs live on experiments.

## Export endpoints

`GET /api/export/` file endpoints: `findings.csv`, `findings.json`, `findings.sarif`, `generation-jobs.csv`, `generation-jobs.json`, `analysis-tasks.csv`, `analysis-tasks.json`, `reports.csv`, `reports.json`. Rows are scoped to your own objects (staff: all), filterable by query params and capped at `limit` (default 10 000, hard cap 50 000). Large CSV exports stream.

> [!WARNING]
> These file exports authenticate via **session cookie only**. Bearer tokens do not work here: the endpoints opt out of the standard auth stack and check the session directly. Download them from a logged-in browser, or with the session cookie in `curl`.

The SARIF export makes findings importable into GitHub code scanning and most security tooling.

Two more export endpoints use the normal auth stack, so Bearer tokens work:

- `GET /api/export/review-sample.zip?experiment_id=|batch_id=&size=30&stratify=model&seed=0&blind=true` draws a seeded, stratified sample of apps with their code, specs and blank label sheets for manual review.
- `POST /api/export/review-sample/score/` (multipart `file`) scores the filled-in ZIP against the automated verdicts. It reports precision with Wilson intervals, Cohen's kappa for the functional verdict and Spearman rho for requirement compliance.

## Relay protocol

`/api/relay/v1/` (outside the ninja API) is the HTTPS protocol a relay on a GPU box speaks: `hello`, `inventory`, a long-poll `poll` that claims queued LLM requests, `requests/<id>/result` and `bye`. The same prefix serves the relay's install files: `relay.py`, `Dockerfile` and `llmlab-relay.service`. Protocol calls authenticate with an endpoint-scoped relay token (`llr_…`), not a user token. Install steps are in [Local models](/docs/LOCAL_MODELS).

## MCP endpoint

`/api/mcp` (outside the ninja API) is a Model Context Protocol server that replays tool calls in-process against this API as the token's user. See [Integrations](/docs/INTEGRATIONS#1-mcp-server).

## Realtime stream

`GET /api/realtime/stream?channels=experiment:<id>,capacity` is a server-sent-events endpoint outside the ninja API (it streams). It is an async view: it checks every channel, closes its database connection, and then holds no worker thread or database connection while the stream is open. A `: heartbeat` comment goes out every 15 s. `channels` may be comma-separated or repeated.

- `401` unauthenticated, `400` without channels or with more than 20, `403` when any channel is malformed, unknown or not yours.
- Channels and who may subscribe:

| Channel | Allowed for | Events |
|---|---|---|
| `generation:<uuid>` | the job's creator or staff | job status and phase |
| `experiment:<uuid>` | the experiment's creator or staff | `job` (every transition), `tick` (rollups, at most every 2 s), `state`, `post_run` |
| `endpoint:<id>` | anyone who may use the endpoint; non-owners are moved to `endpoint:<id>:public` (counts only, no IPs, hostnames or errors) | `endpoint`, `session` |
| `analysis:<uuid>` | the analysis run's creator or staff | `status` (run), `progress` (phase), `result` (a tool started / finished) |
| `runtime:<uuid>` | the container's creator, the owner of its generation job, or staff | container status |
| `capacity` | any signed-in user (aggregates only) | `capacity` |

The publishing side is described in [Background services](/docs/BACKGROUND_SERVICES).
