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
| `/analysis/` | The analysis engine: tool catalog and image, self-test, reviewer options, profiles, runs and findings, studies, targets, plans — see [below](#analysis-api) |
| `/statistics/` | Dashboard aggregates; study tables (`/study/breakdowns/`, `/study/healing/`, `/study/tools/`, `/study/accounting/`) — [Study tables](/docs/STUDY_TABLES) |
| `/rankings/` | Model scores and benchmarks, including a refresh action |
| `/reports/` | Saved report CRUD and generation |
| `/runtime/` | Containers for generated apps: build, start/stop, status |
| `/export/` | File downloads — see below |
| `/docs/`, `/system/` | This docs viewer's tree/page/search; staff-only system snapshots |

## Analysis API

Everything under `/api/analysis/` ([Analysis pipeline](/docs/ANALYSIS_PIPELINE), [Research guide](/docs/RESEARCH_GUIDE)). Runs, studies and your profiles are owner-scoped (staff read all); UUID path parameters are typed (a malformed id is 404/422, never 500); invalid configs answer `422` with dotted field paths (`{"detail", "errors": {"tools.0.config.select": "..."}}`); lists use the envelope `{items, total, page, per_page, pages}`; findings are ordered by severity rank with an id tiebreak. Long work (runs, image builds, study launches, reports) never runs inside a request.

**Tools, image, self-test, reviewer**

| Route | Purpose |
| --- | --- |
| `GET /tools/`, `GET /tools/{slug}/` | The catalog: specs, config schemas, version from the image manifest, latest canary verdict |
| `GET /image/` (`?refresh=true`) | Analyzer image tag, built, image id, manifest, build state |
| `POST /image/build/` | Build in the background (202; 200 when already built; 503 when the daemon is unreachable) |
| `POST /selftest/` (`{"tool_slugs"?}`), `GET /selftest/latest/` (`?image_tag=`, `?any_image=`) | Canary run (201) and the latest verdicts |
| `GET /reviewer/options/` | Engines, harnesses with option schemas, default model, prompt versions, limits, key state |
| `GET /reviewer/models/?q=&limit=` | Models you can configure (catalog plus your visible local models), with capabilities (`limit` ≤ 50) |
| `GET /reviewer/model-capabilities/{model}` | `in_catalog`, `resolvable`, `local`, context window, structured outputs, tools, seed, reasoning efforts, pricing; an unknown model is 200 with `in_catalog: false` |

**Profiles**

| Route | Purpose |
| --- | --- |
| `GET /profiles/` (`?include_archived=`, `?q=`, `?mine=true`) | Latest version per slug: system profiles first, then yours |
| `GET /profiles/defaults/` | The default config and the catalog, for the editor |
| `POST /profiles/validate/` | Validate a config (422 with field paths) |
| `POST /profiles/`, `GET /profiles/{id}/` | Create / read |
| `PUT /profiles/{id}/` | Changed content creates version + 1; identical content only renames |
| `DELETE /profiles/{id}/`, `POST /profiles/{id}/unarchive/` | Archive / unarchive every version of the slug |
| `POST /profiles/{id}/clone/` (`{"name"?, "slug"?}`) | A new profile of yours (version 1) |

**Runs**

| Route | Purpose |
| --- | --- |
| `GET /runs/` | Filters `status`, `purpose` (default: all but canary), `study_id`, `generation_job_id`, `profile_id`, `plan_hash`, `is_complete`, `q`; each row carries `summary.problems` |
| `POST /runs/` | Ad-hoc run: `generation_job_id` or `source_code` (`{path: content}`), and `profile_id` or an inline `config` (201; 400 for an unresolvable target) |
| `POST /runs/preview/` | The preflight for one target (no study-design notes) |
| `GET /runs/{id}/` | Detail: plan, target_meta, manifest totals, tool results with diagnostics, review samples, measurement, summary |
| `POST /runs/{id}/cancel/` | Cooperative, final cancel |
| `POST /runs/{id}/retry/` (`{"tools"?}`) | Requeue failed / timed-out / cancelled / skipped tools; AI reviews resume from stored replicates; `409 procedure_drift` for a study run whose procedure changed |
| `DELETE /runs/{id}/` | Delete a finished ad-hoc or self-test run with its findings and artifacts (409 while active or for a study run) |
| `GET /runs/{id}/findings/` | Filters `severity`, `tool`, `category`, `origin`, `file` (exact or a `dir/` prefix), `rule`, `grounding`, `is_ai`, `counted`, `consensus`, `in_scope`, `issue_key`, `sample`, `result_id`, `q`; `ordering`; facets (each ignores its own dimension's filter) |
| `GET /runs/{id}/issues/` (`?kind=static\|ai`) | Deduplicated issues, or AI consensus clusters, with the detecting tools |
| `GET /runs/{id}/files/`, `GET /runs/{id}/files/content/?path=` | The sample manifest; a file's content (re-resolved, owner only) |
| `GET /runs/{id}/artifacts/{result_id}/{name}` | A raw tool output or review transcript, sha-verified |
| `GET /runs/{id}/export.json`, `.csv`, `.sarif` | Per-run exports |

**Studies, targets, plans**

| Route | Purpose |
| --- | --- |
| `GET /studies/` (`?status=`, `?q=`), `POST /studies/` | List; create a draft (`name`, `description`, `research_questions`, `target_spec`, `profile_id`, `exclusion_rules`, `analysis_design`). The create response has `defaults_applied` when defaults came from an experiment's frozen plan |
| `GET`, `PATCH` (draft only), `DELETE /studies/{id}/` | Detail (preflight, plan, `protocol_verified`, `run_counts`, `complete_runs`, `actions`) / update / delete |
| `POST /studies/{id}/preflight/`, `/lock/`, `/unlock/`, `/launch/`, `/cancel/`, `/retry-failed/` | Lifecycle; refusals are `409` with a `code` (e.g. `procedure_drift` with `tools`, `engine`, `mappings`) |
| `GET /studies/{id}/progress/` | Targets × tools matrix, counts, cost (`complete`, `runs_with_unknown_cost`), stale and capacity hints |
| `POST /studies/{id}/report/` (202), `GET /studies/{id}/report/`, `GET /studies/{id}/report.md` | Post-run report (generating / failed / ready) |
| `GET /studies/{id}/protocol/`, `GET /studies/{id}/protocol.md` | The frozen protocol and its sha256 |
| `GET /studies/{id}/export/{tables.zip\|replication.zip\|findings.csv\|runs.csv}` (`?escape=true`) | Exports after launch |
| `POST /targets/resolve/`, `GET /targets/options/` | Candidate jobs with eligibility (paginated; `eligible_total`, `excluded_by_reason`); what you can target |
| `GET /plans/?experiment_id=&batch_id=&study_id=&purpose=` | Frozen plans of your runs with run and job counts, profiles, a plan brief and the experiment post-run that used it |

Run progress streams on the SSE channel `analysis:<run_id>` (see [Realtime stream](#realtime-stream)). Related endpoints outside this router take a `plan_hash` too: `/api/statistics/study/breakdowns/`, `/api/export/review-sample.zip` and the `study_report` / `experiment_report` configs.

## Export endpoints

`GET /api/export/` endpoints return files: `findings.csv`, `findings.json`, `findings.sarif`, `generation-jobs.csv`, `generation-jobs.json`, `analysis-tasks.csv`, `analysis-tasks.json` (analysis runs), `reports.csv`, `reports.json`, and the manual review sample `review-sample.zip` (scored with `POST /review-sample/score/`; see [Study tables](/docs/STUDY_TABLES#manual-review-sample)). Findings rows carry their research context (job, model, experiment, condition, run status and plan, tool version, file origin, `counted`, `is_ai`, fingerprint, `issue_key`, CWE); new columns are only ever appended. The SARIF carries real tool versions and `invocations[].executionSuccessful`. Rows are filterable by query params and capped at `limit` (default 10 000, hard cap 50 000); large CSV exports stream.

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
