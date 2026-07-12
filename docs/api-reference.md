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

## Routers

| Router | What it covers |
| --- | --- |
| `/users/`, `/tokens/`, `/credentials/` | Profile, API token CRUD, encrypted provider keys (OpenRouter) |
| `/models/` | Model catalog list/filter and OpenRouter sync — [Models reference](/docs/MODELS_REFERENCE) |
| `/generation/` | Jobs, batches, app specs, profiles, stacks, packages — [Generation process](/docs/GENERATION_PROCESS) |
| `/analysis/`, `/analyzers/` | Runs and findings; the tool catalog, workspace, and tool install/test — [Analyzer guide](/docs/ANALYZER_GUIDE) |
| `/statistics/` | Dashboard aggregates |
| `/rankings/` | Model scores and benchmarks, including a refresh action |
| `/reports/` | Saved report CRUD and generation |
| `/runtime/` | Containers for generated apps: build, start/stop, status |
| `/export/` | File downloads — see below |
| `/automation/` | Pipelines, runs, batches, schedules — [Automation guide](/docs/AUTOMATION_GUIDE) |
| `/docs/`, `/system/` | This docs viewer's tree/page/search; staff-only system snapshots |

## Export endpoints

`GET /api/export/` endpoints return files: `findings.csv`, `findings.json`, `findings.sarif`, `generation-jobs.csv`, `generation-jobs.json`, `analysis-tasks.csv`, `analysis-tasks.json`, `reports.csv`, `reports.json`. Rows are filterable by query params and capped at `limit` (default 10 000, hard cap 50 000); large CSV exports stream.

> [!WARNING]
> Export endpoints authenticate via **session cookie only** — Bearer tokens do not work here. They opt out of the standard auth stack and check the session directly, so download them from a logged-in browser (or with the session cookie in `curl`).

The SARIF export makes findings importable into GitHub code scanning and most security tooling.

## Realtime stream

`GET /api/realtime/stream?channels=generation:<id>,analysis:<id>` is a server-sent-events endpoint living outside the ninja API (it streams). Session-authenticated; requires at least one channel. Channel names and the publishing side are described in [Background services](/docs/BACKGROUND_SERVICES).
