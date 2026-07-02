# Copilot Instructions — LLM Eval Lab

## Tech Stack

- **Backend**: Django 6.0 · Python 3.13 · Django Ninja (REST API) · Celery + Redis (task queue) · PostgreSQL
- **Frontend**: SvelteKit 2 · TypeScript · Tailwind CSS 4 · Vite · bits-ui (headless components)
- **Auth**: django-allauth (headless mode, email-only, MFA support)
- **Infra**: Docker Compose (local + production) · uv (Python package manager)

## Commands

### Python (backend)

```sh
# Tests — run inside Docker against the dev Postgres (same command as CI)
just test                                          # all tests
just test path/to/test.py::TestClass::test_method  # single test
docker compose run --rm django sh -c "coverage run -m pytest && coverage html"  # with coverage

# Linting & formatting
uv run ruff check backend --fix   # lint
uv run ruff format backend        # format
uv run pre-commit run --all-files # all pre-commit hooks

# Type checking
uv run mypy backend

# Django management
uv run python manage.py <command>
```

### Frontend

```sh
cd frontend
npm install       # install deps
npm run dev       # dev server (port 8080)
npm run check     # svelte-check (type-check)
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
- `backend/` — Django apps directory (`APPS_DIR`). Apps: `users`, `llm_models`, `credentials`, `generation`, `runtime`, `analysis`, `reports`, `rankings`, `statistics`, `automation`, `realtime`, `tokens`, `export`, `docs`, `system`, `common`, `contrib`
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
# backend/<app>/api/views.py
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
- Shared fixtures in `backend/conftest.py` (e.g., `user` fixture via `UserFactory`)
- Tests use `--reuse-db` and `--import-mode=importlib`
- Test settings: `config.settings.test` (fast password hashing, in-memory email)

### Realtime / SSE

The `realtime` app exposes `GET /api/realtime/stream?channels=<comma-separated>` as a Server-Sent Events endpoint. Backend services publish events via `backend.realtime.events.publish(channel, event_dict)` which writes to Redis pub/sub.

Channel naming convention:
- `generation:<job_id>`
- `analysis:<task_id>`
- `runtime:<container_id>`
- `dashboard` (broadcast)

Frontend subscription via `$lib/api/sse.ts`:

```ts
import { subscribe } from '$lib/api/sse';
const cleanup = subscribe(['generation:42', 'dashboard'], (e) => {
  console.log(e.type, e.data);
});
// on component destroy:
cleanup();
```

### User model

Custom `User` model with email as the login field (no username). Single `name` field instead of first/last name. See `backend/users/models.py`.

## Code Style

- **Imports**: One import per line, enforced by ruff (`force-single-line = true`)
- **Ruff**: Comprehensive rule set (see `pyproject.toml [tool.ruff]`). `S101` (assert) is allowed.
- **Django version target**: 6.0 (enforced by `django-upgrade` pre-commit hook)
- **Type hints**: mypy with `django-stubs`, strict `check_untyped_defs`

## Frontend conventions

### API calls

`$lib/api/client.ts` is a barrel re-export. **New code should import from the domain module directly** (e.g., `$lib/api/generation`). Use `apiFetch` / `allauthFetch` from `$lib/api/core.ts` for custom calls — they handle CSRF tokens automatically. A 401 from `apiFetch` redirects to `/auth/login`.

### Design system

- Colors: background `#0F172A`, primary `#1E293B`, CTA `#22C55E`, text `#F8FAFC`
- Fonts: headings Fira Code, body Fira Sans
- Icons: **SVG only** (Heroicons/Lucide) — no emoji as icons
- All clickable elements must have `cursor-pointer` and visible hover/focus states (150–300ms transition)
- Breakpoints: 375 px, 768 px, 1024 px, 1440 px; respect `prefers-reduced-motion`
- See `frontend/DESIGN_SYSTEM.md` for full spec

## Branching & commits

- Branch names: `<type>/<short-slug>` — e.g. `feat/ranking-export`, `fix/login-csrf`, `chore/upgrade-celery`
- Commits: [Conventional Commits](https://www.conventionalcommits.org/) prefixes (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`)
- Always run `just manage makemigrations --check` before pushing model changes

## Recommended MCP Servers

The following MCP servers improve AI-assisted development in this project. Configuration lives in `.vscode/mcp.json`.

| Server | Purpose | When to use |
|--------|---------|-------------|
| **Playwright** | Browser automation, E2E testing, visual debugging | Testing SvelteKit UI, verifying auth flows, debugging frontend rendering |
| **PostgreSQL** | Schema inspection, query analysis, data exploration | Exploring models, debugging queries, reviewing migrations |
| **Context7** | Live framework documentation (Django, SvelteKit, Tailwind) | Getting accurate API references for the exact versions used |
| **GitHub** | Repo management, issues, PRs, actions | Already configured in Copilot CLI; use for PR workflows and CI debugging |
