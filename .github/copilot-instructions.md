# Copilot Instructions — LLM Eval Lab

## Tech Stack

- **Backend**: Django 6.0 · Python 3.13 · Django Ninja (REST API) · Celery + Redis (task queue) · PostgreSQL
- **Frontend**: SvelteKit 2 · TypeScript · Tailwind CSS 4 · Vite · bits-ui (headless components)
- **Auth**: django-allauth (headless mode, email-only, MFA support)
- **Infra**: Docker Compose (local + production) · uv (Python package manager)

## Commands

### Python (backend)

```sh
# Tests
uv run pytest                                          # all tests
uv run pytest path/to/test.py::TestClass::test_method  # single test
uv run coverage run -m pytest && uv run coverage html  # with coverage

# Linting & formatting
uv run ruff check llm_lab --fix   # lint
uv run ruff format llm_lab        # format
uv run pre-commit run --all-files # all pre-commit hooks

# Type checking
uv run mypy llm_lab

# Django management
uv run python manage.py <command>
```

### Frontend

```sh
cd frontend
npm install       # install deps
npm run dev       # dev server (port 8080)
npm run build     # production build
```

### Docker (via justfile)

```sh
just up       # start all containers
just down     # stop containers
just build    # rebuild images
just logs     # tail logs
just manage <cmd>         # run manage.py in container
just frontend-dev         # start frontend dev server in container
```

## Architecture

### Backend structure

- `config/` — Django project config: settings (`base.py`, `local.py`, `production.py`, `test.py`), root URLconf, ASGI/WSGI, Celery app
- `config/api.py` — Django Ninja API root. Registers routers from Django apps.
- `llm_lab/` — Django apps directory (`APPS_DIR`). Each app lives here.
- `tests/` — Project-level tests

Settings are environment-specific (`config/settings/{base,local,production,test}.py`) and use `django-environ` for env var loading.

### Frontend structure

- `frontend/src/routes/` — SvelteKit file-based routing
  - `/(auth)/` — Login, signup, password reset, email verification, 2FA
  - `/(app)/` — Authenticated app pages (dashboard, models, analysis, etc.)
- Vite proxies `/api`, `/_allauth`, `/admin`, `/media` to the Django backend

### API conventions (Django Ninja)

APIs use function-based views with `ninja.Router`, not class-based viewsets:

```python
# llm_lab/<app>/api/views.py
from ninja import Router
router = Router(tags=["<app>"])

@router.get("/", response=list[MySchema])
def list_items(request):
    ...
```

Schemas use `ninja.ModelSchema` (Pydantic-based). Custom computed fields use `@staticmethod resolve_<field>()`. Routers are registered in `config/api.py`.

Authentication is `SessionAuth` globally. API docs are restricted to staff.

### Testing conventions

- **pytest** with `pytest-django`, configured in `pyproject.toml`
- **factory-boy** for test data — factories live in `<app>/tests/factories.py`
- Shared fixtures in `llm_lab/conftest.py` (e.g., `user` fixture via `UserFactory`)
- Tests use `--reuse-db` and `--import-mode=importlib`
- Test settings: `config.settings.test` (fast password hashing, in-memory email)

### User model

Custom `User` model with email as the login field (no username). Single `name` field instead of first/last name. See `llm_lab/users/models.py`.

## Code Style

- **Imports**: One import per line, enforced by ruff (`force-single-line = true`)
- **Ruff**: Comprehensive rule set (see `pyproject.toml [tool.ruff]`). `S101` (assert) is allowed.
- **Django version target**: 6.0 (enforced by `django-upgrade` pre-commit hook)
- **Type hints**: mypy with `django-stubs`, strict `check_untyped_defs`

## Recommended MCP Servers

The following MCP servers improve AI-assisted development in this project. Configuration lives in `.vscode/mcp.json`.

| Server | Purpose | When to use |
|--------|---------|-------------|
| **Playwright** | Browser automation, E2E testing, visual debugging | Testing SvelteKit UI, verifying auth flows, debugging frontend rendering |
| **PostgreSQL** | Schema inspection, query analysis, data exploration | Exploring models, debugging queries, reviewing migrations |
| **Context7** | Live framework documentation (Django, SvelteKit, Tailwind) | Getting accurate API references for the exact versions used |
| **GitHub** | Repo management, issues, PRs, actions | Already configured in Copilot CLI; use for PR workflows and CI debugging |
