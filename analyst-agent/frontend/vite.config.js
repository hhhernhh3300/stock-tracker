import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // In local dev the app calls same-origin /api/... ; proxy that to the
    // FastAPI backend on :8000 so you don't need to set VITE_API_URL.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
