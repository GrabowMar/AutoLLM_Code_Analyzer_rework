# API reference

Authentication, conventions, and a router-by-router tour of the REST API at `/api/`.

The complete, always-current schema is the interactive OpenAPI UI at `/api/docs` (and `/api/openapi.json`), both staff-only. The API currently has 349 operations. This page covers what the schema can't tell you.

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
| `/api/human-eval/portal/*` | The participant cookie (`__Host-llml_eval`, or `llml_eval` on http origins) and/or a session user, with CSRF on every unsafe method; no Bearer tokens, not exposed through MCP. `join/{token}/`, `resume/`, `logout/` and `me/` are public: the POSTs check CSRF themselves and failed lookups are throttled (429). See [Human evaluation](/docs/HUMAN_EVALUATION#api-summary) |
| Restricted evaluator accounts | Session only, and only `/_allauth/browser/v1/*`, `/api/human-eval/portal/*` and `GET`/`PATCH /api/users/me/`; everything else is 403 `evaluator_only` and their tokens are refused |

> [!WARNING]
> Token scopes (`mcp`, `read`) are enforced only by the MCP endpoint. On the REST API a token with `read` scope still has full read-write access as its user.

## Conventions

- Errors are `{"detail": "<message>"}` with a fitting status code. Missing-OpenRouter-key errors (catalog sync/refresh, custom jobs) add a `remediation` field.
- Most IDs are UUIDs. Reports are addressed by their public ID (`rpt_…`) in `/api/reports/{report_id}/`. Endpoints, webhooks and presets use integer IDs, and catalog models are addressed by slug.
- List endpoints paginate with `page` / `per_page` query params and return the total alongside the items. The exact envelope varies by router.
- The experiments API answers refusals with `400 {"detail", "code", "errors": [...]}`: `code` is machine-readable (e.g. `missing_api_key`, `digest_drift`, `not_draft`) and `errors` lists every blocking issue.
- Owner-scoped resources (generation jobs, experiments and their conditions, schedules and post-runs, analysis runs, reports, endpoints, webhooks, runtime containers and their actions) answer 404 for other users, never 403. Staff see everything, except reports: `/api/reports/` lists, opens, exports and deletes only the requesting user's own reports, staff included (the `reports.csv`/`reports.json` exports list every report to staff).
- Every report type is generated from the requesting user's own jobs, analysis runs and findings (staff: everyone's); the model catalog stays global.
- Staff-only actions answer 403, for example catalog sync/import/refresh/delete, direct endpoints, cloud provider connections and executor settings.

## Routers

All routers are mounted in `config/api.py`.

| Router | What it covers |
| --- | --- |
| `/users/` | First-run bootstrap, current user (`me/`, `me/activity/`), user list and detail (both scoped to yourself) |
| `/tokens/` | Personal API token list, create (raw token shown once), revoke |
| `/credentials/` | Encrypted per-user keys for `openrouter`, `huggingface`, `github`: status, set, delete, test |
| `/models/` | Catalog list/filter, stats, providers, comparison (up to 6), detail (with the catalog twin of local models), related, insights, the sampling your runs sent, research covariates (JSON/CSV), live provider endpoints. Staff-only sync, import, delete. JSON/CSV export. See [Models reference](/docs/MODELS_REFERENCE) |
| `/generation/` | Jobs (agent, scaffolding, custom, legacy copilot) with cancel / retry / replay / rerun / evaluate, artifacts (with per-call telemetry), run records and export; per-job code profile, timeline, quality metrics, same-setup replicates and agent trajectory; per-model behaviour, call telemetry and variability; the per-call CSV (see [Research-data endpoints](#research-data-endpoints)). Also batches, agent harnesses and config, run presets, blocks, profiles (bundles), stacks, app specs, packages and starters. See [Generation process](/docs/GENERATION_PROCESS), [Template packages](/docs/TEMPLATE_PACKAGES) |
| `/generation/experiments/` | Experiment design with a condition cross-product, stateless preview, launch / top-up, pause / resume / cancel / archive / clone, status, cell grid, results, run-to-run variability (JSON/CSV), batches, export, schedules (plus `run-now`) and post-run; `/generation/cron-preview/`. See [Experiments](/docs/EXPERIMENTS#api-summary) |
| `/inference/` | LLM endpoints (OpenRouter, Ollama, OpenAI-compatible; direct or relay): CRUD, refresh, test probes, unload, per-model settings, relay tokens (create/rotate/revoke), sessions, metrics, install commands. See [Local models](/docs/LOCAL_MODELS) |
| `/execution/` | Executor capacity and lanes, the pending-job queue with blocked reasons (filtered to what you may see), staff-only slot settings and drain. See [Execution](/docs/EXECUTION) |
| `/analysis/` | Analysis runs: create (one, or a batch), preview, list and stats, detail, cancel, re-run, bulk cancel/delete, tool raw output, findings and facets, run context (setup, source LOC, test split, rule prevalence). See [Analysis pipeline](/docs/ANALYSIS_PIPELINE#runs-api) |
| `/analyzers/` | Tool catalog and catalog metadata, tool/technology coverage, your workspace (provision / start / stop / delete) and its installed tools (install, bulk install, update, update-all, config, uninstall, test). See [Analyzer guide](/docs/ANALYZER_GUIDE) |
| `/human-eval/` | Human-evaluation tasks (owner-scoped, 404 for other users' tasks): presets, design validation, candidate jobs, environment, task CRUD / status / duplicate, items, preview, the app pool and its hosting, participants (access codes, accounts, set-password links), invites, progress, the responses browser, results, CSV / ZIP / JSON exports. Refusals are `{"detail", "code", "errors"}`. See [Human evaluation](/docs/HUMAN_EVALUATION#api-summary) |
| `/human-eval/portal/` | The participant portal (its own auth, see above): join, resume, logout, me, the task page, consent, pre/post answers, next / finish / withdraw, the workbench (responses, submit, skip, reports and screenshots, finding verdicts, source), heartbeat, app ensure, HTTP console |
| `/statistics/` | Dashboard, overview, severity, trends, models, tools, top findings, recent activity, code generation, analyzer health; study tables (`study/breakdowns`, `healing`, `tools`, `accounting`). A malformed `experiment_id` or `batch_id` returns 400. Top findings are grouped per title, tool and the tool's own label (`tool_severity`, empty = unrated) and flag `dependency_advisory`; average durations are null until something completed. See [Study tables](/docs/STUDY_TABLES) |
| `/rankings/` | Rankings, top models, status, severity-weight sensitivity, refresh, CSV export. Counts nothing measured (no measured app, or no advisory scanner / dynamic tool in the instrument set) are null in JSON and empty in the CSV, never 0; `conditions_pooled`/`pooling_note` flag rows that pool several experiment conditions. A malformed `experiment_id` returns 400 |
| `/reports/` | Report list, generate, detail, data, tables, export, delete. `GET /api/reports/{report_id}/tables/` returns the curated tables of the Markdown/LaTeX export as JSON (`tables: [{title, columns: [{key, header}], rows, note, total_rows}]`), with cells formatted as in the export (`-` = not measured) and at most 500 rows per table (`total_rows` is the full count). It answers 400 until the report is completed. `tool_analysis` answers 400 for an unknown `tool_name`. A report still `pending` 10 minutes, or `generating` 1 hour, after creation is marked `failed` ("Interrupted…") when reports are listed or opened; generate it again |
| `/runtime/` | Containers for generated apps: build for a job, smoke test, start / stop / restart / remove, logs, health, inspect, whitelisted diagnostic exec, async action status, Docker info. A container is yours if you built it or own its generation job; build and smoke take only your own jobs |
| `/export/` | File downloads and review samples, see below |
| `/docs/` | This docs viewer: tree, categories, search, page |
| `/system/` | Staff-only snapshots (host, containers, Redis, Celery, DB, app stats) and maintenance actions (clear stuck analysis/generation, purge orphan containers, clear caches) |
| `/integrations/` | Cloud provider presets (test key, connect), webhooks (event types, CRUD, test, deliveries), GitHub status and export, MCP client config. See [Integrations](/docs/INTEGRATIONS) |

There is no `/automation/` router any more: schedules and post-runs live on experiments.

## Research-data endpoints

Read-only endpoints for the research data recorded around generation runs. All use the normal auth stack (Bearer tokens work) and are scoped to your own jobs, experiments and analysis runs: another user's or a malformed id is a 404. A model id outside `LLMModel.objects.visible_to(user)` is a 404. Everything is computed on read (no backfill needed) and descriptive: n on every figure, no p-values, and unknown values null (empty in CSV), never 0.

| Endpoint | Query | Returns |
| --- | --- | --- |
| `GET /api/generation/jobs/{job_id}/code-profile/` | | Source LOC by layer and language next to the all-lines count, excluded and non-source files, file origins, structure heuristics, dependency ownership ([Generation process](/docs/GENERATION_PROCESS#job-lifecycle-all-modes)) |
| `GET /api/generation/jobs/{job_id}/timeline/` | | Phases with start, end, seconds and source; milestones; execution record; experiment cell |
| `GET /api/generation/jobs/{job_id}/quality-metrics/` | | The latest lizard, radon and jscpd results (and other tools with numeric metrics), with their instruments and source runs |
| `GET /api/generation/jobs/{job_id}/replicates/` | | The job's same-setup group (contract C3): members with relation, metrics and similarity, a summary, `determinism` probes for replays and `issue_coverage` ([Experiments](/docs/EXPERIMENTS#run-to-run-variation)) |
| `GET /api/generation/jobs/{job_id}/trajectory/` | `limit` (1–2000, default 500) | Agent jobs only: the trajectory record, one row per LLM call, the rule table and notes ([Generation process](/docs/GENERATION_PROCESS#trajectory-and-behaviour-metrics)) |
| `GET /api/generation/jobs/{job_id}/trajectory/steps/{seq}/` | | Agent jobs only: one LLM call in full (texts cut at about 20,000 characters) |
| `GET /api/generation/models/{model_id}/behaviour/` | | `metrics.behaviour` per harness over your completed agent jobs of the model (`model_id` is the numeric pk) |
| `GET /api/generation/models/{model_id}/call-telemetry/` | `mode` (`agent`, `scaffolding`, `custom`; empty: all; another value is a 400) | Per-call statistics overall and per upstream provider ([Generation process](/docs/GENERATION_PROCESS#statistics-and-exports)) |
| `GET /api/generation/models/{model_id}/variability/` | | Within-group spread over the model's same-setup groups of your completed runs |
| `GET /api/generation/calls.csv` | `experiment` (UUID), `job` (UUID), `model` (pk); combined, at least one required (else 400); `spreadsheet_safe` (default true) | One streamed row per LLM call, no message content |
| `GET /api/generation/experiments/{experiment_id}/variability/` | `exclude_invalid`, `exclude_truncated`, `common_cells`, `fingerprint` (`all` \| `majority`), `planned_only` (as `results/`); `format` (`json` \| `csv`; another value is a 400) | Run-to-run variation of the Results tab's kept cells; CSV: one row per (condition, app) cell ([Experiments](/docs/EXPERIMENTS#run-to-run-variation)) |
| `GET /api/analysis/runs/{run_id}/context/` | `scope` (`experiment` \| `mine`; empty: the experiment when the app is in one of yours; another value is a 400) | Setup line, study source LOC with excluded paths, per-tool test split, rule prevalence ([Analysis pipeline](/docs/ANALYSIS_PIPELINE#run-context)) |
| `GET /api/analysis/runs/{run_id}/findings/` | `scope` also takes `source` and `tests` | Every finding carries `in_tests`; facets add `code_parts` |
| `GET /api/models/detail/{slug}/sampling/` | | The sampling parameters your runs of the model sent, per key: sent, not sent, unknown ([Models reference](/docs/MODELS_REFERENCE#sampling-defaults)) |
| `GET /api/models/covariates/` | `used_only` (default true: models with at least one of your jobs), `format` (`json` \| `csv`; another value is a 400) | One row of research covariates per visible model; CSV `model_covariates_<used\|all>_<YYYYMMDD>.csv` ([Models reference](/docs/MODELS_REFERENCE#covariates-export)) |

`GET /api/generation/jobs/{job_id}/artifacts/` now includes each call's `telemetry` (contract C1), and `GET /api/models/detail/{slug}/` a `twin` for local endpoint models. `POST /api/models/sync/` and `POST /api/models/detail/{slug}/refresh/` fetch Hugging Face metadata with the staff caller's stored Hugging Face token.

## Export endpoints

`GET /api/export/` file endpoints: `findings.csv`, `findings.json`, `findings.sarif`, `generation-jobs.csv`, `generation-jobs.json`, `analysis-tasks.csv`, `analysis-tasks.json`, `reports.csv`, `reports.json`. Rows are scoped to your own objects (staff: all), newest first, and capped at `limit` (default 10 000, hard cap 50 000; a negative limit is a 422). Every response carries `X-Total-Count` (rows matching before the limit) and `X-Truncated` (`true` when the limit cut rows). Large CSV exports stream. CSV cells that a spreadsheet would read as a formula (starting with `=`, `+`, `-`, `@`, tab or CR, and not a number) get a leading apostrophe; pass `spreadsheet_safe=false` for pandas/R, or use the JSON export.

Findings rows hold the analysis run (`task_id`), `result_id`, `result_status`, the app they describe (`generation_job_id`, `experiment_id`, `condition_id`, `condition_label` (the arm's name as the study tables and experiment pages show it: its label, else `<model> / <method>`), `model` slug, `app`, `stack`, `repeat_index`), `analyzer`, `tool_version`, `tool_severity` (the level exactly as the tool printed it, `unrated` when it rates nothing), `normalized_severity` (heuristic 5-level bucket for statistics), `category`, location, rule, message, description, `cwe`, `package`, `installed_version`, `fixed_versions`, `docs_url` and `is_latest`. `is_latest` is true when the row's result is the one the study tables count (latest result per job and tool, `ToolResult.latest_ids_per_job`); it is empty for ad-hoc runs without a generation job. The findings exports keep every run's rows. Filters: `task_id`, `generation_job_id`, `experiment_id` (UUIDs), `analyzer`, `tool_severity` (case-insensitive, `unrated` = no label), `normalized_severity` (`severity` is a deprecated alias), `latest_only=true`, `since`. `generation-jobs.*?model_id=` takes the model slug (e.g. `deepseek/deepseek-v4.1-flash`), and the jobs export has `model_id`, `app`, `stack`, `experiment_id`, `condition_id`, `condition_label` and `repeat_index` columns (the same `condition_label`). `analysis-tasks.*` reports `reported_severity_counts` (the tools' own labels, JSON) and `unrated_findings` next to the `normalized_*` buckets; runs analyzed before those counts were recorded show them blank.

> [!WARNING]
> These file exports authenticate via **session cookie only**. Bearer tokens do not work here: the endpoints opt out of the standard auth stack and check the session directly. Download them from a logged-in browser, or with the session cookie in `curl`.

The SARIF export has one SARIF run per tool result (analysis run x tool). Its driver names the tool version the analyzer recorded (omitted when none was), and `run.properties` name the app (job, model, app, experiment cell) and `is_latest`. Each result's `level` follows the tool's own label (`none` when the tool rates nothing), with `properties.tool_severity` (raw) and `properties.normalized_severity`. DAST results (ZAP, Nuclei, Wapiti) carry a `webRequest` with the URL path instead of a file location.

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
