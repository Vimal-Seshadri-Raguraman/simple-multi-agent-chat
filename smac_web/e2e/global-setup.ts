/**
 * Playwright global setup for the SMAC web e2e journey (SMAC-85 Task 6,
 * web spec §5's "E2E: Playwright (chromium) against a real spawned
 * server"). Spawns a REAL `uvicorn app.main:app` -- the same ASGI app
 * `smac-server` runs -- on a randomly-assigned free port, against a
 * throwaway temp-file SQLite database, so this never touches the
 * developer's own managed database at `~/.local/share/smac/smac.db`
 * (mirrors `tests/conftest.py`'s `real_smac_server` fixture's isolation
 * goal, adapted to Playwright's own setup/teardown lifecycle instead of
 * pytest's).
 *
 * Process hygiene: the spawned child's exact PID is captured in this
 * module's closure and killed by that PID alone in the returned teardown
 * -- never a `pgrep`/`pkill` pattern that could match an unrelated
 * process. `app/main.py`'s own FastAPI `lifespan` runs `init_db()`
 * (`alembic upgrade head`) on startup, so the fresh temp database is
 * migrated to the current schema automatically -- no separate migration
 * step needed here.
 *
 * Playwright convention (documented since Playwright 1.15): a
 * `globalSetup` function may return an async function of its own, which
 * Playwright then calls as the teardown once every test has finished --
 * both run in the SAME Node process/module instance, so the spawned
 * child's handle and the temp dir path can simply live in this function's
 * closure instead of being persisted to disk for a separate teardown
 * process to rediscover.
 */

import { type ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import http from "node:http";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..", "..");
const UVICORN_BIN = path.join(REPO_ROOT, ".smac", "bin", "uvicorn");

/** How long to wait for the spawned server's `/meta` to answer before
 * giving up -- generous, since `init_db()`'s `alembic upgrade head` runs
 * on every fresh temp database this setup creates. */
const READY_TIMEOUT_MS = 30_000;
const READY_POLL_INTERVAL_MS = 200;

/** How long `--stop`-equivalent teardown waits for a clean SIGTERM exit
 * before escalating to SIGKILL -- mirrors `smac_cli/server.py`'s own
 * `_STOP_TIMEOUT_S`. */
const STOP_TIMEOUT_MS = 5_000;

/** Grab an ephemeral, currently-free TCP port from the OS -- the Node
 * twin of `tests/conftest.py`'s `_free_port()`, so parallel/CI runs never
 * collide on a hardcoded port. */
async function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close((closeErr) => {
        if (closeErr) {
          reject(closeErr);
          return;
        }
        if (address && typeof address === "object") {
          resolve(address.port);
        } else {
          reject(new Error("failed to allocate a free port"));
        }
      });
    });
  });
}

/** One GET, resolving `true`/`false` on any response/connection error
 * rather than throwing -- the caller polls this in a loop. */
function probe(url: string): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout: 2_000 }, (res) => {
      res.resume(); // drain, don't care about the body
      resolve(res.statusCode !== undefined && res.statusCode < 500);
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function waitUntilReady(
  baseURL: string,
  child: ChildProcessWithoutNullStreams
): Promise<void> {
  const deadline = Date.now() + READY_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`e2e server process exited early with code ${child.exitCode}`);
    }
    if (await probe(`${baseURL}/meta`)) {
      return;
    }
    await new Promise((r) => setTimeout(r, READY_POLL_INTERVAL_MS));
  }
  throw new Error(`e2e server didn't answer /meta within ${READY_TIMEOUT_MS}ms`);
}

async function killAndWait(child: ChildProcessWithoutNullStreams): Promise<void> {
  if (child.exitCode !== null || child.pid === undefined) {
    return; // already exited, or never got a pid to begin with
  }
  const exited = new Promise<void>((resolve) => child.once("exit", () => resolve()));
  child.kill("SIGTERM"); // exact PID only -- see module docstring
  const timedOut = await Promise.race([
    exited.then(() => false),
    new Promise<boolean>((resolve) => setTimeout(() => resolve(true), STOP_TIMEOUT_MS)),
  ]);
  if (timedOut && child.exitCode === null) {
    child.kill("SIGKILL");
    await exited;
  }
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  const port = await freePort();
  const baseURL = `http://127.0.0.1:${port}`;

  // A throwaway temp-file database -- NEVER the developer's own
  // `~/.local/share/smac/smac.db`. Deleted whole (directory and all) in
  // teardown, whether the run passed or failed.
  const dbDir = mkdtempSync(path.join(tmpdir(), "smac-web-e2e-db-"));
  const dbPath = path.join(dbDir, "e2e.db");

  const child = spawn(
    UVICORN_BIN,
    ["app.main:app", "--host", "127.0.0.1", "--port", String(port)],
    {
      cwd: REPO_ROOT,
      env: { ...process.env, DATABASE_URL: `sqlite:///${dbPath}` },
    }
  );

  let serverOutput = "";
  child.stdout.on("data", (chunk: Buffer) => {
    serverOutput += chunk.toString();
  });
  child.stderr.on("data", (chunk: Buffer) => {
    serverOutput += chunk.toString();
  });

  try {
    await waitUntilReady(baseURL, child);
  } catch (err) {
    // Setup itself failed -- Playwright will NOT call a teardown function
    // in this case (none has been returned yet), so this catch block is
    // the only place that can clean up the child/temp dir before
    // re-throwing to fail the whole run loudly.
    await killAndWait(child);
    rmSync(dbDir, { recursive: true, force: true });
    const message = err instanceof Error ? err.message : String(err);
    throw new Error(`${message}\n--- spawned server output ---\n${serverOutput}`);
  }

  // Read by the spec file at TEST-RUN time (after this function has
  // already returned), not by `playwright.config.ts` at config-load time
  // -- config evaluation happens before `globalSetup` runs, so a static
  // `use.baseURL` read from this env var here would always see it unset.
  process.env.SMAC_E2E_BASE_URL = baseURL;

  return async function globalTeardown(): Promise<void> {
    await killAndWait(child);
    rmSync(dbDir, { recursive: true, force: true });
  };
}
