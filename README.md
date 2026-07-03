<div align="center">

# LLM Eval Lab

**Which model writes the best code for a given task?**

Generate one application per LLM, run each in a Docker sandbox,
and score the code with 14 analysis tools.

[![CI](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/ci.yml/badge.svg)](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/ci.yml)
[![CodeQL](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/codeql.yml/badge.svg)](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/codeql.yml)
[![Gitleaks](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/gitleaks.yml/badge.svg)](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/gitleaks.yml)
[![Trivy](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/trivy.yml/badge.svg)](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/trivy.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[Quick start](#-quick-start) ·
[Deploy](#-deploy-to-a-server) ·
[Development](#-development) ·
[Contributing](#-contributing) ·
[Security](#-security) ·
[Docs](docs/README.md)

![The analyzer tool shop — 14 installable analysis tools](docs/images/analyzers.png)

</div>

## 💡 The idea

Benchmarks tell you how a model scores on puzzles; they say little about
the quality of a full application it writes. Here the experiment is
end-to-end: you pick a requirement template and a set of models from
[OpenRouter][openrouter], and for each model the platform

1. **generates** a complete application from the template
   ([how templates work](docs/TEMPLATE_SPECIFICATION.md)),
2. **runs it** in an isolated container with its own subdomain, so you can
   click through the live app,
3. **analyzes** the code with the tools you select — ruff, bandit, semgrep,
   eslint, mypy, pylint, gitleaks, detect-secrets, hadolint, codespell,
   vulture, radon, jscpd, and an LLM code reviewer,
4. **aggregates** the findings and metrics into reports and per-model
   rankings.

The comparison rests on measured findings, not gut feeling. Everything runs
from the web UI, or unattended as [automation pipelines][pipelines-doc]
composed in a node-based editor — schedule them or fan them out over a
parameter matrix. Multi-user support (email login, optional MFA, per-user
OpenRouter keys, API tokens) makes a shared instance practical.

The stack: Django 6 + Celery + PostgreSQL + Redis on the backend,
SvelteKit 2 on the frontend, everything in Docker.

This began as a master's-thesis project and is maintained by one person —
expect sharp edges, and feel free to file issues.

## 🚀 Quick start

You need Docker (with the Compose plugin),
[`just`](https://github.com/casey/just), and an
[OpenRouter API key](https://openrouter.ai/keys) — the key is only needed
for generation, browsing works without it.

```bash
git clone https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework.git
cd AutoLLM_Code_Analyzer_rework
just bootstrap              # .env files with generated secrets
just up                     # build and start the stack
just manage migrate
just manage createsuperuser
```

Then open <http://localhost:8000>, log in, and add your OpenRouter key in
the UI (or set `OPENROUTER_API_KEY` in `.envs/.local/.django` as a global
fallback).

| URL                              | Service                   |
| -------------------------------- | ------------------------- |
| <http://localhost:8000>          | Web UI (SvelteKit)        |
| <http://localhost:8001/admin/>   | Django admin              |
| <http://localhost:8001/api/docs> | API docs (staff only)     |
| <http://localhost:8025>          | Mailpit (captured emails) |
| <http://localhost:5555>          | Flower (Celery tasks)     |

To develop inside the containers instead, see
[docs/dev-containers.md](docs/dev-containers.md).

## 🌍 Deploy to a server

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/GrabowMar/AutoLLM_Code_Analyzer_rework/main/scripts/deploy.sh)
```

The script clones the repo, generates env files, starts the production
stack (`docker-compose.production.yml`: Traefik, Nginx, Gunicorn), runs
migrations, and can configure Caddy or nginx for your domain. Options are
documented in the script header.

## 🛠️ Development

The `justfile` is the entry point for everything:

```bash
just up / just down     # start / stop the stack
just logs               # tail container logs
just test               # backend tests — pytest in Docker, same as CI
just manage <cmd>       # any manage.py command
just build              # rebuild images
just prune              # remove containers and volumes
```

Linting and type checks:

```bash
uv run pre-commit run --all-files   # ruff, djlint, prettier, and friends
uv run mypy backend
cd frontend && npm run check        # svelte-check
```

How the code is laid out:

```
backend/    Django apps, one per domain: generation, analysis, automation,
            reports, rankings, runtime, users, ...
config/     settings, root URLconf, Celery app, API root
frontend/   SvelteKit app — routes, components, API client
compose/    Dockerfiles for the local and production targets
scripts/    bootstrap.py (env files), deploy.sh (server install)
docs/       deeper documentation — start at docs/README.md
```

Secrets live in `.envs/`; only `*.example` templates are committed, and
`just bootstrap` derives the real files from them.

## 🤝 Contributing

Bug reports, ideas, and PRs are welcome. The short version:

- Branch off `main` as `<type>/<short-slug>` (e.g. `fix/login-csrf`) and
  use [Conventional Commits](https://www.conventionalcommits.org/)
  prefixes: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`.
- Install the git hooks once — they run the same checks as CI:
  `uv sync && uv run pre-commit install`.
- Before pushing, run the three commands from
  [Development](#-development) above (lint, `just test`, svelte-check);
  CI gates merging on them.
- One logical change per PR; screenshots for UI changes.

Backend conventions (app layout, routers, factories) live in
[docs/app-layout.md](docs/app-layout.md); frontend design tokens in
`frontend/DESIGN_SYSTEM.md`.

## 🔒 Security

**Don't open a public issue for a vulnerability.** Report it privately via
[GitHub Security Advisories](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/security/advisories/new)
with reproduction steps and the affected commit; expect an acknowledgment
within a few business days. Only the current `main` branch is supported.
CodeQL, gitleaks, and Trivy already scan every PR — anything they missed
is still worth reporting.

## 📄 License

MIT — see [LICENSE](LICENSE).

Marcin Grabowski — [@GrabowMar](https://github.com/GrabowMar)

Built on [cookiecutter-django](https://github.com/cookiecutter/cookiecutter-django),
[shadcn-svelte](https://shadcn-svelte.com/)/bits-ui, and
[OpenRouter][openrouter].

[openrouter]: https://openrouter.ai
[pipelines-doc]: docs/AUTOMATION_WORKFLOWS.md
