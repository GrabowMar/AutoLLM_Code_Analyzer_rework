# Generation process

What happens between clicking Generate and having a runnable app: the three modes, template assembly, and the job lifecycle.

## Three modes

`GenerationJob.mode` (`backend/generation/models.py`) selects the strategy:

| Mode | What it does |
| --- | --- |
| `custom` | Free-form: your prompt goes to the model, the response is parsed into code files |
| `scaffolding` | Template-driven: a scaffolding template plus an app requirement produce structured backend/frontend prompts; output slots into a known project skeleton |
| `copilot` | Agentic: [aider](https://aider.chat) iterates on a real git workspace (`aider_runner.py`, `copilot_workspace.py`), producing genuine multi-file projects |

Scaffolding is the mode used for comparable model benchmarks — every model gets the same skeleton and requirements, so differences in the output are differences between models.

## Templates and bundles

Auto-seeded from `backend/generation/data/` after every `migrate` (`backend/generation/seeding.py`, `apps.py`); `just manage seed_generation_templates` re-runs it manually:

- **ScaffoldingTemplate** — a tech stack skeleton (seeds: `flask-react`, `fastapi-vue`, `fastapi-react`).
- **AppRequirementTemplate** — what to build, as structured requirement JSON (`data/requirements/`, 33 specs like `crud_todo_list`, validated against `data/requirements/schema.json`). One mutable row per slug; `version`/`content_hash` bump when the spec content changes. Each spec carries a `difficulty` tier (`basic`/`standard`/`advanced`) inferred from endpoint/field counts — a coarse research-metadata signal, not a hard guarantee.
- **PromptTemplate** (legacy, admin-UI only) and **ContentBlock** — the prompt text and reusable fragments (`data/prompts/`, `data/blocks/`), assembled with Jinja2 by `prompt_renderer.py`. `ContentBlock` is versioned by content hash: editing a source file creates a new `(slug, version)` row rather than overwriting the one a past job's `resolved_bundle` snapshot points to.
- **TemplateBundle** — a versioned, ordered set of content blocks + scaffold slug, editable through the API (`POST` creates v1, `PUT` creates v1+1, `DELETE` archives — versions are immutable) or importable/exportable as packages (`generation/services/bundle_packages/`).

Under pytest's `--reuse-db`, the seeding signal only fires when the test DB is (re)created — `data/` changes need `pytest --create-db` to take effect.

`manage.py lint_generation_data` validates every requirement spec against `schema.json` and checks stack parity: every stack in `runtime/scaffolding/manifest.json` with `has_frontend: true` must have a system bundle in `blocks/catalog.yaml` covering all four prompt-stage slots (backend/frontend × system/user). It's a pure data check — no DB access, no seeding required first.

## Job lifecycle

1. The API view creates the job and hands it to `dispatcher.dispatch_job`, which runs it on a daemon thread — no Celery worker needed. (Celery task variants `run_generation_job` / `run_generation_batch` exist in `generation/tasks.py` for [automation](/docs/AUTOMATION_GUIDE); never both for the same job.)
2. `GenerationService.execute` (`services/orchestrator.py`) marks the job `running` and builds an `OpenRouterClient` with the **job owner's** key, resolved by `credentials/services/resolver.py` — see [Models reference](/docs/MODELS_REFERENCE) for the precedence rules.
3. Prompts are rendered, the model is called, and the response is parsed into structured files (`code_parser.py`); copilot mode instead drives aider against a workspace and validates the result (`copilot_validation.py`).
4. `result_writer.py` persists the code and artifacts on the job. If the model response was cut off at the token limit, the affected part is flagged (`backend_truncated` / `frontend_truncated`) and later surfaced by analysis runs.

Statuses move `pending → running → completed | failed | cancelled`; errors land on the job's error field and progress streams over the `generation:<job_id>` SSE channel.

## Running the result

A completed job can be built and started as a Docker container by the runtime app — one click on the job page. Locally the app is reachable through the in-app proxy; in production it gets its own subdomain (see [Deployment guide](/docs/deployment-guide)). From there, the natural next step is an [analysis run](/docs/ANALYSIS_PIPELINE) against the same job.
