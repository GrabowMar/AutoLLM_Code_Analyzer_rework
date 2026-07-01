# Security Policy

## Supported versions

Only the current `main` branch is supported. There are no maintained release
branches.

## Automated scanning

This repo already runs CodeQL, [gitleaks](.gitleaks.toml), and Trivy
(filesystem + container image) on every PR/push and weekly — see
[`.github/workflows/`](.github/workflows/). If you find something these
missed, or a false negative in their config, that's still worth reporting
below.

## Scope

In scope:

- The Django backend (`backend/`, `config/`) and its REST API.
- The SvelteKit frontend (`frontend/`).
- Docker/Compose configuration used to build and run the app
  (`compose/`, `docker-compose.*.yml`).
- `scripts/deploy.sh` and other deployment tooling.

See "Out of scope" below for what isn't.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Instead, report them privately via GitHub's
[Security Advisories](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework/security/advisories/new)
flow, which gives us a private channel to triage and fix the issue before
public disclosure.

Please include:

- A description of the issue and its impact.
- Steps to reproduce (or a proof-of-concept).
- The affected commit / branch.
- Any suggested mitigation.

We will acknowledge reports within a few business days and aim to have a fix
or mitigation in place within 30 days of confirmation, depending on
complexity.

## Out of scope

- Issues that require physical access to a developer's machine.
- Findings purely from automated scanners with no demonstrated impact.
- Reports on third-party services (OpenRouter, Sentry, etc.) — please report
  those to the respective vendors.
