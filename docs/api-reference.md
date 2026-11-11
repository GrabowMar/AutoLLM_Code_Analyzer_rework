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

- Errors are `{"detail": "<message>"}` with a fitting status code. Missing-OpenRouter-key errors (catalog sync/refresh, runs on OpenRouter models) add a `remediation` field.
- Most IDs are UUIDs. Reports are addressed by their public ID (`rpt_…`) in `/api/reports/{report_id}/`. Endpoints, webhooks and presets use integer IDs, and catalog models are addressed by slug.
- List endpoints paginate with `page` / `per_page` query params and return the total alongside the items. The exact envelope varies by router.
- The studies API answers refusals with `{"detail", "code", ...}`: `code` is machine-readable. 400: `invalid_design` (with `path` and, when a plan was attempted, `issues`), `invalid_analysis_plan`, `plan_has_errors` (`data.issues`), `confirmation_required` (`data.{p90_total, expected_total, threshold}`), `prompt_set_ambiguous` (`data.candidates`), `reason_required`, `invalid_cells`, `invalid_cap`, `invalid_replicates`, `invalid_results_query`, `invalid_mode` (clone), `invalid_owner_kind` (prompt overrides), `post_run_active`, `unknown_resource_policy`, `invalid_format` (export), and the generic `invalid_request` fallback. 409: `plan_stale`, `study_locked`, `not_launchable`, `not_piloting`, `pilot_active`, `pilot_gates_failed` (`data.gates`), `not_running`, `not_paused`, `design_drifted`, and the generic `conflict` fallback (which keeps the error's own status code when it has one). A top-up lists `cell_has_usable_run` or `not_in_plan` per refused cell. Plan issues are typed (`{code, severity, message, cells, path, data, fix}`) and their codes come from the registry served by `/api/generation/config/`.
- Owner-scoped resources (generation runs, studies and their plans, conditions, cells, schedules and post-runs, analysis runs, reports, endpoints, webhooks, runtime containers and their actions) answer 404 for other users, never 403. Staff see everything, except reports: `/api/reports/` lists, opens, exports and deletes only the requesting user's own reports, staff included (the `reports.csv`/`reports.json` exports list every report to staff).
- Every report type is generated from the requesting user's own runs, analysis runs and findings (staff: everyone's); the model catalog stays global.
- Staff-only actions answer 403, for example catalog sync/import/refresh/delete, direct endpoints, cloud provider connections and executor settings.

## Routers

All routers are mounted in `config/api.py`.

| Router | What it covers |
| --- | --- |
| `/users/` | First-run bootstrap, current user (`me/`, `me/activity/`), user list and detail (both scoped to yourself) |
| `/tokens/` | Personal API token list, create (raw token shown once), revoke |
| `/credentials/` | Encrypted per-user keys for `openrouter`, `huggingface`, `github`: status, set, delete, test |
| `/models/` | Catalog list/filter, stats, providers, comparison (up to 6), detail (with the catalog twin of local models), related, insights, the sampling your runs sent, research covariates (JSON/CSV), live provider endpoints. Staff-only sync, import, delete. JSON/CSV export. See [Models reference](/docs/MODELS_REFERENCE) |
| `/generation/` | v3 sample generation, by area (spec section 9): `library/` (versioned entities), `prompts/` (render, diff), `harnesses/` and `config/`, `budget/`, `studies/` (below), `runs/` (runs, attempts, files, trajectory, retry, evaluate), `slots/`, `patterns/`, `eval/`; plus the research-data endpoints below. See [Generation process](/docs/GENERATION_PROCESS), [Library](/docs/LIBRARY) |
| `/generation/studies/` | Study design (CRUD), ephemeral `resolve/` and `freeze-plan/`, plan cells and cell details (rendered stimuli with source spans, effective policy and params, identity), cell diffs, `launch/`, `expand/` (pilot to main), pause / resume / cancel, per-condition pause, `top-up/`, `add-replicates/`, `clone/`, cells, `results/`, `attrition/`, `export/` (zip), prompt overrides, schedules (plus `run-now`), post-runs; `studies/adhoc/` and `studies/cron-preview/`. See [Studies](/docs/STUDIES) |
| `/inference/` | LLM endpoints (OpenRouter, Ollama, OpenAI-compatible; direct or relay): CRUD, refresh, test probes, unload, per-model settings, relay tokens (create/rotate/revoke), sessions, metrics, install commands. See [Local models](/docs/LOCAL_MODELS) |
| `/execution/` | Executor capacity and lanes, the pending-run queue with blocked reasons (filtered to what you may see), staff-only slot settings and drain. See [Execution](/docs/EXECUTION) |
| `/analysis/` | Analysis runs: create (one, or a batch), preview, list and stats, detail, cancel, re-run, bulk cancel/delete, tool raw output, findings and facets, run context (setup, source LOC, test split, rule prevalence). See [Analysis pipeline](/docs/ANALYSIS_PIPELINE#runs-api) |
| `/analyzers/` | Tool catalog and catalog metadata, tool/technology coverage, your workspace (provision / start / stop / delete) and its installed tools (install, bulk install, update, update-all, config, uninstall, test). See [Analyzer guide](/docs/ANALYZER_GUIDE) |
| `/human-eval/` | Human-evaluation tasks (owner-scoped, 404 for other users' tasks): presets, design validation, candidate runs, environment, task CRUD / status / duplicate, items, preview, the app pool and its hosting, participants (access codes, accounts, set-password links), invites, progress, the responses browser, results, CSV / ZIP / JSON exports. Refusals are `{"detail", "code", "errors"}`. See [Human evaluation](/docs/HUMAN_EVALUATION#api-summary) |
| `/human-eval/portal/` | The participant portal (its own auth, see above): join, resume, logout, me, the task page, consent, pre/post answers, next / finish / withdraw, the workbench (responses, submit, skip, reports and screenshots, finding verdicts, source), heartbeat, app ensure, HTTP console |
| `/statistics/` | Dashboard, overview, severity, trends, models, tools, top findings, recent activity, code generation, analyzer health; study tables (`study/breakdowns`, `tools`, `accounting`). A malformed `study_id` returns 400. Top findings are grouped per title, tool and the tool's own label (`tool_severity`, empty = unrated) and flag `dependency_advisory`; average durations are null until something completed. See [Study tables](/docs/STUDY_TABLES) |
| `/rankings/` | Rankings (MSS v2 fields, `eligible_only`, `min_mss`), top eligible models, input sources (`/sources/`), severity-weight sensitivity, CSV export, and the study selection: live preview (`/selection/`, writes nothing), snapshots (`/selection/snapshots/`, `/selection/latest/`; create and `/confirm/` are staff only). Counts nothing measured (no measured app, or no advisory scanner / dynamic tool in the instrument set) are null in JSON and empty in the CSV, never 0; `conditions_pooled`/`pooling_note` flag rows that pool several study conditions. Filters `study_id` and `config_fingerprint`; a malformed `study_id` returns 400 |
| `/reports/` | Report list, generate, detail, data, tables, export, delete. `GET /api/reports/{report_id}/tables/` returns the curated tables of the Markdown/LaTeX export as JSON (`tables: [{title, columns: [{key, header}], rows, note, total_rows}]`), with cells formatted as in the export (`-` = not measured) and at most 500 rows per table (`total_rows` is the full count). It answers 400 until the report is completed. `tool_analysis` answers 400 for an unknown `tool_name`. A report still `pending` 10 minutes, or `generating` 1 hour, after creation is marked `failed` ("Interrupted…") when reports are listed or opened; generate it again |
| `/runtime/` | Containers for generated apps: build for a run (`jobs/{run_id}/build/`), the preview smoke probe (`jobs/{run_id}/smoke/`: a diagnostic stored on the container, never a metric), start / stop / restart / remove, logs, health, inspect, whitelisted diagnostic exec, async action status, Docker info. A container is yours if you built it or own its generation run; build and smoke take only your own runs |
| `/export/` | File downloads and review samples, see below |
| `/docs/` | This docs viewer: tree, categories, search, page |
| `/system/` | Staff-only snapshots (host, containers, Redis, Celery, DB, app stats) and maintenance actions (clear stuck analysis/generation, purge orphan containers, clear caches) |
| `/integrations/` | Cloud provider presets (test key, connect), webhooks (event types, CRUD, test, deliveries), GitHub status and export, MCP client config. See [Integrations](/docs/INTEGRATIONS) |

There is no `/automation/` router any more: schedules and post-runs live on studies.

### Studies operations

Every study route is under `/api/generation/` with an explicit ninja operation id (the MCP server's study tools `list_studies`, `get_study`, `resolve_study` and `launch_study` call the first four rows):

| Operation id | Route | What |
| --- | --- | --- |
| `generation_studies_list` | `GET /studies/` | Your studies (status, mode, kind, progress, spend against the cap, exclusion rate, lock) |
| `generation_studies_get` | `GET /studies/{id}/` | The study with its active plan summary and factors |
| `generation_studies_resolve` | `POST /studies/{id}/resolve/` | Ephemeral plan: counts, estimate, issues, conditions, units, a cell sample (nothing written) |
| `generation_studies_launch` | `POST /studies/{id}/launch/` | `{plan_hash, confirm_usd?, study_cap?, pilot_override?}`; returns the runs, the stage and `resolved_cap` |
| `generation_studies_create` / `_update` | `POST /studies/`, `PATCH /studies/{id}/` | Draft design, analysis plan, budget preset (refused once locked) |
| `generation_studies_adhoc` | `POST /studies/adhoc/` | Create, resolve and launch in one call: `{study_id, plan_hash, run_ids, status, issues, estimate}` |
| `generation_studies_freeze` | `POST /studies/{id}/freeze-plan/` | Persist the plan (status planned) |
| `generation_studies_plan_cells` / `_plan_cell` / `_plan_diff` | `GET /studies/{id}/plans/{plan_hash}/cells/`, `.../cells/{cell_key}/`, `POST .../diff/` | Plan cells (filter by condition and unit, paged), one cell with its rendered stimuli, source spans, effective policy and manifest, and the diff of two cells |
| `generation_studies_expand` | `POST /studies/{id}/expand/` | Pilot to main (`override_gates` needs a `reason`) |
| `generation_studies_pause` / `_resume` / `_cancel`, `generation_studies_condition_pause` / `_condition_resume` | `POST /studies/{id}/pause/` ... | Study and per-condition controls |
| `generation_studies_top_up`, `generation_studies_add_replicates` | `POST /studies/{id}/top-up/`, `POST /studies/{id}/add-replicates/` | New attempts for cells without a usable run; extra beyond-plan replicates (exploratory only) |
| `generation_studies_clone` | `POST /studies/{id}/clone/` | `{mode: exact | latest_versions, name}` with `version_diffs` |
| `generation_studies_cells`, `generation_studies_results`, `generation_studies_attrition` | `GET /studies/{id}/cells/`, `results/`, `attrition/` | Cell states and current runs; results (`metric`, `fingerprint`, `sensitivity`, `baseline`, `common_cells`); attrition per condition |
| `generation_studies_export` | `GET /studies/{id}/export/` | The study zip |
| `generation_studies_prompt_overrides` / `_prompt_override_create` | `GET`/`POST /studies/{id}/prompt-overrides/` | Inline prompt edits saved as forked library versions (`prompt_set_ref` optional) |
| `generation_studies_schedules`, `_schedule_create` / `_update` / `_delete` / `_run_now`; `generation_studies_post_runs`, `_post_run_create`; `generation_studies_cron_preview` | `/studies/{id}/schedules/`, `/studies/{id}/post-runs/`, `GET /studies/cron-preview/` | Schedules (`top_up`, `add_replicates`, `clone_and_run`), post-runs (`analysis`, `patterns`) and the next fire times of a cron expression |

## Research-data endpoints

Read-only endpoints for the research data recorded around generation runs. All use the normal auth stack (Bearer tokens work) and are scoped to your own runs, studies and analysis runs: another user's or a malformed id is a 404. A model id outside `LLMModel.objects.visible_to(user)` is a 404. Everything is computed on read (no backfill needed) and descriptive: n on every figure, no p-values, and unknown values null (empty in CSV), never 0.

| Endpoint | Query | Returns |
| --- | --- | --- |
| `GET /api/generation/runs/{run_id}/code-profile/` | | Source LOC by layer and language next to the all-lines count, excluded and non-source files, file origins, structure heuristics, dependency ownership ([Generation process](/docs/GENERATION_PROCESS#the-collected-sample-agenticcollectorpy)) |
| `GET /api/generation/runs/{run_id}/timeline/` | | Phases with start, end, seconds and source; milestones; execution record; study cell |
| `GET /api/generation/runs/{run_id}/quality-metrics/` | | The latest lizard, radon and jscpd results (and other tools with numeric metrics), with their instruments and source runs |
| `GET /api/generation/runs/{run_id}/replicates/` | | The run's same-setup group (`config_fingerprint`): members with relation (`self`, `retry`, `replay`, `rerun`, `replicate`), metrics and similarity, a summary, `determinism` probes for replays and `issue_coverage` ([Studies](/docs/STUDIES#run-to-run-variation)) |
| `GET /api/generation/runs/{run_id}/trajectory/` | `attempt` (default: the last) | The parsed harness trajectory and the observed inputs of one attempt ([Generation process](/docs/GENERATION_PROCESS)) |
| `GET /api/generation/runs/{run_id}/calls/` | `attempt` (default: the current) | The attempt's LLM calls from the gateway's per-call records: request class, tokens, cache hits, finish reason, served provider, duration |
| `GET /api/generation/models/{model_id}/behaviour/` | | Descriptive trajectory aggregates per harness over your finished agentic runs of the model (unusable runs left out and counted; `model_id` is the numeric pk) |
| `GET /api/generation/models/{model_id}/call-telemetry/` | `kind` (`app_agentic`, `slot_single_shot`, `slot_agent`; empty: all; another value is a 400) | Per-call statistics overall and per upstream provider ([Generation process](/docs/GENERATION_PROCESS#per-call-telemetry)) |
| `GET /api/generation/models/{model_id}/variability/` | | Within-group spread over the model's same-setup groups of your completed runs |
| `GET /api/generation/calls.csv` | `study` (UUID), `run` (UUID), `model` (pk); combined, at least one required (else 400); `spreadsheet_safe` (default true) | One streamed row per LLM call, no message content |
| `GET /api/generation/studies/{study_id}/variability/` | `fingerprint` (`all` \| `majority`), `sensitivity` (empty \| `exclude_truncated`); `format` (`json` \| `csv`; another value is a 400) | Run-to-run variation over the main-stage current runs (intention-to-treat); CSV: one row per (condition, task) cell ([Studies](/docs/STUDIES#run-to-run-variation)) |
| `GET /api/analysis/runs/{run_id}/context/` | `scope` (`study` \| `mine`; another value is a 400) | Setup line, study source LOC with excluded paths, per-tool test split, rule prevalence ([Analysis pipeline](/docs/ANALYSIS_PIPELINE#run-context)) |
| `GET /api/analysis/runs/{run_id}/findings/` | `scope` also takes `source` and `tests` | Every finding carries `in_tests`; facets add `code_parts` |
| `GET /api/models/detail/{slug}/sampling/` | | The sampling parameters your runs of the model sent, per key: sent, not sent, unknown ([Models reference](/docs/MODELS_REFERENCE#sampling-defaults)) |
| `GET /api/models/covariates/` | `used_only` (default true: models with at least one of your runs), `format` (`json` \| `csv`; another value is a 400) | One row of research covariates per visible model; CSV `model_covariates_<used\|all>_<YYYYMMDD>.csv` ([Models reference](/docs/MODELS_REFERENCE#covariates-export)) |

`GET /api/models/detail/{slug}/` includes a `twin` for local endpoint models. `POST /api/models/sync/` and `POST /api/models/detail/{slug}/refresh/` fetch Hugging Face metadata with the staff caller's stored Hugging Face token.

## Export endpoints

`GET /api/export/` file endpoints: `findings.csv`, `findings.json`, `findings.sarif`, `generation-runs.csv`, `generation-runs.json`, `runs/{run_id}/bundle.zip`, `analysis-tasks.csv`, `analysis-tasks.json`, `reports.csv`, `reports.json`. Rows are scoped to your own objects (staff: all), newest first, and capped at `limit` (default 10 000, hard cap 50 000; a negative limit is a 422). Every response carries `X-Total-Count` (rows matching before the limit) and `X-Truncated` (`true` when the limit cut rows). Large CSV exports stream. CSV cells that a spreadsheet would read as a formula (starting with `=`, `+`, `-`, `@`, tab or CR, and not a number) get a leading apostrophe; pass `spreadsheet_safe=false` for pandas/R, or use the JSON export.

Findings rows hold the analysis run (`task_id`), `result_id`, `result_status`, the app they describe (`generation_run_id`, `study_id`, `condition_id`, `condition_label` (the condition's label as the study tables and study pages show it, else the model id), `model` slug, `app`, `stack`, `repeat_index`), `analyzer`, `tool_version`, `tool_severity` (the level exactly as the tool printed it, `unrated` when it rates nothing), `normalized_severity` (heuristic 5-level bucket for statistics), `category`, location, rule, message, description, `cwe`, `package`, `installed_version`, `fixed_versions`, `docs_url` and `is_latest`. `is_latest` is true when the row's result is the one the study tables count (latest result per run and tool, `ToolResult.latest_ids_per_job`); it is empty for analysis runs without a generation run. The findings exports keep every run's rows. Filters: `task_id`, `generation_run_id`, `study_id` (UUIDs), `analyzer`, `tool_severity` (case-insensitive, `unrated` = no label), `normalized_severity` (`severity` is a deprecated alias), `latest_only=true`, `since`. `generation-runs.*?model_id=` takes the model slug (e.g. `deepseek/deepseek-v4.1-flash`); the runs export filters `status, model_id, study_id, kind, outcome`. `analysis-tasks.*` reports `reported_severity_counts` (the tools' own labels, JSON) and `unrated_findings` next to the `normalized_*` buckets; runs analyzed before those counts were recorded show them blank.

> [!WARNING]
> These file exports authenticate via **session cookie only**. Bearer tokens do not work here: the endpoints opt out of the standard auth stack and check the session directly. Download them from a logged-in browser, or with the session cookie in `curl`.

The SARIF export has one SARIF run per tool result (analysis run x tool). Its driver names the tool version the analyzer recorded (omitted when none was), and `run.properties` name the sample (run, model, task, study cell) and `is_latest`. Each result's `level` follows the tool's own label (`none` when the tool rates nothing), with `properties.tool_severity` (raw) and `properties.normalized_severity`. DAST results (ZAP, Nuclei, Wapiti) carry a `webRequest` with the URL path instead of a file location.

Two more export endpoints use the normal auth stack, so Bearer tokens work:

- `GET /api/export/review-sample.zip?study_id=&size=30&stratify=model&seed=0&blind=true` draws a seeded, stratified sample of apps with their code, task requirements and blank label sheets for manual review.
- `POST /api/export/review-sample/score/` (multipart `file`) scores the filled-in ZIP against the automated verdicts. It reports precision with Wilson intervals, Cohen's kappa for the functional verdict and Spearman rho for requirement compliance.

## Relay protocol

`/api/relay/v1/` (outside the ninja API) is the HTTPS protocol a relay on a GPU box speaks: `hello`, `inventory`, a long-poll `poll` that claims queued LLM requests, `requests/<id>/result` and `bye`. The same prefix serves the relay's install files: `relay.py`, `Dockerfile` and `llmlab-relay.service`. Protocol calls authenticate with an endpoint-scoped relay token (`llr_…`), not a user token. Install steps are in [Local models](/docs/LOCAL_MODELS).

## MCP endpoint

`/api/mcp` (outside the ninja API) is a Model Context Protocol server that replays tool calls in-process against this API as the token's user. See [Integrations](/docs/INTEGRATIONS#1-mcp-server).

## Realtime stream

`GET /api/realtime/stream?channels=study:<id>,capacity` is a server-sent-events endpoint outside the ninja API (it streams). It is an async view: it checks every channel, closes its database connection, and then holds no worker thread or database connection while the stream is open. A `: heartbeat` comment goes out every 15 s. `channels` may be comma-separated or repeated.

- `401` unauthenticated, `400` without channels or with more than 20, `403` when any channel is malformed, unknown or not yours.
- Channels and who may subscribe:

| Channel | Allowed for | Events |
|---|---|---|
| `run:<uuid>` | the run's creator, the creator of its study, or staff | `status`, `step`, `usage`, `trajectory_step`, `log`, `evaluation` |
| `study:<uuid>` | the study's creator or staff | `run` (every transition), `tick` (rollups, at most every 2 s), `state`, `post_run`, `spend`, `budget_alert`, `stopped`, `paused` |
| `endpoint:<id>` | anyone who may use the endpoint; non-owners are moved to `endpoint:<id>:public` (counts only, no IPs, hostnames or errors) | `endpoint`, `session` |
| `analysis:<uuid>` | the analysis run's creator or staff | `status` (run), `progress` (phase), `result` (a tool started / finished) |
| `runtime:<uuid>` | the container's creator, the owner of its generation run, or staff | container status |
| `capacity` | any signed-in user (aggregates only) | `capacity` |

The publishing side is described in [Background services](/docs/BACKGROUND_SERVICES).
