import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// This machine only, unless asked. The dev server relays requests to an API that trusts
// this machine, so exposing it on the network deserves to be a decision, not a default.
const FRONT_HOST = process.env.AGENT_FRONT_HOST ?? 'localhost';

// `vite --mode alt` is the fallback pair (3042 → 3043). A mode rather than inline
// variables in package.json: `AGENT_FRONT_PORT=3042 vite` is not a command cmd.exe runs.
export default defineConfig(({ mode }) => {
  const alt = mode === 'alt';
  const FRONT_PORT = Number(process.env.AGENT_FRONT_PORT ?? (alt ? 3042 : 3040));
  // 127.0.0.1, not localhost: Node may resolve localhost to ::1 first (Windows does), while
  // the API listens on IPv4 loopback only — and the proxy then answers "connection refused".
  const BACKEND_URL = process.env.AGENT_BACKEND_URL ?? (alt ? 'http://127.0.0.1:3043' : 'http://127.0.0.1:3041');
  return {
    plugins: [react()],
    server: {
      port: FRONT_PORT,
      host: FRONT_HOST,
      proxy: {
        // ws:true also carries the SSE stream and any future WebSocket through untouched.
        // The browser's Host and the client's address go through unchanged (changeOrigin
        // off, X-Forwarded-For on): the API checks both, and a proxy must not make a
        // request look more local than it is.
        '/api': { target: BACKEND_URL, changeOrigin: false, xfwd: true, ws: true },
      },
    },
    build: { outDir: 'dist', sourcemap: false },
  };
});
