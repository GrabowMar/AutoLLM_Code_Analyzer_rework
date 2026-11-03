# Quickstart

Get a local LLM Lab instance running with Docker Compose, from clone to your first study: an agentic harness builds an app in a sandbox, the app is evaluated and analyzed.

## Prerequisites

- Docker with the Compose plugin, able to run one privileged container. Generated apps, agent sandboxes and analyzer workspaces run as nested containers inside the `apps-dind` service (Docker-in-Docker), never on the host daemon.
- [just](https://github.com/casey/just) and Python 3 on the host. Python only runs the bootstrap script; everything else runs in containers.
- Enough memory: the services' caps in `docker-compose.local.yml` add up to about 15 GB (apps-dind alone 4 GB). Idle use is far lower, but agent runs and app builds need several GB.
- An [OpenRouter API key](https://openrouter.ai/keys). Not needed to boot the stack, but nothing can be generated or AI-reviewed without one, unless you connect a local model server instead (see [Local models](/docs/LOCAL_MODELS)).

## Bootstrap

```bash
git clone https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework.git
cd AutoLLM_Code_Analyzer_rework
just bootstrap
```

`just bootstrap` runs `scripts/bootstrap.py`. It copies `.envs/.local/.django.example` and `.envs/.local/.postgres.example` to `.envs/.local/.django` and `.envs/.local/.postgres` and replaces the `changeme` placeholders with random Flower and Postgres credentials. It never overwrites an existing file.

Optionally set `OPENROUTER_API_KEY` in `.envs/.local/.django` as a global fallback key. You can skip this and add a personal key per user in the UI later (see [Models reference](/docs/MODELS_REFERENCE)).

## Build and start

```bash
just build
just up
```

| Service | URL / port | Role |
| --- | --- | --- |
| `frontend` | http://localhost:8000 | SvelteKit dev server. **This is the app.** |
| `django` | http://localhost:8001 | API backend; the frontend proxies `/api`, `/_allauth`, `/admin`, `/media` and `/apps` to it |
| `mailpit` | http://localhost:8025 | Catches all outgoing email |
| `flower` | http://localhost:5555 | Celery dashboard (credentials from bootstrap, in `.envs/.local/.django`) |
| `executor` | internal | Runs every generation run, evaluation, study schedule and post-run |
| `apps-dind` | internal | Sandbox Docker daemon for apps, sandboxes and workspaces |
| `postgres`, `redis`, `celeryworker`, `celerybeat` | internal | Database, broker and pub/sub, background tasks |

On startup the django container runs `python manage.py migrate`. After every migrate the analyzer tool catalog and the system OpenRouter endpoint seed themselves, and the executor waits for migrations to finish before it starts.

The generation library (tasks with their prose variants, stack profiles and starters, styles, guidance, prompt templates and prompt sets, harness presets, sampling presets, the `frugal` / `standard` / `generous` budget presets, evaluation suites and pattern detectors) is seeded from `backend/generation/data/` by `just manage seed_library`, or automatically after every migrate when `GENERATION_SEED_ON_MIGRATE=true` is set in `.envs/.local/.django`. Seeding is idempotent: unchanged content is a no-op, changed seed content becomes the next version (existing versions are immutable), and user-owned slugs are never touched. See [The generation library](/docs/LIBRARY).

## First run

1. Open http://localhost:8000. On an empty database the sign-up page creates the **initial admin account**: a superuser whose email is `admin@<DJANGO_DOMAIN>` (`admin@example.com` with the template's `DJANGO_DOMAIN`), which you then sign in with. `just manage createsuperuser` works too if you prefer your own email. Later sign-ups create normal accounts; locally email verification is optional and the mails land in mailpit.
2. Add your OpenRouter key under **Settings → API keys** (`/users/settings#credentials`). It is stored encrypted and takes precedence over the global fallback key.
3. Open **Models** and sync the catalog. Sync is staff-only and uses the staff user's own key, not the global fallback.
4. Generate something: **Studies** → **New study**. Pick one task as the unit and one model and one stack profile in the factor table; every other factor has a default (harness preset `opencode-native`, style `model-choice`, auth `contract_bearer`, prose variant `standard`, the `frugal` budget preset). The plan rail previews every rendered prompt, the seeds, the cost estimate against the cap and any issues; **Start** launches it (see [Studies](/docs/STUDIES)). The executor picks the runs up within a few seconds. The same flow is one API call: `POST /api/generation/studies/adhoc/` with a design.
5. Open the finished sample under **Applications** (its outcome, manifest, files, trajectory and evaluation) and start an analysis. The first run provisions your personal analyzer workspace container (building the `backend/analyzer-base:latest` image) and installs the selected tools, so it takes a while; later runs reuse it.

> [!NOTE]
> Generation runs and evaluations run in the `executor` service and survive web restarts. Interactive analysis runs, workspace provisioning and tool installs still run on threads inside the `django` container, so restarting it (or a backend code reload) kills those. See [Background services](/docs/BACKGROUND_SERVICES).

## Next steps

- [User guide](/docs/USER_GUIDE): the app page by page.
- [Development guide](/docs/development-guide): just targets, tests, env vars, code layout.
- [Architecture](/docs/ARCHITECTURE): how the pieces fit together.
- [Troubleshooting](/docs/TROUBLESHOOTING): when something above did not work.
