import { request } from "node:http";
import { sveltekit } from "@sveltejs/kit/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, type Plugin } from "vite";

const apiTarget = process.env.API_TARGET ?? "http://localhost:8000";
// Sample-app subdomains (<name>.<APPS_DOMAIN>), served by Django when
// APPS_SUBDOMAIN_PROXY is on. Empty disables the passthrough below.
const appsDomain = (process.env.APPS_DOMAIN ?? "").trim().toLowerCase();

// Generated apps are named llm-<hex> (backend/runtime/middleware.py); only a
// single such label under APPS_DOMAIN is an app host.
function isAppHost(host: string | undefined): boolean {
  if (!appsDomain || !host) return false;
  const name = host.split(":")[0].toLowerCase();
  if (!name.endsWith(`.${appsDomain}`)) return false;
  const label = name.slice(0, -(appsDomain.length + 1));
  return label.startsWith("llm-") && !label.includes(".");
}

// Hand every request for an app subdomain to Django untouched (Host kept, so
// AppSubdomainProxyMiddleware routes it). Registered before Vite's own
// middleware, so the app's /, /assets, /src and /api never reach the platform.
function appSubdomainPassthrough(): Plugin {
  return {
    name: "app-subdomain-passthrough",
    apply: "serve",
    configureServer(server) {
      if (!appsDomain) return;
      const target = new URL(apiTarget);
      server.middlewares.use((req, res, next) => {
        if (!isAppHost(req.headers.host)) return next();
        const upstream = request(
          {
            hostname: target.hostname,
            port: target.port || 80,
            method: req.method,
            path: req.url,
            headers: req.headers,
          },
          (upstreamRes) => {
            res.writeHead(upstreamRes.statusCode ?? 502, upstreamRes.headers);
            upstreamRes.pipe(res);
          },
        );
        upstream.on("error", () => {
          if (!res.headersSent) res.writeHead(502);
          res.end("Bad gateway");
        });
        req.pipe(upstream);
      });
    },
  };
}

export default defineConfig({
  plugins: [appSubdomainPassthrough(), tailwindcss(), sveltekit()],
  ssr: {
    // layerchart ships raw .svelte files; Node can't load them when the
    // package is externalized during dev SSR.
    noExternal: ["layerchart"],
  },
  build: {
    rollupOptions: {
      output: {
        // Reduce chunk count to improve mobile loading reliability.
        // Without this, each Lucide icon and each bits-ui component
        // becomes its own chunk (~56 chunks for the dashboard page).
        manualChunks(id) {
          if (id.includes("@lucide/svelte")) return "vendor-lucide";
          if (id.includes("bits-ui")) return "vendor-bits-ui";
          if (id.includes("mode-watcher") || id.includes("svelte-sonner"))
            return "vendor-ui-utils";
        },
      },
    },
  },
  optimizeDeps: {
    include: [
      "mode-watcher",
      "svelte-sonner",
      "bits-ui",
      "tailwind-variants",
      "tailwind-merge",
      "svelte/reactivity",
      // Lucide icons used throughout the app
      "@lucide/svelte/icons/activity",
      "@lucide/svelte/icons/alert-triangle",
      "@lucide/svelte/icons/app-window",
      "@lucide/svelte/icons/arrow-left",
      "@lucide/svelte/icons/arrow-right",
      "@lucide/svelte/icons/arrow-up-down",
      "@lucide/svelte/icons/ban",
      "@lucide/svelte/icons/bar-chart-3",
      "@lucide/svelte/icons/bot",
      "@lucide/svelte/icons/book-open",
      "@lucide/svelte/icons/box",
      "@lucide/svelte/icons/boxes",
      "@lucide/svelte/icons/brain",
      "@lucide/svelte/icons/chart-column",
      "@lucide/svelte/icons/chart-line",
      "@lucide/svelte/icons/check",
      "@lucide/svelte/icons/check-circle",
      "@lucide/svelte/icons/chevron-down",
      "@lucide/svelte/icons/chevron-right",
      "@lucide/svelte/icons/chevron-left",
      "@lucide/svelte/icons/circle",
      "@lucide/svelte/icons/circle-check",
      "@lucide/svelte/icons/circle-stop",
      "@lucide/svelte/icons/circle-x",
      "@lucide/svelte/icons/clock",
      "@lucide/svelte/icons/cloud-download",
      "@lucide/svelte/icons/code",
      "@lucide/svelte/icons/cookie",
      "@lucide/svelte/icons/cpu",
      "@lucide/svelte/icons/dollar-sign",
      "@lucide/svelte/icons/download",
      "@lucide/svelte/icons/ellipsis",
      "@lucide/svelte/icons/eraser",
      "@lucide/svelte/icons/external-link",
      "@lucide/svelte/icons/history",
      "@lucide/svelte/icons/key",
      "@lucide/svelte/icons/eye",
      "@lucide/svelte/icons/file-code",
      "@lucide/svelte/icons/file-json",
      "@lucide/svelte/icons/file-text",
      "@lucide/svelte/icons/flask-conical",
      "@lucide/svelte/icons/folder-open",
      "@lucide/svelte/icons/gauge",
      "@lucide/svelte/icons/git-compare-arrows",
      "@lucide/svelte/icons/hard-drive",
      "@lucide/svelte/icons/info",
      "@lucide/svelte/icons/key-round",
      "@lucide/svelte/icons/keyboard",
      "@lucide/svelte/icons/laptop-minimal",
      "@lucide/svelte/icons/layers",
      "@lucide/svelte/icons/line-chart",
      "@lucide/svelte/icons/loader-2",
      "@lucide/svelte/icons/loader-circle",
      "@lucide/svelte/icons/log-out",
      "@lucide/svelte/icons/medal",
      "@lucide/svelte/icons/microscope",
      "@lucide/svelte/icons/minus",
      "@lucide/svelte/icons/moon",
      "@lucide/svelte/icons/more-horizontal",
      "@lucide/svelte/icons/octagon-x",
      "@lucide/svelte/icons/panel-left",
      "@lucide/svelte/icons/pause",
      "@lucide/svelte/icons/pie-chart",
      "@lucide/svelte/icons/play",
      "@lucide/svelte/icons/plus",
      "@lucide/svelte/icons/refresh-cw",
      "@lucide/svelte/icons/rocket",
      "@lucide/svelte/icons/rotate-ccw",
      "@lucide/svelte/icons/rotate-cw",
      "@lucide/svelte/icons/save",
      "@lucide/svelte/icons/search",
      "@lucide/svelte/icons/settings",
      "@lucide/svelte/icons/shield",
      "@lucide/svelte/icons/shield-check",
      "@lucide/svelte/icons/sparkles",
      "@lucide/svelte/icons/square",
      "@lucide/svelte/icons/square-terminal",
      "@lucide/svelte/icons/sun",
      "@lucide/svelte/icons/trash-2",
      "@lucide/svelte/icons/trending-up",
      "@lucide/svelte/icons/triangle-alert",
      "@lucide/svelte/icons/trophy",
      "@lucide/svelte/icons/unlock",
      "@lucide/svelte/icons/upload",
      "@lucide/svelte/icons/user",
      "@lucide/svelte/icons/wand",
      "@lucide/svelte/icons/wand-sparkles",
      "@lucide/svelte/icons/wrench",
      "@lucide/svelte/icons/x",
      "@lucide/svelte/icons/zap",
    ],
  },
  server: {
    allowedHosts: process.env.VITE_ALLOWED_HOSTS
      ? [
          ...process.env.VITE_ALLOWED_HOSTS.split(",")
            .map((h) => h.trim())
            .filter(Boolean),
          // Vite checks the Host before any plugin middleware runs.
          ...(appsDomain ? [`.${appsDomain}`] : []),
        ]
      : true,
    hmr: {
      overlay: false,
    },
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/_allauth": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/admin": {
        target: apiTarget,
        changeOrigin: true,
      },
      "/media": {
        target: apiTarget,
        changeOrigin: true,
      },
      // Sample-app reverse proxy (path routing) — forwarded to Django, which
      // proxies on to the app container.
      "/apps": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
});
