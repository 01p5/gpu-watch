import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API_PATHS = ["/healthz", "/nodes", "/fleet", "/audit"];

export default defineConfig({
  plugins: [react()],
  build: { outDir: "../static/dist", emptyOutDir: true, sourcemap: false },
  server: {
    port: 5175,
    proxy: Object.fromEntries(API_PATHS.map((p) => [p, "http://127.0.0.1:8780"])),
  },
});
