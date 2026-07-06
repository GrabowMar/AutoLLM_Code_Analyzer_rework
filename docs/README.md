# LLM Lab documentation

Guides and reference for running, using, and extending LLM Lab — the platform that generates one application per LLM, runs each in a Docker sandbox, and scores the code with 14 analysis tools.

## What is LLM Lab

You pick a requirement template and a set of models from OpenRouter. For each model, the platform generates a complete application, can run it live in its own container, analyzes the code with the tools you select, and aggregates findings into statistics, reports, and per-model rankings. Everything works from the web UI or unattended through automation pipelines.

## Documentation map

| Doc | What it covers |
| --- | --- |
| [Quickstart](/docs/QUICKSTART) | From clone to your first generated and analyzed app |
| [Development guide](/docs/development-guide) | just targets, tests, env vars, code layout, doc conventions |
| [Architecture](/docs/ARCHITECTURE) | How the pieces fit: apps, auth, threads vs Celery, Docker surfaces |
| [Generation process](/docs/GENERATION_PROCESS) | The three generation modes, templates, and the job lifecycle |
| [Analysis pipeline](/docs/ANALYSIS_PIPELINE) | Analyzer workspaces, tool execution, findings |
| [Background services](/docs/BACKGROUND_SERVICES) | Celery, the scheduler, and the SSE event stream |
| [Automation guide](/docs/AUTOMATION_GUIDE) | Pipelines, the DSL, batches, and schedules |
| [Analyzer guide](/docs/ANALYZER_GUIDE) | The 14 tools, tool YAML format, adding a tool |
| [Models reference](/docs/MODELS_REFERENCE) | Model catalog sync and API key resolution |
| [API reference](/docs/api-reference) | Auth, conventions, routers, exports, SSE |
| [Deployment guide](/docs/deployment-guide) | The deploy script and the production Traefik stack |
| [Troubleshooting](/docs/TROUBLESHOOTING) | Symptom-first fixes for common failures |

## Where things live

```text
backend/    Django apps, one per domain (generation, analysis, automation, …)
config/     settings, API root, URLs, Celery app
frontend/   SvelteKit app
compose/    Dockerfiles for local and production stacks
scripts/    bootstrap.py and deploy.sh
docs/       these pages — served live by the in-app viewer
```
