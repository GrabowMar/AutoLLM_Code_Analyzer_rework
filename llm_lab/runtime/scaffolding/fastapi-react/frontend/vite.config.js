import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // The FastAPI backend serves the built frontend under /static (StaticFiles
  // mount), so assets must be referenced from /static/ — not Vite's default /.
  // Without this, index.html points at /assets/* which 404s and renders blank.
  base: '/static/',
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://localhost:8000',
    }
  }
})
