import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: {{frontend_port|8000}},
    proxy: {
      '/api': {
        target: 'http://localhost:{{backend_port|5000}}',
        changeOrigin: true,
      }
    }
  }
})
