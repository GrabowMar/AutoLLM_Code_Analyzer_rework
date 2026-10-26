# Deployment guide

Two ways to put LLM Lab on a server: the deploy script that runs the local stack behind an existing reverse proxy, or the production compose stack with Traefik.

## Choosing a path

| | `scripts/deploy.sh` (local stack) | `docker-compose.production.yml` |
| --- | --- | --- |
| Settings | `config.settings.local` (`DEBUG=True`, uvicorn `--reload`, Vite dev server) | `config.settings.production` (gunicorn + uvicorn workers, built SvelteKit) |
| Sandbox daemon | `apps-dind`: apps, agent sandboxes and analyzer workspaces are nested containers | Host Docker socket mounted into django, executor and Celery; no apps-dind |
| Edge | Existing Caddy or nginx proxies to port 8000 | Traefik on 80/443/5555 with Let's Encrypt |
| App URLs | `/apps/<name>/` path proxy, or subdomains through Django | `https://<container>.<APPS_DOMAIN>/` via Traefik |
| Local-model agent runs | Supported | Refused (no route from host-daemon gateways to the executor's LLM proxy) |
| Email | mailpit | Mailgun (django-anymail) when `MAILGUN_API_KEY` is set, otherwise SMTP from `EMAIL_*` |

The deploy script is the quicker and better-isolated path today; the production stack is hardened at the HTTP layer but runs untrusted generated code on the host daemon.

## Path A: the deploy script

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/GrabowMar/AutoLLM_Code_Analyzer_rework/main/scripts/deploy.sh)
```

From an existing clone, run `./scripts/deploy.sh` instead.

Steps: check `git`, `python3`, `curl` and Docker Compose (falls back to `sudo docker`); clone or update the repo; run `scripts/bootstrap.py`; patch `.envs/.local/.django`; build and `up -d`; run `migrate`; wait for django on :8001 and the frontend on :8000; wire the reverse proxy; print a summary.

| Env var | Default | Meaning |
| --- | --- | --- |
| `DOMAIN` | prompted | Public hostname (required in CI mode) |
| `DEPLOY_DIR` | `~/AutoLLM_Code_Analyzer_rework` | Install / update path |
| `REPO_URL` | the GitHub repo | Git remote |
| `BRANCH` | `main` | Branch to track |
| `OPENROUTER_KEY` | empty | Global OpenRouter key; also sets `OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=True` |
| `CADDY_CONTAINER` | auto-detected | Name of a running Caddy container |
| `CADDY_HOST_IP` | Caddy network gateway | Host address Caddy proxies to |
| `NO_PROXY` | `0` | `1` skips reverse-proxy wiring |
| `CI` | empty | `1` or `true`: non-interactive |

It writes `DJANGO_DOMAIN`, `FRONTEND_PUBLIC_ORIGIN=https://<DOMAIN>`, `DJANGO_ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS` and `CSRF_TRUSTED_ORIGINS` into `.envs/.local/.django` and makes both env files mode 600. For the proxy it first looks for a Caddy container with a bind-mounted `/etc/caddy/Caddyfile`, appends `<DOMAIN> { reverse_proxy <host>:8000 }` and reloads Caddy; otherwise it writes `/etc/nginx/sites-available/autollm` (HTTP only; add TLS with certbot).

> [!WARNING]
> An update runs `git reset --hard origin/<BRANCH>` in `DEPLOY_DIR`, discarding local changes there. The local compose file publishes 8000, 8001, 8025 and 5555 on all interfaces, and Docker-published ports bypass host firewalls such as ufw; bind them to `127.0.0.1` with a compose override (`ports: !override`, as `.devcontainer/docker-compose.host.yml` does). Registration is open by default: set `DJANGO_ACCOUNT_ALLOW_REGISTRATION=False` once your accounts exist.

The first sign-up on the empty database becomes the admin (see [Quickstart](/docs/QUICKSTART)); `createsuperuser` also works.

## Path B: production compose

Copy each template in `.envs/.production/` (`.django.example`, `.postgres.example`, `.frontend.example`, `.traefik.example`) to its name without `.example` and fill it in. Values that matter in `.django`:

| Variable | Notes |
| --- | --- |
| `DJANGO_SECRET_KEY` | Required |
| `DJANGO_ADMIN_URL` | Admin path, default `admin/`. Traefik's django router derives its prefix from the same variable, so any value works |
| `SENTRY_DSN` | Optional; Sentry is initialised only when it is set |
| `DJANGO_DOMAIN`, `DJANGO_ALLOWED_HOSTS`, `FRONTEND_PUBLIC_ORIGIN` | Public identity; the frontend's `ORIGIN` (in `.frontend`) must match |
| `DJANGO_CORS_ALLOWED_ORIGINS`, `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated `https://` origins, normally your site; production names carry the `DJANGO_` prefix (the local ones do not) |
| `MAILGUN_API_KEY`, `MAILGUN_DOMAIN`, `MAILGUN_API_URL` | Mailgun through django-anymail when the key is set; domain defaults to `DJANGO_DOMAIN`, use `https://api.eu.mailgun.net/v3` for EU accounts |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL` | SMTP, used when `MAILGUN_API_KEY` is empty; defaults to `localhost:25` without auth |
| `DJANGO_DEFAULT_FROM_EMAIL`, `DJANGO_SERVER_EMAIL` | Sender addresses; default `LLM Eval Lab <noreply@<DJANGO_DOMAIN>>` |
| `DJANGO_SECURE_SSL_REDIRECT` | Template sets `False` (Traefik already redirects HTTP); settings default `True` |
| `LETSENCRYPT_EMAIL` | Read by Traefik; defaults to `admin@<DJANGO_DOMAIN>` |
| `APPS_DOMAIN` | Base domain for app subdomains; defaults to `DJANGO_DOMAIN` |
| `OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK=False` | Recommended: every user brings their own key ([Models reference](/docs/MODELS_REFERENCE)) |
| `REDIS_URL`, `CELERY_FLOWER_USER`, `CELERY_FLOWER_PASSWORD` | As locally |

`docker-compose.production.yml` sets `DJANGO_SETTINGS_MODULE=config.settings.production` on every Django-image service (django, executor, celeryworker, celerybeat, flower); do not override it, since `config/asgi.py`, `manage.py` and the Celery app fall back to local settings, which need `debug_toolbar` (absent from the production image). Media stays on the `production_django_media` volume (no S3 storage). In `.frontend`, keep `PORT=3000`: the image exposes it and Traefik routes to `frontend:3000`.

Then build, migrate (production `/start` does not migrate; the executor waits up to 10 minutes for it) and start:

```bash
docker compose -f docker-compose.production.yml build
docker compose -f docker-compose.production.yml run --rm django python manage.py migrate
docker compose -f docker-compose.production.yml up -d
docker compose -f docker-compose.production.yml run --rm django python manage.py createsuperuser
```

Run `migrate` again after every deploy that adds migrations.

| Service | Role |
| --- | --- |
| `django` | gunicorn with uvicorn workers on :5000; runs `collectstatic` on start |
| `executor` | `/start-executor`: waits for migrations, then `run_executor`; `restart: unless-stopped`; joins the internal `llmproxy` network |
| `postgres` | With a backups volume |
| `redis` | Broker, pub/sub, cache |
| `celeryworker`, `celerybeat`, `flower` | See [Background services](/docs/BACKGROUND_SERVICES) |
| `traefik` | Ports 80 (redirect), 443, 5555 (Flower); routes `/api/`, `/_allauth/`, `/static/` and the `DJANGO_ADMIN_URL` path to django, `/media/` to nginx, the rest to the frontend |
| `nginx` | Serves Django media read-only |
| `frontend` | Built SvelteKit (adapter-node) |

## Sample-app routing

How a browser reaches a generated app depends on which settings are active (`config/settings/base.py`):

| Mode | Enabled by | App URL |
| --- | --- | --- |
| Traefik bridge | `TRAEFIK_DYNAMIC_DIR` + `DOCKER_APPS_NETWORK` (set by the production compose file) | `https://<container>.<APPS_DOMAIN>/` |
| Django subdomain proxy | `APPS_SUBDOMAIN_PROXY=True` + `APPS_DOMAIN` | `https://llm-<hex>.<APPS_DOMAIN>/` |
| Django path proxy | `APPS_PROXY_PATH=True` (set by the local compose file) | `<APPS_PUBLIC_ORIGIN>/apps/<name>/`, login required; only the app's owner or staff (others get 404) |

`APPS_SUBDOMAIN_PROXY` wins over the path proxy. The path proxy needs no DNS work, but most generated SPAs load root-absolute `/assets/...` and call `/api/...`, which then hit the platform instead of the app, so the page renders blank even though the app works. It also serves app JavaScript on the platform's origin. Prefer a subdomain mode for anything beyond quick checks.

### Traefik (production compose)

1. Set `APPS_DOMAIN` in `.django`. A dedicated subdomain such as `apps.example.com` keeps the wildcard scoped. Create DNS `*.apps.example.com` pointing at the host.
2. Fill `.traefik` with a DNS-01 provider and credentials (`TRAEFIK_DNS_PROVIDER=cloudflare` + `CF_DNS_API_TOKEN`, or your provider's equivalents). Traefik refuses to start without `TRAEFIK_DNS_PROVIDER`. The file is loaded only by the traefik container, so the DNS secret never reaches django.
3. Traefik issues one wildcard `*.APPS_DOMAIN` certificate at startup. Django writes a route file per app container into the shared `production_traefik_dynamic` volume; Traefik watches it and routes the subdomain to the container over the `llm_apps` network. Apps publish no host ports.

If a route file goes stale (for example after manual container surgery), `python manage.py reconcile_app_routes` rewrites them all and `sync_container_status` reconciles container rows with Docker. Both are worth a cron entry.

### Django subdomain proxy (any single edge proxy)

The local stack can serve subdomain URLs through one existing edge proxy (Caddy, nginx) in front of the stack:

1. In `.envs/.local/.django` set `APPS_DOMAIN=<base domain>` and `APPS_SUBDOMAIN_PROXY=True`, and add `.<APPS_DOMAIN>` to `DJANGO_ALLOWED_HOSTS`.
2. At the edge, route `*.<APPS_DOMAIN>` to Django's port (8001 in the local stack, not the frontend's 8000) with a wildcard certificate. Wildcards need DNS-01 issuance: use a Caddy build with your DNS provider's module (for example `xcaddy build --with github.com/caddy-dns/ovh`) or a certbot DNS plugin. More specific site blocks (your main domain) keep winning over the wildcard.
3. `AppSubdomainProxyMiddleware` (right after `SecurityMiddleware`, before sessions and CSRF) picks up `llm-*.<APPS_DOMAIN>` requests and reverse-proxies them to the app's port on apps-dind (`APPS_UPSTREAM_HOST=apps-dind`). Other subdomains fall through untouched.

## Local models in production

Relay endpoints and OpenRouter work in both stacks. If the instance sits behind proxies, set `TRUSTED_PROXY_CIDRS` so relay client IPs resolve correctly, and `LLMLAB_PUBLIC_URL` if the relay install commands should point somewhere other than the request host. See [Local models](/docs/LOCAL_MODELS) and [Execution engine](/docs/EXECUTION#production-compose).

## See also

- [Quickstart](/docs/QUICKSTART): the local equivalent.
- [Troubleshooting](/docs/TROUBLESHOOTING): including production failure modes.
