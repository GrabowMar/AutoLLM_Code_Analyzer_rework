# Sample-app routing

Generated sample apps run in their own containers and are reached through the
edge reverse proxy. Each app is served at the **root of its own origin** so that
the SPA's root-absolute URLs (`/assets/...`, `/api/...`) resolve to the app and
not to the main platform. Path routing under the main domain (`/apps/<name>/`)
does **not** work for these SPAs and is deprecated.

## Modes

| Mode | URL | Enabled by | Edge |
|------|-----|-----------|------|
| Traefik subdomain (prod) | `https://<name>.<APPS_DOMAIN>` | `TRAEFIK_DYNAMIC_DIR` | Traefik routes the subdomain straight to the container (wildcard cert via DNS-01). |
| Django subdomain (single-proxy, e.g. Caddy) | `https://<name>.<APPS_DOMAIN>` | `APPS_SUBDOMAIN_PROXY=True` | A wildcard `*.<APPS_DOMAIN>` edge route → Django; `AppSubdomainProxyMiddleware` proxies to the app container. |
| Path (deprecated) | `https://<origin>/apps/<name>/` | `APPS_PROXY_PATH=True` | Django path proxy. Breaks SPAs with root-absolute assets/API. |

`resolve_app_url` (runtime API) emits the subdomain URL while the container is
running; the frontend "Access app" link uses it unchanged.

## dev1.grabowmar.ovh deploy (Caddy + `APPS_SUBDOMAIN_PROXY`)

The live deploy reuses the **existing `*.grabowmar.ovh` wildcard DNS** instead of
adding a new `*.dev1.grabowmar.ovh` record. So apps are reached at
**`https://<name>.grabowmar.ovh/`** (apex wildcard), NOT `<name>.dev1.…`. A
`*.dev1.grabowmar.ovh` host would NXDOMAIN — a DNS wildcard matches exactly one
label, so `*.grabowmar.ovh` does **not** cover `<name>.dev1.grabowmar.ovh`.

`APPS_DOMAIN=grabowmar.ovh` and `APPS_SUBDOMAIN_PROXY=True` in
`.envs/.local/.django`. Django is published on host port **8001**.
`AppSubdomainProxyMiddleware` only routes hosts that start with `llm-`
(`APP_NAME_PREFIX`), so the broad `*.grabowmar.ovh` wildcard can't hijack
`dev1`/`cloud`/`www` — those fall through to normal handling (and `dev1`/`cloud`
have their own explicit Caddy blocks anyway).

### Caddy config (host-network Caddy, single-file Caddyfile bind mount)
```caddy
# Apps: <name>.grabowmar.ovh → Django:8001 (middleware routes llm-* by subdomain).
# dev1/cloud have more-specific blocks, so Caddy matches those first.
https://*.grabowmar.ovh {
  tls /etc/letsencrypt/selfsigned-grabowmar/fullchain.pem /etc/letsencrypt/selfsigned-grabowmar/privkey.pem
  reverse_proxy 127.0.0.1:8001
}

https://dev1.grabowmar.ovh, https://dev1.grabowmar.ovh:8443 {
  tls /etc/letsencrypt/live/dev1.grabowmar.ovh/fullchain.pem /etc/letsencrypt/live/dev1.grabowmar.ovh/privkey.pem
  reverse_proxy 127.0.0.1:8000       # frontend (unchanged)
}
```

The `tls` cert is currently a **self-signed `*.grabowmar.ovh`** (CN matches), so
browsers show a warning. For trusted access, issue a real `*.grabowmar.ovh` cert
via DNS-01 (HTTP-01/webroot can't do wildcards) and point `tls` at it:

```sh
certbot certonly --dns-<provider> -d 'grabowmar.ovh' -d '*.grabowmar.ovh'
```

**Caddyfile is a single-file bind mount** — editing it creates a new inode, so
Caddy must be **restarted** (`docker restart llm-lab-dev1-https-proxy`), a reload
won't pick it up.

Verified e2e: `https://llm-68259302.grabowmar.ovh/` → 200 HTML, its
`/static/assets/*.js` → 200 `application/javascript`, `/api/health` → app JSON;
`dev1`/`cloud` unaffected.

## Why subdomains (not path prefix)
Generated SPAs reference assets and API at the domain root (`/assets/index.js`,
`fetch('/api/...')`). Under `/apps/<name>/` those resolve to the **main**
platform (whose `/assets` and `/api` differ), so the app renders blank. A
separate origin per app makes every app work unmodified — and isolates the
(untrusted) app from the main app's session.
