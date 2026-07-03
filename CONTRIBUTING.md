# Contributing

Thanks for helping out! This page covers everything you need to get a
change from idea to merged PR.

## 🧰 Set up

```bash
just bootstrap         # local .env files
just up                # full stack in Docker
just manage migrate
just manage createsuperuser
```

Install the git hooks on your machine too — they run the same checks as CI:

```bash
uv sync
uv run pre-commit install
```

## ✏️ Make your change

1. Branch off `main`, named `<type>/<short-slug>` — for example
   `feat/ranking-export`, `fix/login-csrf`, `docs/api-guide`.
   (Dependabot owns `dependabot/**`; don't push there.)
2. Commit using [Conventional Commits](https://www.conventionalcommits.org/)
   prefixes: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`.
3. Keep each commit — and each PR — to one logical change.

## ✅ Check before you push

```bash
uv run pre-commit run --all-files    # lint + format
just test                            # backend tests (pytest in Docker)
cd frontend && npm run check         # svelte-check
```

CI runs the same checks on every PR. Lint, backend tests, and the frontend
build gate merging; mypy and svelte-check are warm-up jobs that report but
don't block (yet), so running everything locally just saves a round trip.

## 📬 Open a PR

- Fill in the PR template and link related issues with `Closes #N`.
- Include screenshots or GIFs for UI changes.
- CI must be green and the branch up to date with `main` before merging.

## 🍳 Recipes

**Database changes** — add a Django migration alongside the model change;
it must apply cleanly on a fresh DB and on the current schema. Verify with
`just manage makemigrations --check`.

**Frontend changes** — components live in `frontend/src/lib/components/`,
routes in `frontend/src/routes/`. Follow the design tokens in
`frontend/DESIGN_SYSTEM.md`.

**New Django app** —

1. Create `backend/<name>/` with `apps.py`, `models.py`, `tests/`, and —
   if it exposes an API — `api/views.py` defining a `ninja.Router`.
2. Add it to `LOCAL_APPS` in `config/settings/base.py`.
3. Register the router in `config/api.py`.
4. Add test factories in `backend/<name>/tests/factories.py`.

Full conventions live in [docs/app-layout.md](docs/app-layout.md).

## 🔒 Security issues

Never open a public issue or PR for a vulnerability — follow
[SECURITY.md](SECURITY.md) instead.
