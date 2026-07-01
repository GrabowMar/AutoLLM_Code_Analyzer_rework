import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  // The FastAPI backend serves the built frontend under /static (StaticFiles
  // mount), so assets must be referenced from /static/ — not Vite's default /.
  // Without this, index.html points at /assets/* which 404s and renders blank.
  base: "/static/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
