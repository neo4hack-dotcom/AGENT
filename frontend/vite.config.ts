import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const FRONT_PORT = Number(process.env.AGENT_FRONT_PORT ?? 3040);
const BACKEND_URL = process.env.AGENT_BACKEND_URL ?? 'http://localhost:3041';
// This machine only, unless asked. The dev server relays requests to an API that trusts
// this machine, so exposing it on the network deserves to be a decision, not a default.
const FRONT_HOST = process.env.AGENT_FRONT_HOST ?? 'localhost';

export default defineConfig({
  plugins: [react()],
  server: {
    port: FRONT_PORT,
    host: FRONT_HOST,
    proxy: {
      // ws:true also carries the SSE stream and any future WebSocket through untouched.
      // The browser's Host and the client's address go through unchanged (changeOrigin
      // off, X-Forwarded-For on): the API checks both, and a proxy must not make a request
      // look more local than it is.
      '/api': { target: BACKEND_URL, changeOrigin: false, xfwd: true, ws: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
});
