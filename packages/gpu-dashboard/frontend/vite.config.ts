import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API_PATHS = ["/healthz", "/nodes", "/fleet", "/audit", "/mcp"];

// S2.B2 — base-path support for sub-path serving (e.g. mounted under
// Olympus at /gpu/* via reverse proxy). Set VITE_BASE_PATH=/gpu/ at
// build time to rewrite asset URLs. Dev / standalone build use "/".
const BASE = process.env.VITE_BASE_PATH ?? "/";

export default defineConfig({
  base: BASE,
  plugins: [react()],
  build: { outDir: "../static/dist", emptyOutDir: true, sourcemap: false },
  server: {
    port: 5175,
    proxy: Object.fromEntries(API_PATHS.map((p) => [p, "http://127.0.0.1:8780"])),
  },
});
