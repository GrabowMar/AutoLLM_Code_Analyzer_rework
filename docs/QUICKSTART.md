# Quickstart

Get a local LLM Lab instance running with Docker Compose, from clone to your first generated and analyzed app.

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
| `executor` | internal | Runs every generation job, evaluation, experiment schedule and post-run |
| `apps-dind` | internal | Sandbox Docker daemon for apps, sandboxes and workspaces |
| `postgres`, `redis`, `celeryworker`, `celerybeat` | internal | Database, broker and pub/sub, background tasks |

On startup the django container runs `python manage.py migrate`. After every migrate the analyzer tool catalog, the generation library (run presets, app specs, prompts, blocks, profiles), the built-in stacks and the system OpenRouter endpoint seed themselves. The executor waits for migrations to finish before it starts.

## First run

1. Open http://localhost:8000. On an empty database the sign-up page creates the **initial admin account**: a superuser whose email is `admin@<DJANGO_DOMAIN>` (`admin@example.com` with the template's `DJANGO_DOMAIN`), which you then sign in with. `just manage createsuperuser` works too if you prefer your own email. Later sign-ups create normal accounts; locally email verification is optional and the mails land in mailpit.
2. Add your OpenRouter key under **Settings → API keys** (`/users/settings#credentials`). It is stored encrypted and takes precedence over the global fallback key.
3. Open **Models** and sync the catalog. Sync is staff-only and uses the staff user's own key, not the global fallback.
4. Generate something: a single app from **Sample Generator**, or a planned set of runs from **Experiments** (see [Experiments](/docs/EXPERIMENTS)). The executor picks the job up within a few seconds.
5. Open the finished app under **Applications** and start an analysis. The first run provisions your personal analyzer workspace container (building the `backend/analyzer-base:latest` image) and installs the selected tools, so it takes a while; later runs reuse it.

> [!NOTE]
> Generation jobs run in the `executor` service and survive web restarts. Interactive analysis runs, workspace provisioning and tool installs still run on threads inside the `django` container, so restarting it (or a backend code reload) kills those. See [Background services](/docs/BACKGROUND_SERVICES).

## Next steps

- [User guide](/docs/USER_GUIDE): the app page by page.
- [Development guide](/docs/development-guide): just targets, tests, env vars, code layout.
- [Architecture](/docs/ARCHITECTURE): how the pieces fit together.
- [Troubleshooting](/docs/TROUBLESHOOTING): when something above did not work.
