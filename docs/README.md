# LLM Lab documentation

Guides and reference for running, using, and extending LLM Lab — the platform that generates one application per LLM, runs each in a Docker sandbox, and measures the code with a pinned static-analysis toolchain and a replicated AI reviewer.

## What is LLM Lab

You pick a requirement template and a set of models from OpenRouter (or your own local models). For each model, the platform generates a complete application, can run it live in its own container, analyzes the code under a frozen analysis plan, and aggregates the measurements into studies, statistics, reports, and per-model rankings. Everything works from the web UI, or unattended through experiment schedules and post-runs.

## Documentation map

| Doc | What it covers |
| --- | --- |
| [Quickstart](/docs/QUICKSTART) | From clone to your first generated and analyzed app |
| [Development guide](/docs/development-guide) | just targets, tests, env vars, code layout, doc conventions |
| [Architecture](/docs/ARCHITECTURE) | How the pieces fit: apps, auth, threads vs Celery, Docker surfaces |
| [Generation process](/docs/GENERATION_PROCESS) | The generation modes, run presets, fairness rules, identity keys, and the job lifecycle |
| [Experiments](/docs/EXPERIMENTS) | Designed runs: conditions, presets, comparability, launch and export |
| [Study tables](/docs/STUDY_TABLES) | Pre-registered plans, the study report (Markdown/LaTeX), healing ablation, deduplicated findings, review sample, tool accounting |
| [Template packages](/docs/TEMPLATE_PACKAGES) | The package format, import/export rules, and starter packages |
| [Analysis pipeline](/docs/ANALYSIS_PIPELINE) | The v2 engine: samples and scope, the pinned image and per-tool containers, tool outcomes, normalization, the AI reviewer, plans and durable execution |
| [Research guide](/docs/RESEARCH_GUIDE) | Running a pre-registered analysis study: targets, design, preflight, protocol, report, exports, a worked example |
| [Analysis methodology](/docs/ANALYSIS_METHODOLOGY) | Thesis appendix: SLOC, measured scope, mapping tables, dedup, outcomes, AI grounding and consensus, statistics |
| [Background services](/docs/BACKGROUND_SERVICES) | Celery, the scheduler, and the SSE event stream |
| [Automation guide](/docs/AUTOMATION_GUIDE) | Where automation went (experiment schedules and post-runs) and the analyze step |
| [Analyzer guide](/docs/ANALYZER_GUIDE) | The 21 pinned tools and the AI reviewer, options, isolation, canary self-test, adding a tool |
| [Models reference](/docs/MODELS_REFERENCE) | Model catalog sync and API key resolution |
| [Local models](/docs/LOCAL_MODELS) | Ollama and OpenAI-compatible endpoints, relays, the LLM request queue |
| [Execution](/docs/EXECUTION) | The job executor: lanes, slots, the memory rule, leases |
| [Integrations](/docs/INTEGRATIONS) | MCP server, cloud LLM providers, GitHub export, notifications |
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
