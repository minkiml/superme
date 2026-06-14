import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The frontend talks only to /api; Vite proxies it to the BFF (incl. WebSocket).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
