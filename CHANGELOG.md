# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `scripts/deploy.sh` — one-shot install/update script for Docker hosts,
  with optional Caddy/nginx reverse-proxy configuration.

### Changed
- **Breaking:** upgraded PostgreSQL from 17 to 18. The postgres:18 image
  moved its volume mount point from `/var/lib/postgresql/data` to
  `/var/lib/postgresql`, and a v18 server cannot read a v17 data
  directory. Existing deployments must dump before upgrading
  (`docker compose exec postgres backup`), remove the old postgres data
  volume, and `restore <backup-file>` into the fresh cluster.
- Remade the root docs: `README.md` restructured along common
  README-template conventions (about/features/getting started/usage/
  structure/contact), `CONTRIBUTING.md` reorganized as a setup → change →
  check → PR walkthrough, `SECURITY.md` reordered to lead with how to
  report. The VS Code Dev Containers guide (including troubleshooting)
  moved to `docs/dev-containers.md`.
- Renamed the `llm_lab/` Django-apps package to `backend/` for a clearer
  top-level layout (`backend/` + `config/` + `frontend/`). Updated every
  import, `INSTALLED_APPS` entry, migration reference, Docker image/volume
  name, and doc/CI reference accordingly.
- Trimmed `docs/` down to the material that actually describes this
  codebase (`AUTOMATION_WORKFLOWS.md`, `TEMPLATE_SPECIFICATION.md`,
  `sample-app-routing.md`, `app-layout.md`) and rewrote `docs/README.md` as
  an accurate index.
- Fixed `.github/dependabot.yml` directory globs that pointed at
  directories which no longer exist (`compose/local/docs/`,
  `compose/local/node/`, `compose/production/aws/`).

### Removed
- `docs/ARCHITECTURE.md`, `ANALYZER_GUIDE.md`, `ANALYSIS_PIPELINE.md`,
  `BACKGROUND_SERVICES.md`, `GENERATION_PROCESS.md`, `MODELS_REFERENCE.md`,
  `QUICKSTART.md`, `TROUBLESHOOTING.md`, `api-reference.md`,
  `deployment-guide.md`, `development-guide.md`, `TODO.md` — these
  described the pre-rework Flask/microservices architecture ("ThesisAppRework")
  and no longer matched the Django + SvelteKit codebase.
- Committed build artifacts that shouldn't have been tracked: `test.db`,
  `test_inspect.db`, `frontend/svelte-check-output.txt`,
  `frontend/svelte-check-full-output.txt`. Added matching `.gitignore` rules.

## [0.1.0] — 2026-05-21

Initial commit on the fresh `AutoLLM_Code_Analyzer_rework` repository.

Snapshot of the project state at upstream commit `129f9ab` (2026-05-19,
"feat(api): enhance error handling with formatApiError function and update
sync logic"), with the following maintainability and security additions baked
into the initial commit:

### Added
- Frontend pre-commit hooks: `prettier` (Svelte + Tailwind plugins) and
  `eslint` (Svelte + TypeScript), scoped to `frontend/`.
- `gitleaks` pre-commit hook and `.github/workflows/gitleaks.yml` for secret
  scanning on every PR/push and weekly.
- `.github/workflows/codeql.yml` — CodeQL security scanning for Python and
  JavaScript/TypeScript on every PR/push and weekly.
- `.github/workflows/trivy.yml` — Trivy filesystem scan and image scan
  (Django + Nginx production images) on PRs touching compose/deps and
  weekly.
- `CHANGELOG.md` (this file).
- README CodeQL badge.

### Changed
- CI badge URL in `README.md` and security advisory URL in `SECURITY.md` /
  `.github/ISSUE_TEMPLATE/config.yml` updated from the old `GrabowMar/app`
  slug to `GrabowMar/AutoLLM_Code_Analyzer_rework`.
- `SECURITY.md` — removed the "Known prior leaks" section; it referenced
  older commits that no longer exist in this repo's history (this repo
  starts from a clean single-commit base).

### Predecessor
The previous repository, including its full git history, is archived at
[`GrabowMar/AutoLLM_Code_Analyzer_rework_archive`](https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework_archive).
