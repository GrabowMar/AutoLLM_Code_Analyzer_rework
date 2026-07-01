# Contributing

Thanks for your interest in LLM Eval Lab. This guide covers the day-to-day
workflow for working in this repo.

## Setting up

```bash
just up                # start the full stack
just manage migrate    # apply migrations
just manage createsuperuser
```

Install Python tooling outside the container too — `pre-commit` runs locally:

```bash
uv sync
uv run pre-commit install
```

## Branching

- `main` is always deployable; CI must be green before merging.
- Feature work lives on branches named `<type>/<short-slug>`, e.g.
  `feat/ranking-export`, `fix/login-csrf`, `chore/upgrade-celery`,
  `docs/architecture-update`.
- Dependabot owns `dependabot/**` branches — don't push to them.

## Commits

- Use [Conventional Commits](https://www.conventionalcommits.org/) prefixes
  (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`).
- Keep commits focused and self-contained. Unrelated changes belong in
  separate PRs.

## Before you push

```bash
uv run pre-commit run --all-files    # ruff + djlint + django-upgrade
docker compose -f docker-compose.local.yml exec django python -m pytest -q
cd frontend && npm run check
```

CI runs the same checks on every PR — running them locally just saves a
round trip.

## Pull requests

- One PR per logical change.
- Fill in the PR template (it appears automatically).
- Link any related issue with `Closes #N`.
- Screenshots / GIFs for any UI change.
- Don't request review until CI is green.

## Database changes

- Always add a Django migration alongside the model change.
- Migrations must apply cleanly on a fresh DB **and** on the current
  production schema.
- Run `just manage makemigrations --check` before pushing.

## Frontend changes

- Components live under `frontend/src/lib/components/`; routes under
  `frontend/src/routes/`.
- Run `npm run check` to catch Svelte/TS errors before pushing.
- Respect the design tokens in `frontend/DESIGN_SYSTEM.md`.

## Adding a new Django app

1. Create `backend/<name>/` with `apps.py`, `models.py`, `tests/`, and
   (if exposing an API) `api/views.py` defining a `ninja.Router`.
2. Add the app to `LOCAL_APPS` in `config/settings/base.py`.
3. Register its router in `config/api.py`.
4. Add factories under `backend/<name>/tests/factories.py` for tests.

See [`docs/app-layout.md`](docs/app-layout.md) for the full conventions —
standard file layout, API/service-layer patterns, and where shared helpers
live.

## Security issues

Don't open a PR or public issue for a security vulnerability — follow
[SECURITY.md](SECURITY.md) instead.
