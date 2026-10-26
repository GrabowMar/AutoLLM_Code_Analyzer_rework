<div align="center">

# LLM Lab

**Which model writes the best code for a given task?**

Have each LLM build the same application, run every app in a Docker sandbox,
grade it with a hidden test suite, and score the code with 33 analysis tools.

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

</div>

## 💡 The idea

Benchmarks tell you how a model scores on puzzles; they say little about
the quality of a full application it builds. Here the experiment is
end-to-end: you pick an app spec and a set of models (from
[OpenRouter][openrouter], or your own Ollama / OpenAI-compatible server),
and for each model the platform

1. **generates** the app. In the primary *agent* mode a coding-agent
   harness (OpenCode, mini-swe-agent or Aider) works on a stack skeleton in
   an isolated sandbox, with the model behind a budget-enforcing gateway;
   template and single-prompt modes cover one-shot comparisons
   ([generation](docs/GENERATION_PROCESS.md)),
2. **grades** it: the built app is deployed with no network and checked by
   a hidden conformance suite the model never saw,
3. **runs it** in its own container so you can click through the live app,
4. **analyzes** it with the tools you install from the analyzer shop: 29
   static tools (ruff, bandit, semgrep, eslint, trivy, osv-scanner,
   gitleaks, hadolint, …), an LLM code reviewer, and three DAST scanners
   (OWASP ZAP, Nuclei, Wapiti) that attack the running app
   ([analyzer guide](docs/ANALYZER_GUIDE.md)),
5. **compares** the results in statistics, rankings and study reports.

Comparisons are run as **experiments**: apps × conditions (model, harness,
sampling preset, profile) × repeats, with a pre-registered baseline, a
reproducibility seed, a live monitor, cron schedules and post-run analysis
([experiments](docs/EXPERIMENTS.md)). A dedicated executor process runs every
job under leases and fair capacity lanes, so runs survive web restarts
([execution](docs/EXECUTION.md)). Study tooling covers deduplicated findings,
build-healing ablation, a manual-review sample and Markdown/LaTeX study
reports ([study tables](docs/STUDY_TABLES.md)).

Outward, the platform exposes a REST API with personal tokens, an MCP server
for Claude, ChatGPT and other MCP clients, GitHub export and webhooks
([integrations](docs/INTEGRATIONS.md)). Multi-user support (email login,
optional MFA, per-user encrypted API keys) makes a shared instance practical.

The stack: Django 6 + django-ninja + PostgreSQL + Redis (+ Celery for a few
background tasks) on the backend, SvelteKit (Svelte 5) on the frontend,
everything in Docker, with untrusted code confined to a Docker-in-Docker
sandbox.

This began as a master's-thesis project and is maintained by one person.
Expect sharp edges, and feel free to file issues.

## 🚀 Quick start

You need Docker (with the Compose plugin and permission to run one
privileged container), [`just`](https://github.com/casey/just), Python 3 for
the bootstrap script, and an
[OpenRouter API key](https://openrouter.ai/keys) (or a local model server).
The key is only needed for generation; browsing works without it.

```bash
git clone https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework.git
cd AutoLLM_Code_Analyzer_rework
just bootstrap          # .env files with generated secrets
just build && just up   # build and start the stack
```

Migrations and seed data (analyzer tools, generation library, stacks, run
presets) are applied automatically when the django container starts. Open
<http://localhost:8000>: on an empty database the sign-up page creates the
initial admin account. Then add your OpenRouter key under
**Settings → API keys** and sync the model catalog from **Models**.

| URL                              | Service                   |
| -------------------------------- | ------------------------- |
| <http://localhost:8000>          | Web UI (SvelteKit)        |
| <http://localhost:8001/admin/>   | Django admin              |
| <http://localhost:8001/api/docs> | API docs (staff only)     |
| <http://localhost:8025>          | Mailpit (captured emails) |
| <http://localhost:5555>          | Flower (Celery tasks)     |

The full walkthrough is in [docs/QUICKSTART.md](docs/QUICKSTART.md); the
[user guide](docs/USER_GUIDE.md) tours the UI page by page.

## 🌍 Deploy to a server

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/GrabowMar/AutoLLM_Code_Analyzer_rework/main/scripts/deploy.sh)
```

The script handles first-time installs and in-place updates of the local
stack behind an existing Caddy (or nginx) proxy; options are documented in
the script header. For the production compose stack (Traefik with wildcard
TLS, per-app subdomains) and its caveats, see
[docs/deployment-guide.md](docs/deployment-guide.md).

## 🛠️ Development

The `justfile` is the entry point for everything:

```bash
just up / just down     # start / stop the stack
just logs [service]     # tail container logs
just test [args]        # backend tests: pytest in Docker, same as CI
just manage <cmd>       # any manage.py command
just build              # rebuild images
just prune              # remove containers and volumes
```

Linting, type checks and frontend tests:

```bash
uv run pre-commit run --all-files   # ruff, djlint, prettier, and friends
uv run mypy backend
cd frontend && npm run check && npm test   # svelte-check, vitest
```

How the code is laid out:

```
backend/    Django apps, one per domain: generation, execution, inference,
            analysis, statistics, reports, rankings, runtime, integrations, ...
config/     settings, root URLconf, Celery app, API root
frontend/   SvelteKit app: routes, components, API client
relay/      the relay script that connects a GPU box's model server
compose/    Dockerfiles for the local and production targets
scripts/    bootstrap.py (env files), deploy.sh (server install)
docs/       deeper documentation, start at docs/README.md
```

Secrets live in `.envs/`; only `*.example` templates are committed, and
`just bootstrap` derives the real files from them.

Docs under `docs/` are served live by the in-app viewer at `/docs`. The
[development guide](docs/development-guide.md) covers the workflow and the
conventions for writing them.

## 🤝 Contributing

Bug reports, ideas, and PRs are welcome. The short version:

- Branch off `main` as `<type>/<short-slug>` (e.g. `fix/login-csrf`) and
  use [Conventional Commits](https://www.conventionalcommits.org/)
  prefixes: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`.
- Install the git hooks once — they run the same checks as CI:
  `uv sync && uv run pre-commit install`.
- Before pushing, run the commands from
  [Development](#-development) above (lint, `just test`, svelte-check and vitest);
  CI gates merging on them.
- One logical change per PR; screenshots for UI changes.

Backend conventions (app layout, routers, error handling) live in the
[development guide](docs/development-guide.md); frontend design tokens in
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
