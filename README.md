<div align="center">

<h1>🧪 LLM Eval Lab</h1>

<p><strong>Generate applications with LLMs, run them in sandboxes, and
benchmark the code they write.</strong></p>

<p>
<a href="https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/ci.yml"><img src="https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
<a href="https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/codeql.yml"><img src="https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/actions/workflows/codeql.yml/badge.svg" alt="CodeQL"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
</p>

<p>
<img src="https://img.shields.io/badge/Python_3.13-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
<img src="https://img.shields.io/badge/Django_6-092E20?style=for-the-badge&logo=django&logoColor=white" alt="Django">
<img src="https://img.shields.io/badge/SvelteKit_2-FF3E00?style=for-the-badge&logo=svelte&logoColor=white" alt="SvelteKit">
<img src="https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white" alt="TypeScript">
<img src="https://img.shields.io/badge/Tailwind_4-06B6D4?style=for-the-badge&logo=tailwindcss&logoColor=white" alt="Tailwind CSS">
<img src="https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL">
<img src="https://img.shields.io/badge/Redis-DC382D?style=for-the-badge&logo=redis&logoColor=white" alt="Redis">
<img src="https://img.shields.io/badge/Celery-37814A?style=for-the-badge&logo=celery&logoColor=white" alt="Celery">
<img src="https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker">
</p>

</div>

<details>
<summary>📚 Table of contents</summary>

- [🔍 About](#-about)
- [✨ Features](#-features)
- [⚙️ How it works](#%EF%B8%8F-how-it-works)
- [🚀 Getting started](#-getting-started)
- [🕹️ Usage](#%EF%B8%8F-usage)
- [🛠️ Development](#%EF%B8%8F-development)
- [🗂️ Project structure](#%EF%B8%8F-project-structure)
- [🤝 Contributing](#-contributing)
- [📄 License](#-license)
- [📫 Contact](#-contact)
- [🙏 Acknowledgments](#-acknowledgments)

</details>

## 🔍 About

LLM Eval Lab answers a simple question: *which model writes the best code
for a given task?* You pick a requirement template and a set of models from
[OpenRouter](https://openrouter.ai), the platform generates a complete
application per model, runs each one in an isolated Docker container, and
puts the code through a battery of static-analysis and security tools. The
results feed reports and rankings, so model comparisons rest on measured
findings rather than gut feeling.

It started as a master's-thesis project and is maintained by a single
developer, so expect sharp edges — issues and PRs are welcome.

## ✨ Features

| | Feature | What you get |
| --- | --- | --- |
| 🤖 | **Code generation** | Prompt multiple LLMs with the same app template; collect complete, runnable applications |
| 📦 | **Sandboxed execution** | Every generated app runs in its own Docker container with subdomain routing |
| 🔬 | **14 analyzers** | ruff, bandit, semgrep, eslint, mypy, pylint, gitleaks, detect-secrets, hadolint, codespell, vulture, radon, jscpd, and an LLM code reviewer |
| 📊 | **Reports & rankings** | Findings aggregated into comparative reports, statistics, and per-model rankings |
| 🔁 | **Automation pipelines** | A visual node-based editor to chain generate → analyze → report workflows |
| 👥 | **Multi-user** | Email login with optional MFA, per-user OpenRouter keys, API tokens for programmatic access |

## ⚙️ How it works

```mermaid
flowchart LR
    T["📝 Requirement
    template"] --> G["🤖 Generation
    one app per model"]
    G --> S["📦 Sandbox
    Docker per app"]
    S --> A["🔬 Analysis
    14 tools"]
    A --> R["📊 Reports
    & rankings"]
```

The whole loop can run interactively from the UI, or unattended as an
[automation pipeline](docs/AUTOMATION_WORKFLOWS.md).

## 🚀 Getting started

### Prerequisites

- Docker with the Compose plugin
- [`just`](https://github.com/casey/just) — command runner
- An [OpenRouter API key](https://openrouter.ai/keys) (needed for
  generation; browsing works without one)

### Install

```bash
git clone https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework.git
cd AutoLLM_Code_Analyzer_rework
just bootstrap        # create local .env files with random secrets
just up               # build and start the stack
just manage migrate
just manage createsuperuser
```

Open <http://localhost:8000> and log in. Add your OpenRouter key in the
UI (per user), or set `OPENROUTER_API_KEY` in `.envs/.local/.django` as a
global fallback.

Useful local endpoints:

| URL                              | What                      |
| -------------------------------- | ------------------------- |
| <http://localhost:8000>          | Frontend                  |
| <http://localhost:8001/admin/>   | Django admin              |
| <http://localhost:8001/api/docs> | API docs (staff only)     |
| <http://localhost:8025>          | Mailpit (captured emails) |

Prefer developing inside the containers? See
[docs/dev-containers.md](docs/dev-containers.md) for the VS Code setup.

### Deploy to a server

One command on any Docker host:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/GrabowMar/AutoLLM_Code_Analyzer_rework/main/scripts/deploy.sh)
```

The script clones the repo, generates env files, starts the stack, runs
migrations, and configures Caddy or nginx for your domain. Options are
documented in the script header; production proper uses
`docker-compose.production.yml` (Traefik, Nginx, Gunicorn).

## 🕹️ Usage

The typical loop:

1. **Pick or write a template** under *Sample generator → Templates* —
   templates describe the app to build (see
   [docs/TEMPLATE_SPECIFICATION.md](docs/TEMPLATE_SPECIFICATION.md))
2. **Generate** — select models, generate one app per model
3. **Analyze** — run an analysis profile (a preset selection of the 14
   tools) against the generated apps
4. **Compare** — open *Reports* and *Rankings* to see how the models did

Repetitive experiments can be scripted as
[automation pipelines](docs/AUTOMATION_WORKFLOWS.md) instead of clicking
through the steps.

## 🛠️ Development

```bash
just up / just down    # start / stop the stack
just logs              # tail container logs
just manage <cmd>      # any manage.py command
just build             # rebuild images
just prune             # remove containers + volumes
```

Tests run inside the container against the dev Postgres:

```bash
docker compose -f docker-compose.local.yml exec \
  -e DATABASE_URL="postgres://$POSTGRES_USER:$POSTGRES_PASSWORD@postgres:5432/$POSTGRES_DB" \
  django python -m pytest -q
```

Linting and type checks:

```bash
uv run pre-commit run --all-files    # ruff, djlint, prettier, and friends
uv run mypy backend
cd frontend && npm run check         # svelte-check
```

## 🗂️ Project structure

```
backend/     Django apps, one per domain (generation, analysis, automation,
             reports, rankings, runtime, users, ...)
config/      Settings, root URLconf, Celery app, API root
frontend/    SvelteKit app (routes, components, API client)
compose/     Dockerfiles for local and production targets
scripts/     bootstrap.py (env files), deploy.sh (server install)
docs/        Deeper documentation — start at docs/README.md
```

Secrets live in `.envs/` — only `*.example` templates are committed;
`just bootstrap` creates the real files.

## 🤝 Contributing

Bug reports, ideas, and PRs are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md) for the workflow. For security
vulnerabilities, follow [SECURITY.md](SECURITY.md) instead of opening a
public issue.

## 📄 License

Distributed under the MIT License — see [LICENSE](LICENSE).

## 📫 Contact

Marcin Grabowski — [@GrabowMar](https://github.com/GrabowMar)

## 🙏 Acknowledgments

- [cookiecutter-django](https://github.com/cookiecutter/cookiecutter-django)
  for the project skeleton
- [shadcn-svelte](https://shadcn-svelte.com/) / bits-ui for UI components
- [OpenRouter](https://openrouter.ai) for unified model access
