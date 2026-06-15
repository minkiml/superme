import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

// Single source of truth: the frontend reads VITE_* from the repo-root .env (shared
// with the backend), not a separate web/frontend/.env. The frontend talks only to /api;
// Vite proxies it to the BFF (incl. WebSocket).
export default defineConfig(({ mode }) => {
  const repoRoot = path.resolve(__dirname, '../..')
  const env = loadEnv(mode, repoRoot, '')
  const port = Number(env.VITE_PORT) || 5173
  const bffTarget = env.VITE_BFF_TARGET || 'http://127.0.0.1:8000'
  return {
    plugins: [react()],
    envDir: repoRoot,
    resolve: {
      // `@/` → src, so feature folders import across areas without ../../ chains.
      alias: { '@': path.resolve(__dirname, 'src') },
    },
    server: {
      port,
      proxy: {
        '/api': { target: bffTarget, changeOrigin: true, ws: true },
      },
    },
  }
})
