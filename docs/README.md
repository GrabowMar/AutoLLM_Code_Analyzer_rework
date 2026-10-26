# LLM Lab documentation

Guides and reference for running, using and extending LLM Lab, the platform that has each LLM build the same application, grades and runs every app in a Docker sandbox, and scores the code with 33 analysis tools.

## What is LLM Lab

You pick an app spec and a set of models, from OpenRouter or from your own Ollama / OpenAI-compatible server. For each model the platform builds a complete application (in the primary agent mode, a coding-agent harness works in an isolated sandbox), grades it with a hidden conformance suite, can run it live in its own container, analyzes the code with the tools you install, and aggregates the results into statistics, rankings and study reports. Designed comparisons run as experiments, executed by a dedicated executor process; everything is also reachable over the REST API and an MCP server.

## Documentation map

| Doc | What it covers |
| --- | --- |
| **Getting started** | |
| [Quickstart](/docs/QUICKSTART) | From clone to your first generated and analyzed app |
| [User guide](/docs/USER_GUIDE) | The web UI page by page, and the workflows that connect the pages |
| [Development guide](/docs/development-guide) | just targets, devcontainer, tests, env vars, code layout, doc conventions |
| **Architecture** | |
| [Architecture](/docs/ARCHITECTURE) | How the pieces fit: apps, auth, where work runs, the Docker sandbox |
| [Generation process](/docs/GENERATION_PROCESS) | Agent and template modes, harnesses, run presets, grading, healing, identity keys, the job lifecycle |
| [Experiments](/docs/EXPERIMENTS) | Designed runs: conditions, comparability, launch, monitor, results, schedules, post-run, export |
| [Execution](/docs/EXECUTION) | The executor: leases, pools and lanes, fair-share ordering, blocked reasons, capacity settings, drain |
| [Analysis pipeline](/docs/ANALYSIS_PIPELINE) | Analyzer workspaces, tool execution, result statuses, the AI reviewer, measurement rules |
| [Background services](/docs/BACKGROUND_SERVICES) | Executor, web-process threads, Celery, and the SSE event stream |
| **Reference** | |
| [Analyzer guide](/docs/ANALYZER_GUIDE) | The 33 tools and the analyzer shop, tool YAML format, adding a tool |
| [Study tables](/docs/STUDY_TABLES) | Analysis plans, study report (Markdown/LaTeX), healing ablation, deduplicated findings, review sample, accounting |
| [Template packages](/docs/TEMPLATE_PACKAGES) | The package format, import/export rules, starter packages |
| [Local models](/docs/LOCAL_MODELS) | Endpoints for Ollama and OpenAI-compatible servers, direct connections and the relay |
| [Models reference](/docs/MODELS_REFERENCE) | Model catalog sync, enrichment, the model page, API key resolution |
| [Integrations](/docs/INTEGRATIONS) | MCP server, provider connections, GitHub export, webhooks |
| [API reference](/docs/api-reference) | Auth, conventions, routers, exports, relay, MCP, SSE |
| [Automation guide](/docs/AUTOMATION_GUIDE) | Where the retired automation hub went: experiment schedules and post-runs |
| **Operations** | |
| [Deployment guide](/docs/deployment-guide) | The deploy script and the production Traefik stack |
| [Troubleshooting](/docs/TROUBLESHOOTING) | Symptom-first fixes for common failures |

## Where things live

```text
backend/    Django apps, one per domain (generation, execution, inference, analysis, …)
config/     settings, API root, URLs, Celery app
frontend/   SvelteKit app
relay/      relay script for connecting a model server behind NAT
compose/    Dockerfiles for local and production stacks
scripts/    bootstrap.py and deploy.sh
docs/       these pages, served live by the in-app viewer
```
