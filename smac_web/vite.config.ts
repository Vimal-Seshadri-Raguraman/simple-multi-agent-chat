/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev-only proxy so `npm run dev` can run against a real, locally running
// SMAC server (`smac-server`, default 127.0.0.1:8000) without CORS or a
// second origin -- mirrors the serving contract in app/webui.py exactly:
// everything under these prefixes is the API/WS surface, never the SPA.
const API_PROXY_TARGET = "http://127.0.0.1:8000";
const API_PREFIXES = [
  "/accounts",
  "/workspaces",
  "/members",
  "/mentions",
  "/auth",
  "/meta",
  "/docs",
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
