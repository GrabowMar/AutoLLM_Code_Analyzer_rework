# LLM Lab documentation

Guides and reference for running, using and extending LLM Lab, the research platform that has LLMs and coding-agent harnesses build web applications under controlled, versioned conditions, evaluates every sample in a Docker sandbox, scores the code with 33 analysis tools and reports comparisons with intention-to-treat statistics.

## What is LLM Lab

You design a **study**: factors (model, harness preset, stack, style, auth mode, guidance, prompt set, prose variant, sampling, budget preset, ...), units (task versions, or slot targets in frozen parent apps), replicates and an evaluation level. The platform pins every library entity by version and content hash, renders and previews every prompt before launch (prompts are editable library entities, and an edit becomes a new version), pairs conditions with common random seeds, and runs each cell under hard budget caps set by a budget preset (`frugal`, `standard`, `generous`, or your own version) whose knobs cover tokens, money, time, routing, reasoning, concurrency, stop rules and the pilot. Budget is enforced outside the model and never appears in a prompt. **Agentic whole-app generation** (`app@1`) is the primary mode: a harness works in an isolated sandbox behind a per-run LLM gateway. **Slot mode** (`slot@1`) regenerates one validated part of frozen parent apps many times to measure pattern prevalence. Samples are built, tested and analysed; results report attrition, by-factor estimates and a confirmatory gate for pre-registered studies. Everything is reachable over the REST API and an MCP server.

## Documentation map

| Doc | What it covers |
| --- | --- |
| **Getting started** | |
| [Quickstart](/docs/QUICKSTART) | From clone to your first study and analysed sample |
| [User guide](/docs/USER_GUIDE) | The web UI page by page, and the workflows that connect the pages |
| [Development guide](/docs/development-guide) | just targets, devcontainer, tests, env vars, code layout, doc conventions |
| **Architecture** | |
| [Architecture](/docs/ARCHITECTURE) | How the pieces fit: apps, auth, where work runs, the Docker sandbox |
| [Studies](/docs/STUDIES) | Designs and factors (harness presets, prompts, budget presets), plans, CRN seeds and block order, launch and lock, pilots, top-ups, results, run-to-run variation, analysis plans, schedules and post-runs, export, the identity changelog |
| [Generation process](/docs/GENERATION_PROCESS) | The agentic run lifecycle: manifest, sandbox, gateway, harness, collection, outcomes, runs API |
| [Slot mode](/docs/SLOT_MODE) | Parents, symbol index, targets and holes, context and leak scan, validation, executors, assembly and evaluation |
| [Pattern analysis](/docs/PATTERNS) | Detectors, normalizers, clusters, prevalence, audits and the pattern export |
| [Budget](/docs/BUDGET) | Budget presets and their knobs (validity classes), effective policy and lint, estimates, reservations and blocks, live spend, spend caps and quotas, stop rules and pilot gates |
| [Evaluation](/docs/EVALUATION) | EvalSuite v2: lock, build, start, functional checks, security probes, adherence, starter baselines, reuse and re-evaluation |
| [Harnesses](/docs/HARNESSES) | The eight harness adapters, which are enabled and why, images, native and controlled presets, smoke runs |
| [LLM gateway](/docs/GATEWAY) | Front protocols, passthrough, budget enforcement and the per-run logs |
| [Stacks and starters](/docs/STACKS) | Stack profiles, starters, the build context and the dependency caches |
| [Human evaluation](/docs/HUMAN_EVALUATION) | Manual testing studies: task design, participants and the evaluation portal, hosting apps for testers, blinded results, exports |
| [Execution](/docs/EXECUTION) | The executor: leases, pools and lanes, fair-share ordering, blocked reasons, capacity settings, drain |
| [Sample builds](/docs/SAMPLE_BUILDS) | Shared sample images, BuildKit caches, resource caps, refresh and rollout |
| [Analysis pipeline](/docs/ANALYSIS_PIPELINE) | Analyzer workspaces, tool execution, result statuses, the AI reviewer, measurement rules |
| [Background services](/docs/BACKGROUND_SERVICES) | Executor, web-process threads, Celery, and the SSE event stream |
| **Reference** | |
| [The generation library](/docs/LIBRARY) | Versioned library entities: tasks, stacks, styles, guidance, prompts, presets, suites, detectors |
| [Analyzer guide](/docs/ANALYZER_GUIDE) | The 33 tools and the analyzer shop, tool YAML format, adding a tool |
| [Study tables](/docs/STUDY_TABLES) | Analysis plans, the study report (Markdown/LaTeX), deduplicated findings, review sample, accounting |
| [Library packages](/docs/TEMPLATE_PACKAGES) | The library package format (v4), import/export rules |
| [Local models](/docs/LOCAL_MODELS) | Endpoints for Ollama and OpenAI-compatible servers, direct connections and the relay |
| [Models reference](/docs/MODELS_REFERENCE) | Model catalog sync, enrichment, the model page, API key resolution |
| [Integrations](/docs/INTEGRATIONS) | MCP server, provider connections, GitHub export, webhooks |
| [API reference](/docs/api-reference) | Auth, conventions, routers, exports, relay, MCP, SSE |
| **Operations** | |
| [Deployment guide](/docs/deployment-guide) | The deploy script and the production Traefik stack |
| [Troubleshooting](/docs/TROUBLESHOOTING) | Symptom-first fixes for common failures |

## Where things live

```text
backend/    Django apps, one per domain (generation, execution, inference, analysis, …)
  generation/study/     the study engine (design, expansion, plans, launch, results)
  generation/testing/   factories, fakes (harness, gateway, sandbox, evaluator, study seams), golden library
config/     settings, API root, URLs, Celery app
frontend/   SvelteKit app
relay/      relay script for connecting a model server behind NAT
compose/    Dockerfiles for local and production stacks
scripts/    bootstrap.py and deploy.sh
docs/       these pages, served live by the in-app viewer
```

For tests, `backend/generation/testing/` holds the factories for every generation model, the fakes for the harness, gateway, sandbox, evaluator, slot, patterns and the study engine's seams (`fake_study.py`), and the frozen golden library (`golden_library/`: a miniature library and a parent app) used by the identity golden tests ([Studies](/docs/STUDIES#identity-changelog)).
