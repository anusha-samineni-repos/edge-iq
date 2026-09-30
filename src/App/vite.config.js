import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The console talks to the API over same-origin /api. In production the
// FastAPI app serves the built bundle itself (app.py mounts the SPA catch-all),
// so there is no CORS story at all. In dev we proxy instead of enabling CORS,
// which keeps those two environments behaving identically.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: process.env.VITE_API_BASE || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
