import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  // The Docker build copies dist/ to the backend's ./static directory, and
  // the backend serves it under the /static/ URL prefix, so every built
  // asset URL must start with /static/ (not Vite's default /). Client-side
  // routes are unaffected: the backend answers any other non-API path with
  // index.html.
  base: "/static/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 3000,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
