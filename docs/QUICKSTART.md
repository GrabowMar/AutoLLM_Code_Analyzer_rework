# Quickstart

Get a local LLM Lab instance running with Docker Compose, from clone to your first generated and analyzed app.

## Prerequisites

- Docker with the Compose plugin, able to run one privileged container: analysis tool containers and generated sample apps run nested inside the `apps-dind` service (Docker-in-Docker), never on the host daemon.
- [just](https://github.com/casey/just) and Python 3 on the host (only used to run the bootstrap script; everything else runs in containers).
- An [OpenRouter API key](https://openrouter.ai/keys). Not needed to boot the stack, but nothing can be generated or AI-reviewed without one.

## Bootstrap

```bash
git clone <repo-url> && cd AutoLLM_Code_Analyzer_rework
just bootstrap
```

`just bootstrap` runs `scripts/bootstrap.py`: it copies `.envs/.local/.django.example` and `.envs/.local/.postgres.example` to their real names and fills in random Flower and Postgres credentials. It is idempotent and never overwrites existing `.env` files.

Optionally edit `.envs/.local/.django` now to set `OPENROUTER_API_KEY` as a global fallback key — or skip it and add a personal key per user in the UI later (see [Models reference](/docs/MODELS_REFERENCE)).

## Build and start

```bash
just build
just up
```

| Service | URL / port | Role |
| --- | --- | --- |
| frontend | http://localhost:8000 | SvelteKit dev server — **this is the app** |
| django | http://localhost:8001 | API backend (the frontend proxies `/api` to it) |
| mailpit | http://localhost:8025 | Catches all outgoing email (verification links) |
| flower | http://localhost:5555 | Celery dashboard (credentials from bootstrap) |
| postgres, redis, celeryworker, celerybeat | internal | Database, broker, background workers |

On startup the django container runs `python manage.py migrate` automatically, and both the analyzer tool catalog and the generation library (stacks, app specs, blocks, profiles) seeds itself after every migrate. One thing is **not** automatic:

```bash
just manage createsuperuser
```

## First run

1. Open http://localhost:8000 and sign in (or register — the verification email lands in mailpit at :8025).
2. Add your OpenRouter key under **Settings → API Credentials**. It is stored encrypted and takes precedence over the global fallback key.
3. Sync the model catalog from the Models page so there is something to generate with.
4. Create a generation job (pick a model, a scaffolding template or a custom prompt) and wait for it to finish.
5. Open the job and start an analysis run (or create one under **Analysis → Runs** with a profile such as `research-default`). The first run builds the pinned analyzer image `llmlab/analyzer:<hash>` on the apps daemon, which takes several minutes; later runs reuse it. You can build it ahead of time from **Analysis → Tools** or with `just manage analysis_build_image`, and check every tool with the canary self-test there.

> [!NOTE]
> Interactive generation runs on background threads inside the django container; restarting it kills them. Analysis runs are durable: a run whose worker died is requeued — see [Troubleshooting](/docs/TROUBLESHOOTING).

## Next steps

- [Development guide](/docs/development-guide) — just targets, tests, env vars, code layout.
- [Architecture](/docs/ARCHITECTURE) — how the pieces fit together.
- [Research guide](/docs/RESEARCH_GUIDE) — run a pre-registered analysis study.
- [Troubleshooting](/docs/TROUBLESHOOTING) — when something above didn't work.
