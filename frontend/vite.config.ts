import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiTarget = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env?.RECONCILE_API_URL ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 4173,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
