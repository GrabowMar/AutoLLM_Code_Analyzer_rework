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

Seeded by `just manage seed_generation_templates` from `backend/generation/data/`:

- **ScaffoldingTemplate** — a tech stack skeleton (seeds: `flask-react`, `fastapi-vue`, `fastapi-react`).
- **AppRequirementTemplate** — what to build, as structured requirement JSON (`data/requirements/`, 33 specs like `crud_todo_list`).
- **PromptTemplate** and **ContentBlock** — the prompt text and reusable fragments (`data/prompts/`, `data/blocks/`), assembled with Jinja2 by `prompt_renderer.py`.
- **TemplateBundle** — a shareable package of the above, importable/exportable through the API (`generation/services/bundle_packages/`).

## Job lifecycle

1. The API view creates the job and hands it to `dispatcher.dispatch_job`, which runs it on a daemon thread — no Celery worker needed. (Celery task variants `run_generation_job` / `run_generation_batch` exist in `generation/tasks.py` for [automation](/docs/AUTOMATION_GUIDE); never both for the same job.)
2. `GenerationService.execute` (`services/orchestrator.py`) marks the job `running` and builds an `OpenRouterClient` with the **job owner's** key, resolved by `credentials/services/resolver.py` — see [Models reference](/docs/MODELS_REFERENCE) for the precedence rules.
3. Prompts are rendered, the model is called, and the response is parsed into structured files (`code_parser.py`); copilot mode instead drives aider against a workspace and validates the result (`copilot_validation.py`).
4. `result_writer.py` persists the code and artifacts on the job. If the model response was cut off at the token limit, the affected part is flagged (`backend_truncated` / `frontend_truncated`) and later surfaced by analysis runs.

Statuses move `pending → running → completed | failed | cancelled`; errors land on the job's error field and progress streams over the `generation:<job_id>` SSE channel.

## Running the result

A completed job can be built and started as a Docker container by the runtime app — one click on the job page. Locally the app is reachable through the in-app proxy; in production it gets its own subdomain (see [Deployment guide](/docs/deployment-guide)). From there, the natural next step is an [analysis run](/docs/ANALYSIS_PIPELINE) against the same job.
