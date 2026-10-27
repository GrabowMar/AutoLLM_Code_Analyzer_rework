import { createApp } from "vue";
import { createRouter, createWebHistory } from "vue-router";
import App from "./App.vue";
import "./style.css";

// Router contract:
// - App.vue is always the root component, and a router is always installed,
//   so App.vue (and any component) can use useRoute(), useRouter(),
//   <RouterLink> and <RouterView>.
// - If src/routes.js exists, its default export (an array of route records)
//   is the route table and App.vue renders the matched page with
//   <RouterView />.
// - Otherwise the conventional router module is used when there is one:
//   src/router/index.js or src/router.js (or .ts), whose default export is
//   either a router made with createRouter() or a route array (a named
//   `routes` export works too).
// - Without any of these every URL matches one catch-all route that renders
//   nothing, so a single App.vue renders its own content (it may switch on
//   useRoute().path and navigate with router.push). A <RouterView /> there
//   renders nothing.
// The glob is empty when the files are absent, so the build never fails on it.
const ROUTE_MODULES = [
  "./routes.js",
  "./routes.ts",
  "./router/index.js",
  "./router/index.ts",
  "./router.js",
  "./router.ts",
];
const modules = import.meta.glob(
  ["./routes.js", "./routes.ts", "./router/index.js", "./router/index.ts", "./router.js", "./router.ts"],
  { eager: true },
);

function customRouter() {
  for (const path of ROUTE_MODULES) {
    const mod = modules[path];
    if (!mod) continue;
    const exported = mod.default ?? mod.router ?? mod.routes;
    if (exported && typeof exported.install === "function") return exported;
    if (Array.isArray(exported)) return createRouter({ history: createWebHistory(), routes: exported });
  }
  return null;
}

const router =
  customRouter() ??
  createRouter({
    history: createWebHistory(),
    routes: [{ path: "/:pathMatch(.*)*", component: { render: () => null } }],
  });

createApp(App).use(router).mount("#app");
