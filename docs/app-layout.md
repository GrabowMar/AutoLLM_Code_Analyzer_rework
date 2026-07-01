# Django App Layout

Standard layout used by every app under `backend/`. Smaller apps may collapse
some pieces (e.g. a single `services.py`), but the names below are reserved.

```
backend/<app>/
  __init__.py
  apps.py              # AppConfig
  admin.py             # @admin.register(...) classes (mandatory if the app has models)
  models.py            # Django models — split into models/ package once it crosses ~300 LOC
  api/
    __init__.py
    views.py           # ninja.Router with function-based endpoints
    schema.py          # ninja.ModelSchema / Pydantic schemas
  services/            # business logic (preferred package layout once a single
    __init__.py        #   services.py file would exceed ~200 LOC). Re-export
    <domain>.py …      #   public callables from __init__.py so existing
                       #   `from backend.<app> import services` imports continue
                       #   to work. A single `services.py` is acceptable for
                       #   small apps.
  tasks.py             # Celery tasks (optional)
  tests/
    __init__.py
    factories.py       # factory-boy factories
    test_*.py
  migrations/
```

## Conventions

- **APIs (Django Ninja).** One `Router` per app, registered in `config/api.py`.
  Endpoints are function-based, not class-based viewsets. **Static routes must
  be declared before dynamic ones** (e.g. `/export` before `/{task_id}`),
  otherwise Ninja routes the static path to the `{task_id}` handler. Schemas
  use `ninja.ModelSchema`; computed fields are exposed via
  `@staticmethod resolve_<field>()`.
- **Auto-registration (analysis).** The analysis app discovers analyzer
  plugins by importing every module in `backend/analysis/analyzers/`; new
  analyzers are picked up by simply adding a module that subclasses the base
  analyzer class. Avoid manual registries.
- **Daemon-thread dispatch (analysis & generation).** Long-running work that
  must outlive the request but does not warrant a Celery task is dispatched on
  a `threading.Thread(target=..., daemon=True)`. Daemon threads are explicitly
  required — they let the worker shut down without blocking on background
  work. Use Celery for anything that needs durability / retries.
- **Common helpers.** Cross-app utilities live in `backend/common/` (HTTP
  helpers, pagination, error envelopes, shared schemas). Reach for an existing
  helper before adding ad-hoc utilities to an app.
- **Admin.** Every app that defines models has an `admin.py` registering them
  with conservative `list_display`. Sensitive fields (token hashes, secrets)
  must never be shown — surface masked / metadata fields (e.g. `prefix`,
  `last_used_at`) only.
- **Imports.** One import per line (enforced by ruff's
  `force-single-line`). Prefer absolute imports from `backend.*`.
- **Splitting `services.py`.** When a `services.py` file approaches ~200 LOC,
  convert it to a `services/` package, group cohesive helpers into
  domain-named submodules, and re-export the public surface from the
  package's `__init__.py` so callers do not change.
