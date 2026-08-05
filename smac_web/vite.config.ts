/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-only proxy so `npm run dev` can run against a real, locally running
// SMAC server (`smac-server`, default 127.0.0.1:8000) without CORS or a
// second origin. app/webui.py derives its own passthrough set from the
// live FastAPI route table (so it can never silently drift), but this
// config is static and evaluated by Node with no access to that table --
// keep it in sync by hand with app/main.py's routers if a new top-level
// API prefix is ever added (server/tests/scripts/dev/README all reference
// the same set; grep for API_PREFIXES).
const API_PROXY_TARGET = "http://127.0.0.1:8000";
const API_PREFIXES = [
  "/accounts",
  "/workspaces",
  "/members",
  "/member",
  "/mentions",
  "/auth",
  "/meta",
  "/health",
  "/docs",
  "/redoc",
  "/openapi.json",
];

// https://vitejs.dev/config/
export default defineConfig(({ command }) => ({
  plugins: [react()],
  // The committed bundle is served under /webui/* (app/webui.py mounts
  // StaticFiles there) while index.html itself is served at "/" and any
  // client route -- so a *build* must emit asset URLs rooted at /webui/,
  // but the dev server should keep serving at "/" for a normal localhost
  // workflow.
  base: command === "build" ? "/webui/" : "/",
  server: {
    proxy: {
      ...Object.fromEntries(
        API_PREFIXES.map((prefix) => [
          prefix,
          { target: API_PROXY_TARGET, changeOrigin: true },
        ])
      ),
      "/ws": { target: API_PROXY_TARGET, ws: true, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/setupTests.ts"],
    globals: true,
  },
}));
