# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
