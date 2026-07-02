# LLM Eval Lab

[![CI](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/ci.yml/badge.svg)](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A platform for benchmarking code written by LLMs. It generates applications
from requirement templates using models on OpenRouter, runs each generated
app in an isolated Docker container, analyzes the code with a set of tools
(ruff, bandit, semgrep, eslint, mypy, gitleaks, LLM review, ...), and turns
the results into comparative reports and rankings across models.

Built with Django 6 + Django Ninja + Celery on the backend and SvelteKit 2 +
Tailwind on the frontend. PostgreSQL and Redis underneath, everything in
Docker Compose.

## Quickstart

You need Docker with the Compose plugin and [`just`](https://github.com/casey/just).

```bash
just bootstrap                  # generate local .env files with random secrets
just up                         # start the full stack
just manage migrate
just manage createsuperuser
```

Then open <http://localhost:8000>. To generate apps you need an OpenRouter
API key — set it per-user in the UI, or globally via `OPENROUTER_API_KEY`
in `.envs/.local/.django` ([get one here](https://openrouter.ai/keys)).

Other local endpoints:

- <http://localhost:8001/admin/> — Django admin
- <http://localhost:8001/api/docs> — API docs (staff only)
- <http://localhost:8025> — Mailpit, captures dev emails

VS Code users can develop inside the stack instead — see
[docs/dev-containers.md](docs/dev-containers.md).

## Everyday commands

```bash
just up / just down             # start / stop the stack
just logs                       # tail container logs
just manage <cmd>               # any manage.py command
just build                      # rebuild images
just prune                      # remove containers + volumes
```

Tests run inside the container against the dev Postgres:

```bash
docker compose -f docker-compose.local.yml exec \
  -e DATABASE_URL="postgres://$POSTGRES_USER:$POSTGRES_PASSWORD@postgres:5432/$POSTGRES_DB" \
  django python -m pytest -q
```

Linting and type checks:

```bash
uv run pre-commit run --all-files   # ruff, djlint, prettier, and friends
uv run mypy backend
cd frontend && npm run check        # svelte-check
```

## Project layout

- `config/` — Django settings, root URLconf, Celery app, API root
- `backend/` — Django apps, one per domain: `generation`, `analysis`,
  `automation`, `reports`, `rankings`, `runtime`, and so on
- `frontend/` — SvelteKit app
- `compose/` — Dockerfiles for local and production targets
- `docs/` — deeper documentation, see [docs/README.md](docs/README.md)

Secrets live in `.envs/` — only `*.example` templates are committed,
`just bootstrap` creates the real files.

## Deployment

`scripts/deploy.sh` installs or updates a deployment on any host with Docker:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/GrabowMar/AutoLLM_Code_Analyzer_rework/main/scripts/deploy.sh)
```

It clones the repo, generates env files, starts the stack, runs migrations,
and configures Caddy or nginx for your domain. Configuration options are
documented in the script header. Production proper runs via
`docker-compose.production.yml` (Traefik, Nginx, Gunicorn).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). For security issues follow
[SECURITY.md](SECURITY.md) instead of opening a public issue.

MIT licensed — see [LICENSE](LICENSE).
