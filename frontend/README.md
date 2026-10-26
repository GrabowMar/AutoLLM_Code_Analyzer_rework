# LLM Lab frontend

The web UI for LLM Lab: SvelteKit 2 with Svelte 5 runes, TypeScript, Tailwind CSS 4
and bits-ui (shadcn-svelte) components, built with `@sveltejs/adapter-node`.

## Running it

In the local stack the `frontend` compose service runs `npm run dev` on container
port 8080, published as <http://localhost:8000>. Always open the app there, not
Django's :8001: the frontend forwards `/api/`, `/_allauth/`, `/admin/`, `/media/` and
`/apps/` to Django (`API_TARGET`, `http://django:8000` in compose), so session
cookies and CSRF work on a single origin.

```bash
just up                 # starts the frontend with the rest of the stack
just frontend-install   # npm install inside the container (after package.json changes)
just frontend-dev       # run the dev server in the foreground
just frontend-build     # production build
```

The forwarding is done twice: by Vite's dev-server `proxy` (`vite.config.ts`) and by
the `handle` hook in `src/hooks.server.ts`, which also serves the production
(adapter-node) build. The hook sets `X-Forwarded-Host` / `-Proto` / `-For`, and
strips `content-encoding` / `content-length` from upstream responses because Node's
`fetch` has already decompressed the body.

## Checks

```bash
npm run check   # svelte-kit sync + svelte-check (types)
npm test        # vitest (unit tests next to the code, *.test.ts)
npm run build   # production build
```

CI runs all three on Node 24. `svelte-check` and `vite build` can exceed the frontend
container's 2 GB memory cap; run them on the host (or a host-side copy of
`frontend/`) if the container gets OOM-killed. Formatting is prettier, through the
repo's pre-commit hooks.

## Layout

```text
src/
  routes/(app)/      one directory per feature page (experiments, sample-generator,
                     analyzers, statistics, …); (auth)/ holds the sign-in flows
  routes/{dashboard,generation,login,settings}/   redirects from old URLs
  hooks.server.ts    API/auth/admin/media/apps reverse proxy
  lib/api/           typed API client, one module per backend router (client.ts, core.ts, sse.ts)
  lib/components/    shared components (ui/ = bits-ui primitives) and per-feature folders
  lib/docs/          the in-app docs renderer (marked + Shiki + Mermaid)
  lib/stores/        auth, page title, preferences (Svelte 5 rune stores)
  lib/nav.ts         navigation: sidebar, mobile menu and breadcrumbs
  app.css            design tokens (light "paper" / dark "observatory" themes)
```

What each page does is described in the [user guide](../docs/USER_GUIDE.md). Visual
rules (tokens, typography, charts, component patterns) are in
[DESIGN_SYSTEM.md](DESIGN_SYSTEM.md); follow it for any UI change.
