# Deployment guide

Two ways to put LLM Lab on a server: the one-shot deploy script behind an existing reverse proxy, or the full production compose stack with Traefik.

## Choosing a path

- **`scripts/deploy.sh`** runs the *local* compose stack on a server and wires it into an existing Caddy reverse proxy. Quickest way to a working instance; apps are reached through the in-app path proxy rather than subdomains.
- **`docker-compose.production.yml`** is the full stack: hardened Django image, Traefik with TLS, nginx for media, and per-app subdomain routing. Use this for anything long-lived or multi-user.

## Path A: the deploy script

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/GrabowMar/AutoLLM_Code_Analyzer_rework/main/scripts/deploy.sh)
```

Handles first-time installs and in-place updates (clone/pull, bootstrap, build, migrate, restart). Interactive by default; for CI set env vars: `DOMAIN` (required), `DEPLOY_DIR`, `REPO_URL`, `BRANCH`, `OPENROUTER_KEY`, `CADDY_CONTAINER` (auto-detected if empty), `NO_PROXY=1` to skip proxy wiring, `CI=1` for non-interactive mode. It reminds you to create a superuser afterwards.

## Path B: production compose

Copy each template in `.envs/.production/` (`.django.example`, `.postgres.example`, `.frontend.example`, `.traefik.example`) to its real name and fill it in. The vars that matter most in `.django`:

- `DJANGO_DOMAIN`, `DJANGO_ALLOWED_HOSTS`, `FRONTEND_PUBLIC_ORIGIN` — your public identity; the frontend's `ORIGIN` (in `.frontend`) must match.
- `DJANGO_SECRET_KEY`, `DJANGO_ADMIN_URL` — generate both; the admin URL doubles as obscurity.
- `MAILGUN_API_KEY` / `MAILGUN_DOMAIN` — production email (verification, resets).
- `DJANGO_AWS_*` — optional S3 media storage.
- `OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=False` — recommended: every user brings their own key ([Models reference](/docs/MODELS_REFERENCE)).
- `APPS_DOMAIN` — see below.

Then:

```bash
docker compose -f docker-compose.production.yml build
docker compose -f docker-compose.production.yml up -d
docker compose -f docker-compose.production.yml run --rm django python manage.py createsuperuser
```

Services: `django` (gunicorn+uvicorn), `postgres` (with a backups volume), `redis`, `celeryworker`, `celerybeat`, `flower`, `traefik` (ports 80/443/5555), `nginx` (serves Django media read-only), `frontend` (built SvelteKit, adapter-node).

## Sample-app subdomain routing

In production each generated app is served at `https://<container>.<APPS_DOMAIN>/` — no host ports, no per-app port limit:

1. Set `APPS_DOMAIN` in `.django` (a dedicated subdomain like `apps.example.com` is recommended so the wildcard stays scoped) and create a DNS wildcard record `*.apps.example.com → your host`.
2. Fill `.traefik` with a DNS-01 provider and credentials (`TRAEFIK_DNS_PROVIDER=cloudflare` + `CF_DNS_API_TOKEN`, or your provider's equivalents). Traefik uses these to issue a single wildcard `*.APPS_DOMAIN` certificate; the file is loaded only by the traefik container so the DNS secret never reaches django.
3. Django writes a per-container route file into a shared volume; Traefik picks it up and routes the subdomain to the app container over the `llm_apps` network.

If a route file goes stale (e.g. after manual container surgery), `python manage.py reconcile_app_routes` rewrites them all; `sync_container_status` reconciles DB state with Docker. Both are worth a cron entry.

## See also

- [Quickstart](/docs/QUICKSTART) — the local equivalent of all this.
- [Troubleshooting](/docs/TROUBLESHOOTING) — including the production-specific failure modes.
