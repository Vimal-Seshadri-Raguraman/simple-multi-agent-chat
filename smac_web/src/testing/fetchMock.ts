import { vi } from "vitest";

/**
 * A tiny hand-rolled fetch mock (task-2 brief: no `msw` dependency --
 * `api.ts` has exactly one call site that touches `fetch`, so a FIFO
 * queue of canned responses is a few lines cheaper than a service-
 * worker-based mocking library and just as precise for these tests).
 *
 * Usage: `const mock = installFetchMock(); mock.queue({status: 200, body: {...}});`
 * then make the call under test, then inspect `mock.calls` for what was
 * actually sent (method/url/headers/parsed-JSON-body).
 */

export type QueuedResponse = { status: number; body?: unknown } | { networkError: true };

export type RecordedCall = {
  method: string;
  url: string;
  headers: Record<string, string>;
  body: unknown;
};

export type FetchMock = {
  calls: RecordedCall[];
  /** Queue the response the NEXT `fetch()` call should resolve with. */
  queue(response: { status: number; body?: unknown }): void;
  /** Queue a network failure (refused connection/DNS/timeout/offline) --
   * the next `fetch()` call REJECTS instead of resolving, mirroring what
   * `lib/api.ts`'s `send()` maps to `Unreachable`. */
  queueNetworkError(): void;
};

function headersToRecord(init: RequestInit["headers"]): Record<string, string> {
  if (!init) {
    return {};
  }
  if (init instanceof Headers) {
    const record: Record<string, string> = {};
    init.forEach((value, key) => {
      record[key] = value;
    });
    return record;
  }
  return init as Record<string, string>;
}

export function installFetchMock(): FetchMock {
  const responses: QueuedResponse[] = [];
  const calls: RecordedCall[] = [];

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    const headers = headersToRecord(init?.headers);
    let body: unknown;
    if (typeof init?.body === "string") {
      try {
        body = JSON.parse(init.body);
      } catch {
        body = init.body;
      }
    }
    calls.push({ method, url, headers, body });

    const next = responses.shift();
    if (!next) {
      throw new Error(`fetchMock: no queued response left for ${method} ${url}`);
    }
    if ("networkError" in next) {
      throw new TypeError("Failed to fetch");
    }
    const text = next.body === undefined ? "" : JSON.stringify(next.body);
    return new Response(text, {
      status: next.status,
      headers: { "Content-Type": "application/json" },
    });
  });

  vi.stubGlobal("fetch", fetchMock);

  return {
    calls,
    queue(response) {
      responses.push(response);
    },
    queueNetworkError() {
      responses.push({ networkError: true });
    },
  };
}
