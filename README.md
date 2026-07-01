# LLM Eval Lab

A Django + SvelteKit platform for **generating, executing, and benchmarking
AI-generated applications** across multiple LLM providers (OpenRouter, etc.).
It runs each generated app in an isolated Docker container, streams analysis
results in real time, and produces comparative reports across models.

[![CI](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/ci.yml/badge.svg)](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/ci.yml)
[![CodeQL](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/codeql.yml/badge.svg)](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/codeql.yml)
[![Built with Cookiecutter Django](https://img.shields.io/badge/built%20with-Cookiecutter%20Django-ff69b4.svg?logo=cookiecutter)](https://github.com/cookiecutter/cookiecutter-django/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Stack

| Layer        | Technology                                                      |
| ------------ | --------------------------------------------------------------- |
| Backend      | Django 6 · Python 3.13 · Django Ninja (REST) · Celery · Redis   |
| Frontend     | SvelteKit 2 · TypeScript · Tailwind CSS 4 · Vite · bits-ui      |
| Auth         | django-allauth (headless, email-only login, MFA support)        |
| Database     | PostgreSQL 17                                                   |
| Infra        | Docker Compose (local + production) · `uv` · `just`             |

---

## Quickstart (Docker)

You need Docker, Docker Compose, and [`just`](https://github.com/casey/just).
Everything else lives in containers.

```bash
# 1. Generate local .env files with random secrets
just bootstrap

# 2. (optional) Edit .envs/.local/.django to set OPENROUTER_API_KEY

# 3. Bring the full stack up (Django, Postgres, Redis, Celery, Mailpit, Frontend)
just up

# 4. Apply migrations
just manage migrate

# 5. Create a superuser
just manage createsuperuser
```

Then open:

| URL                              | What                                                |
| -------------------------------- | --------------------------------------------------- |
| <http://localhost:8000>          | SvelteKit frontend (dev server, hot-reload)         |
| <http://localhost:8001>          | Django app directly (admin, API, allauth headless)  |
| <http://localhost:8001/admin/>   | Django admin                                        |
| <http://localhost:8001/api/docs> | Django Ninja API docs (staff-only)                  |
| <http://localhost:8025>          | Mailpit — captures dev emails                       |

> The frontend container proxies `/api`, `/_allauth`, `/admin`, and `/media`
> to the Django container, so use `http://localhost:8000` for normal browsing.

---

## Dev Containers (VS Code)

Prerequisites: **Docker Desktop** running and the
[Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
extension.

1. Open this folder in VS Code.
2. **Dev Containers: Reopen in Container** (Command Palette).

On first open, [`initializeCommand`](.devcontainer/devcontainer.json) runs
`scripts/bootstrap.py` on your machine to create `.envs/.local/.django` and
`.postgres` from the committed templates (same as `just bootstrap`). You can
also run bootstrap manually before reopening:

```bash
just bootstrap
# or
python scripts/bootstrap.py
```

On Windows, if `python` is not on PATH, try `py -3 scripts/bootstrap.py`.

The dev container attaches to the **`django`** Compose service as user
**`dev-user`**, with the workspace at **`/app`**. The full local Compose stack
starts (same services as `just up`): Postgres, Redis, Mailpit, Django, Celery,
Flower, and frontend.

After the container is ready:

```bash
python manage.py createsuperuser   # optional — migrate runs via /start on django
```

Browse at <http://localhost:8000> (frontend) and <http://localhost:8001> (Django).
Django starts in the background after attach (`/tmp/django-start.log` in the
container). If `django-1` exits with **password authentication failed**, reset
the Postgres volume: `docker compose -f docker-compose.local.yml down -v`, then
`just up`.

If Celery or frontend are missing in Docker Desktop, from the **host** run:

```bash
docker compose -f docker-compose.local.yml up -d
```

| URL                     | What                                       |
| ----------------------- | ------------------------------------------ |
| <http://localhost:8000> | SvelteKit frontend (if `frontend` is up)   |
| <http://localhost:8001> | Django (admin, API, allauth)               |
| <http://localhost:8025> | Mailpit                                    |

**Troubleshooting:** If reopen fails with `env file ... .django not found`, run
`python scripts/bootstrap.py`, then verify
`docker compose -f docker-compose.local.yml config` exits 0, and reopen.
A `Could not connect to WSL` / read-only `/root` message from Docker Desktop
is usually harmless if Docker Engine is running. If attach dies with **exit code
137** during “Installing VS Code Server”, give Docker Desktop **≥ 6–8 GB RAM**
(Settings → Resources), run `docker compose -f docker-compose.local.yml down`,
then **Rebuild and Reopen in Container**.

---

## Secrets & environment

All env files live under `.envs/{.local,.production}/`. Only the `*.example`
templates are committed; real env files are **gitignored**.

- `just bootstrap` (or `python scripts/bootstrap.py`) copies the local
  templates to real files and generates fresh random `POSTGRES_USER`,
  `POSTGRES_PASSWORD`, `CELERY_FLOWER_USER`, and `CELERY_FLOWER_PASSWORD`
  values.
- Fill in `OPENROUTER_API_KEY` yourself — get one at
  <https://openrouter.ai/keys>.
- For production, copy the `.envs/.production/*.example` files to real
  files on the deployment host and fill them in manually. Never commit them.

Maintained as a single contributor today (see [SECURITY.md](SECURITY.md) for
how to report issues privately).

---

## Common tasks

```bash
just bootstrap              # generate local .env files (idempotent)
just up                     # start everything
just down                   # stop everything
just logs                   # tail container logs
just manage <cmd>           # run any manage.py command
just frontend-dev           # frontend dev server (foreground)
just frontend-build         # production frontend build
just build                  # rebuild images
just prune                  # nuke containers + volumes
```

### Tests

The Django test suite runs inside the container against the dev Postgres:

```bash
docker exec -e DATABASE_URL="postgres://$POSTGRES_USER:$POSTGRES_PASSWORD@postgres:5432/$POSTGRES_DB" \
  app-django-1 python -m pytest -q
```

### Lint, format, type-check

All Python tooling is driven via `uv`:

```bash
uv run pre-commit run --all-files   # ruff + djlint + django-upgrade + more
uv run ruff check backend --fix     # lint only
uv run ruff format backend          # format only
uv run mypy backend                 # type-check
```

Frontend:

```bash
cd frontend
npm install
npm run check                       # svelte-check
npm run build                       # production build
```

---

## Architecture

- `config/` — Django project (settings split by env, root URLconf, Celery
  app, Ninja API root).
- `backend/` — Django apps (one per bounded context): `users`, `llm_models`,
  `generation`, `runtime`, `analysis`, `reports`, `rankings`, `statistics`,
  `automation`, `realtime`, `tokens`. See [`docs/app-layout.md`](docs/app-layout.md)
  for the standard app structure and conventions.
- `frontend/` — SvelteKit application; routes live under
  `frontend/src/routes/(auth)` and `frontend/src/routes/(app)`.
- `compose/` — Dockerfiles and entrypoints for local + production targets.
- `docs/` — In-depth docs: see [`docs/README.md`](docs/README.md) for the
  full index.

### API conventions (Django Ninja)

APIs are function-based routers, registered in `config/api.py`:

```python
from ninja import Router
router = Router(tags=["my-app"])

@router.get("/", response=list[MySchema])
def list_items(request):
    ...
```

`SessionAuth` is the global default; `TokenAuth` is available for programmatic
clients (see `backend/tokens/`).

### Auth

`django-allauth` runs in **headless** mode — the classic
`account_login`/`account_signup` URLs are **not** registered. The SvelteKit
frontend talks to the headless API under `/_allauth/browser/v1/*` and renders
auth pages at `/auth/login`, `/auth/signup`, `/auth/password/reset`,
`/auth/2fa`, `/auth/verify-email`.

---

## Deployment

Production runs via `docker-compose.production.yml` (Traefik + Nginx +
Gunicorn/Uvicorn + Postgres + Redis). `scripts/deploy.sh` automates a
first-time install or in-place update on a host with Docker installed —
see the header comment in that script for configuration options.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev workflow, branch naming,
and PR expectations. Security issues — please follow [SECURITY.md](SECURITY.md)
instead of opening a public issue.

## License

MIT — see [LICENSE](LICENSE).
