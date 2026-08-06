import { defineConfig } from "@playwright/test";

/**
 * SMAC web e2e config (SMAC-85 Task 6, web spec §5: "Playwright (chromium)
 * against a real spawned server... marked slow; runs in the branch
 * gates"). Deliberately separate from `vite.config.ts`'s Vitest block --
 * `npm test` (Vitest) stays fast and never touches this file; `npm run e2e`
 * is its own, slower gate.
 *
 * `testDir` scopes Playwright to `e2e/` ONLY -- it never sees
 * `src/__tests__/*.test.tsx` (Vitest's own home), so no explicit ignore
 * pattern is needed for that side of the split. The other half of the
 * split (Vitest not picking up `e2e/*.spec.ts`) lives in `vite.config.ts`'s
 * `test.exclude`, since Vitest's default `include` glob matches `*.spec.*`
 * as well as `*.test.*`.
 *
 * `globalSetup` spawns the real server (see `e2e/global-setup.ts`) on a
 * random free port against a throwaway temp database and returns its own
 * teardown function; each spec reads the resulting base URL from
 * `process.env.SMAC_E2E_BASE_URL` at test-run time rather than from a
 * static `use.baseURL` here, which would be evaluated before
 * `globalSetup` ever runs.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: {
      // Some sandboxes (notably containerized CI/dev environments without
      // the kernel privileges Chromium's own internal sandbox wants)
      // refuse to let Chromium set it up at all, and the browser fails to
      // launch entirely. This is a Playwright-level launch option (not a
      // raw Chromium flag) that disables just that internal sandboxing;
      // acceptable here because the only thing this browser ever talks to
      // is the disposable, freshly-migrated local server this same config
      // just spawned -- there is no untrusted content or network in play.
      chromiumSandbox: false,
    },
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium", viewport: { width: 1280, height: 800 } },
    },
  ],
});
