# Security Policy

## Reporting a vulnerability

**Please don't open a public GitHub issue for security problems.**

Report them privately through GitHub's
[Security Advisories](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/security/advisories/new)
so the issue can be triaged and fixed before public disclosure. Include:

- what the issue is and its impact
- steps to reproduce or a proof of concept
- the affected commit or branch
- any suggested mitigation

Reports are acknowledged within a few business days; the goal is a fix or
mitigation within 30 days of confirmation, depending on complexity.

## Scope

In scope:

- the Django backend (`backend/`, `config/`) and its REST API
- the SvelteKit frontend (`frontend/`)
- Docker/Compose configuration (`compose/`, `docker-compose.*.yml`)
- deployment tooling (`scripts/deploy.sh` and friends)

Out of scope:

- issues requiring physical access to a developer's machine
- automated-scanner findings with no demonstrated impact
- third-party services (OpenRouter, Sentry, ...) — report those to the
  respective vendors

## Supported versions

Only the current `main` branch is supported; there are no maintained
release branches.

## Automated scanning

CodeQL, [gitleaks](.gitleaks.toml), and Trivy (filesystem + image) already
run on every PR/push and weekly — see
[`.github/workflows/`](.github/workflows/). Something they missed, or a
false negative in their config, is still worth reporting.
