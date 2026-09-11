import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const FRONT_PORT = Number(process.env.AGENT_FRONT_PORT ?? 3040);
const BACKEND_URL = process.env.AGENT_BACKEND_URL ?? 'http://localhost:3041';

export default defineConfig({
  plugins: [react()],
  server: {
    port: FRONT_PORT,
    host: true,
    proxy: {
      // ws:true also carries the SSE stream and any future WebSocket through untouched.
      '/api': { target: BACKEND_URL, changeOrigin: true, ws: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
});
