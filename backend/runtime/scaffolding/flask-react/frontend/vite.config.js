import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Vite's default base '/': the Docker build copies dist/ to the backend's
  // ./static directory and the _serve_spa catch-all serves every file there
  // by its path (/assets/...), whatever static route the Flask app keeps,
  // and answers any other non-API path with index.html.
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
