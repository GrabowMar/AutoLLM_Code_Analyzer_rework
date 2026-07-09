# Troubleshooting

Symptoms, causes, and fixes for the most common local and production failures.

## "No OpenRouter API key is configured for this account"

Key resolution is per user: your own key from **Settings → API Credentials** wins; otherwise the global `OPENROUTER_API_KEY` applies only while `OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True`. Add a personal key, or set the global one in `.envs/.local/.django` and restart. Details in [Models reference](/docs/MODELS_REFERENCE).

## A run is stuck in "running" after a restart

Interactive generation and analysis execute on threads inside the django container — restarting it kills in-flight runs, which stay `running` in the DB. There is no automatic retry; start a new run. (Automation pipeline runs go through Celery and retry.)

## Analysis runs fail to start

- The django container must reach the host Docker daemon: check that `/var/run/docker.sock` is mounted and that `docker ps` shows the stack's containers alongside an `analyzer-workspace` one once provisioned.
- The first-ever run builds the `backend/analyzer-base:latest` image, which can take minutes or fail on network hiccups — `just logs django` shows the build output.
- Install tools **one at a time** from the Analyzers page; parallel installs can race workspace provisioning.

## Automation schedules never fire

Schedules only fire when something ticks the scheduler: the `celeryworker` **and** `celerybeat` services must be up (or run `just manage runautomationscheduler` as a substitute loop). Manual pipeline triggers still work without them. See [Background services](/docs/BACKGROUND_SERVICES).

## No verification email arrives

Locally, nothing sends real email — everything lands in mailpit at http://localhost:8025. If the link inside the email points at the wrong host, fix `FRONTEND_PUBLIC_ORIGIN` to match the URL you actually open in the browser.

## 403 / CSRF / CORS errors from a non-localhost address

All three of `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, and `CSRF_TRUSTED_ORIGINS` in `.envs/.local/.django` must include the origin you're browsing from. Restart the stack after changing them.

## Bearer token works everywhere except export URLs

By design: `/api/export/*` endpoints authenticate via session cookie only. Download exports from a logged-in browser session — see [API reference](/docs/api-reference).

## Port already in use

The local stack binds 8000 (frontend), 8001 (django), 8025 (mailpit), and 5555 (flower). Stop whatever holds the port or adjust the mapping in `docker-compose.local.yml`.

## Model list is empty

The catalog isn't seeded — it syncs from OpenRouter on demand. Open the Models page and sync (needs a resolvable API key for your user).

## Generation UI offers almost nothing

Generation templates seed automatically after every migrate. If the UI still looks empty, check the django container logs for a "Generation template seeding failed" warning — malformed YAML/JSON under `backend/generation/data/` is caught and logged, not raised, so migrate still succeeds but seeding silently no-ops. Fix the data file and run `just manage seed_generation_templates` to retry. See [Quickstart](/docs/QUICKSTART).

## Production: a generated app's subdomain doesn't resolve

Check, in order: the DNS wildcard `*.APPS_DOMAIN` points at the host; Traefik obtained the wildcard cert (DNS-01 credentials in `.envs/.production/.traefik`); the route file exists — `python manage.py reconcile_app_routes` rewrites them. See [Deployment guide](/docs/deployment-guide).

## Start over

```bash
just prune   # removes containers AND volumes — deletes the database
just bootstrap && just build && just up
```
