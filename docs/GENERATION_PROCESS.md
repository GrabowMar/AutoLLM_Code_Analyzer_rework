# Generation process

What happens between clicking Generate and having a runnable app: the three modes, template assembly, and the job lifecycle.

## Three modes

`GenerationJob.mode` (`backend/generation/models.py`) selects the strategy:

| Mode | What it does |
| --- | --- |
| `custom` | Free-form: your prompt goes to the model, the response is parsed into code files |
| `scaffolding` | Template-driven: a generation profile plus an app spec produce structured backend/frontend prompts; output slots into a known stack skeleton |
| `copilot` | Agentic: [aider](https://aider.chat) iterates on a real git workspace (`aider_runner.py`, `copilot_workspace.py`), producing genuine multi-file projects |

Scaffolding is the mode used for comparable model benchmarks — every model gets the same skeleton and requirements, so differences in the output are differences between models.

## The three concepts

The [Generation Library](/docs/QUICKSTART) is built from three user-facing concepts, all authorable in the UI:

- **App specs** (`AppRequirementTemplate`) — what to build, as structured requirement JSON (`data/requirements/`, validated against `data/requirements/schema.json`). One mutable row per slug; `version`/`content_hash` bump when the spec content changes. Each spec carries a `difficulty` tier (`basic`/`standard`/`advanced`) inferred from endpoint/field counts — a coarse research-metadata signal, not a hard guarantee.
- **Generation profiles** (`GenerationProfile`) — how to prompt and how the model should sample: a versioned, ordered set of content blocks, a stack slug, and `llm_config` defaults. `POST` creates v1, `PUT` creates version+1, `DELETE` archives — versions are immutable, so a job snapshot keeps resolving to exactly what it captured. **Content blocks** (`ContentBlock`) are the composable fragments underneath (prompt stages, tone, rules, scaffold hints; `data/prompts/`, `data/blocks/`), versioned the same way and rendered with Jinja2 by `prompt_renderer.py`.
- **Stacks** (`runtime.Stack`) — where the generated code runs: a versioned skeleton file tree (Dockerfile, deps, frontend shell) plus runtime config. Builtin stacks are seeded from `runtime/scaffolding/`; user stacks are authored in the UI and get a **server-generated Dockerfile** (pinned template, allowlisted base images — see `runtime/services/stack_validation.py` and `STACK_REQUIRE_APPROVAL`).

Everything is auto-seeded from `backend/generation/data/` and `backend/runtime/scaffolding/` after every `migrate` (`seeding.py` in each app); `just manage seed_generation_templates` re-runs the generation side manually.

Under pytest's `--reuse-db`, the seeding signal only fires when the test DB is (re)created — `data/` changes need `pytest --create-db` to take effect.

`manage.py lint_generation_data` validates every requirement spec against `schema.json` and checks stack parity: every stack in `runtime/scaffolding/manifest.json` with `has_frontend: true` must have a system profile in `blocks/catalog.yaml` covering all four prompt-stage slots (backend/frontend × system/user). It's a pure data check — no DB access, no seeding required first.

## LLM sampling params

The full OpenRouter surface (temperature, top_p/top_k/min_p, max_tokens, penalties, stop sequences, response format, provider routing, reasoning effort) is configurable in layers, later layers winning:

```
profile llm_config → experiment llm_defaults → condition param_overrides → per-run llm_params
```

`generation/services/llm_params.py` owns the vocabulary and validation; the merged result is frozen into the job snapshot's `llm` section together with the model's pricing/context provenance. Sampling params deliberately never enter `prompt_hash` (so runs vary params while staying comparable) but all enter `run_fingerprint`.

## The frozen snapshot

At job creation, `profile_resolver.py` resolves the profile's block refs, renders the prompt templates, merges the LLM layers, pins the exact stack version, and freezes it all onto `GenerationJob.resolved_bundle` (historical column name — "bundle" is what profiles used to be called). Three keys come out of it:

- `prompt_hash` — prompt material only (templates + app spec + block versions). The grouping key: identical prompts across models/seeds/params share it.
- `bundle_key` — `profile-slug@version`, the analytics slicing key.
- `run_fingerprint` — everything (prompts + seed + sampling params + stack content hash). Equal fingerprints = exact-replay-equivalent runs.

## Job lifecycle

1. The API view creates the job and hands it to `dispatcher.dispatch_job`, which runs it on a daemon thread — no Celery worker needed. (Celery task variants `run_generation_job` / `run_generation_batch` exist in `generation/tasks.py` for [automation](/docs/AUTOMATION_GUIDE); never both for the same job.)
2. `GenerationService.execute` (`services/orchestrator.py`) marks the job `running` and builds an `OpenRouterClient` with the **job owner's** key, resolved by `credentials/services/resolver.py` — see [Models reference](/docs/MODELS_REFERENCE) for the precedence rules.
3. Prompts are rendered, the model is called, and the response is parsed into structured files (`code_parser.py`); copilot mode instead drives aider against a workspace and validates the result (`copilot_validation.py`).
4. `result_writer.py` persists the code and artifacts on the job. If the model response was cut off at the token limit, the affected part is flagged (`backend_truncated` / `frontend_truncated`) and later surfaced by analysis runs.

Statuses move `pending → running → completed | failed | cancelled`; errors land on the job's error field and progress streams over the `generation:<job_id>` SSE channel.

## Running the result

A completed job can be built and started as a Docker container by the runtime app — one click on the job page. Locally the app is reachable through the in-app proxy; in production it gets its own subdomain (see [Deployment guide](/docs/deployment-guide)). From there, the natural next step is an [analysis run](/docs/ANALYSIS_PIPELINE) against the same job.

## Comparing results across models

Rankings, statistics, and the `template_comparison` report accept optional `prompt_hash`/`bundle_key` filters to scope a comparison to one prompt version — without this, jobs generated before and after a profile edit get pooled into the same numbers. For running several models/profiles across a set of apps as one designed run, see [Experiments](/docs/EXPERIMENTS).
