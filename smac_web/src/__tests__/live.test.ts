import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../lib/api";
import { connectBell, connectRoom } from "../lib/live";
import type { MentionEvent, MessagePayload } from "../lib/api";

// live.ts's only external dependency is `api.wsChannelUrl`/`wsEventsUrl` --
// mocking the whole module (same convention as auth-flows.test.tsx) lets
// each test control exactly what URL a given (re)connect attempt sees.
vi.mock("../lib/api");

/**
 * A scripted `WebSocket` double (task-4 brief: "scripted mock WebSocket
 * class (vi.stubGlobal) driving open/message/close/error"). Every
 * `new WebSocket(url)` call `live.ts` makes registers a new instance here
 * so a test can reach in and drive its lifecycle by hand -- there is no
 * real networking, no real timers (see `vi.useFakeTimers()` below).
 */
class MockSocket {
  static instances: MockSocket[] = [];

  url: string;
  closeCalls = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockSocket.instances.push(this);
  }

  /** What a real browser calling `.close()` looks like from the
   * outside: `onclose` fires (possibly synchronously, as here -- the
   * strongest test of `ReconnectingSocket.close()`'s teardown order). */
  close(): void {
    this.closeCalls++;
    this.onclose?.();
  }

  // -- test-only script hooks, not part of the real WebSocket surface --
  open(): void {
    this.onopen?.();
  }

  message(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }

  /** The server (or the network) dropped the connection -- distinct
   * from the test calling `.close()` itself, but the same handler. */
  emitClose(): void {
    this.onclose?.();
  }
}

function latestSocket(): MockSocket {
  const socket = MockSocket.instances[MockSocket.instances.length - 1];
  if (!socket) {
    throw new Error("no MockSocket instance yet -- did the test await the connect tick?");
  }
  return socket;
}

beforeEach(() => {
  vi.useFakeTimers();
  MockSocket.instances = [];
  vi.stubGlobal("WebSocket", MockSocket);
  // Deterministic backoff by default: `Math.random() === 0` means the
  // jitter term is always exactly 0, so every delay assertion below can
  // check an exact millisecond boundary without being flaky. The
  // "jitter is actually applied" behavior gets its own test further down
  // with a different stubbed value.
  vi.spyOn(Math, "random").mockReturnValue(0);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const SAMPLE_PAYLOAD = {
  timestamp: "2026-08-05T12:00:00",
  workspace: { workspace_id: "w1", workspace_name: "Acme" },
  Channel: { channel_id: "c1", channel_name: "general" },
  Sender: { member_id: "m1", member_name: "Alice" },
  Message: { message_id: "msg-1", message_text: "hi" },
  mentions: [],
  channel_refs: [],
} satisfies MessagePayload;

describe("connectRoom (task-4 brief: reconnect + gap discipline)", () => {
  it("dials with a fresh URL and fires onGap after the very first successful connect", async () => {
    vi.mocked(api.wsChannelUrl).mockResolvedValue("ws://x/channels/c1?token=t1");
    const onPayload = vi.fn();
    const onGap = vi.fn();

    const conn = connectRoom("c1", onPayload, onGap);
    await vi.advanceTimersByTimeAsync(0);

    expect(api.wsChannelUrl).toHaveBeenCalledWith("c1");
    expect(MockSocket.instances).toHaveLength(1);
    expect(onGap).not.toHaveBeenCalled(); // not yet -- only fires on OPEN

    latestSocket().open();
    expect(onGap).toHaveBeenCalledTimes(1); // fires on the very first connect, not just reconnects

    conn.close();
  });

  it("delivers decoded message payloads via onPayload", async () => {
    vi.mocked(api.wsChannelUrl).mockResolvedValue("ws://x/channels/c1");
    const onPayload = vi.fn();
    const conn = connectRoom("c1", onPayload, vi.fn());
    await vi.advanceTimersByTimeAsync(0);

    latestSocket().open();
    latestSocket().message(SAMPLE_PAYLOAD);

    expect(onPayload).toHaveBeenCalledWith(SAMPLE_PAYLOAD);
    conn.close();
  });

  it("reconnects after a drop with a FRESH url and fires onGap again", async () => {
    vi.mocked(api.wsChannelUrl)
      .mockResolvedValueOnce("ws://x/channels/c1?token=t1")
      .mockResolvedValueOnce("ws://x/channels/c1?token=t2");
    const onGap = vi.fn();
    const conn = connectRoom("c1", vi.fn(), onGap);
    await vi.advanceTimersByTimeAsync(0);

    latestSocket().open();
    expect(onGap).toHaveBeenCalledTimes(1);

    latestSocket().emitClose(); // the connection drops
    await vi.advanceTimersByTimeAsync(999);
    expect(MockSocket.instances).toHaveLength(1); // backoff hasn't elapsed yet

    await vi.advanceTimersByTimeAsync(1); // total 1000ms -- the 1s initial backoff
    expect(MockSocket.instances).toHaveLength(2);
    expect(api.wsChannelUrl).toHaveBeenCalledTimes(2); // every dial re-calls the provider

    latestSocket().open();
    expect(onGap).toHaveBeenCalledTimes(2); // gap discipline on EVERY reconnect

    conn.close();
  });

  it("backs off exponentially (doubling from 1s) up to a 30s cap", async () => {
    vi.mocked(api.wsChannelUrl).mockResolvedValue("ws://x/channels/c1");
    const conn = connectRoom("c1", vi.fn(), vi.fn());
    await vi.advanceTimersByTimeAsync(0);

    // Never opened -- every attempt fails outright, so backoff keeps
    // doubling: 1s, 2s, 4s, 8s, 16s, then capped at 30s from then on.
    const expectedDelaysMs = [1000, 2000, 4000, 8000, 16000, 30000, 30000];
    for (const delay of expectedDelaysMs) {
      const before = MockSocket.instances.length;
      latestSocket().emitClose();

      await vi.advanceTimersByTimeAsync(delay - 1);
      expect(MockSocket.instances).toHaveLength(before); // not yet

      await vi.advanceTimersByTimeAsync(1);
      expect(MockSocket.instances).toHaveLength(before + 1); // dialed right on schedule
    }

    conn.close();
  });

  it("applies jitter on top of the base backoff (not just the bare exponential value)", async () => {
    vi.spyOn(Math, "random").mockReturnValue(1); // max jitter: +25% of the current backoff
    vi.mocked(api.wsChannelUrl).mockResolvedValue("ws://x/channels/c1");
    const conn = connectRoom("c1", vi.fn(), vi.fn());
    await vi.advanceTimersByTimeAsync(0);

    latestSocket().emitClose();
    await vi.advanceTimersByTimeAsync(1249); // 1000ms base + 25% jitter - 1ms
    expect(MockSocket.instances).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(1); // exactly 1250ms
    expect(MockSocket.instances).toHaveLength(2);

    conn.close();
  });

  it("resets the backoff to 1s after a healthy (re)connect", async () => {
    vi.mocked(api.wsChannelUrl).mockResolvedValue("ws://x/channels/c1");
    const conn = connectRoom("c1", vi.fn(), vi.fn());
    await vi.advanceTimersByTimeAsync(0);

    latestSocket().emitClose(); // fail #1 -- next backoff would be 2s if it kept growing
    await vi.advanceTimersByTimeAsync(1000);
    expect(MockSocket.instances).toHaveLength(2);

    latestSocket().open(); // a HEALTHY connect -- resets backoff back to 1s
    latestSocket().emitClose(); // fail again immediately

    await vi.advanceTimersByTimeAsync(999);
    expect(MockSocket.instances).toHaveLength(2); // not yet -- proves it's back to 1s, not 4s
    await vi.advanceTimersByTimeAsync(1);
    expect(MockSocket.instances).toHaveLength(3);

    conn.close();
  });

  it("treats a failed url provider (e.g. a lapsed session) as a connect failure and retries", async () => {
    vi.mocked(api.wsChannelUrl)
      .mockRejectedValueOnce(new Error("session expired"))
      .mockResolvedValueOnce("ws://x/channels/c1?token=fresh");
    const onGap = vi.fn();
    const conn = connectRoom("c1", vi.fn(), onGap);
    await vi.advanceTimersByTimeAsync(0);

    expect(MockSocket.instances).toHaveLength(0); // the first attempt never got a URL to dial

    await vi.advanceTimersByTimeAsync(1000);
    expect(MockSocket.instances).toHaveLength(1);

    latestSocket().open();
    expect(onGap).toHaveBeenCalledTimes(1);

    conn.close();
  });

  it("treats a `new WebSocket(url)` constructor failure (e.g. a malformed URL) as a connect failure and retries", async () => {
    // A URL the constructor itself rejects (rather than the provider
    // failing to produce one at all) -- the guard this exercises lives
    // OUTSIDE the `urlProvider()` try/catch, wrapping `new WebSocket(...)`
    // directly (fix round 1: this failure mode previously had no
    // dedicated test of its own, only incidental coverage via other
    // suites' default mocks).
    let constructCalls = 0;
    const ThrowingThenWorkingSocket = vi.fn().mockImplementation((url: string) => {
      constructCalls++;
      if (constructCalls === 1) {
        throw new DOMException("The URL is invalid", "SyntaxError");
      }
      return new MockSocket(url);
    });
    vi.stubGlobal("WebSocket", ThrowingThenWorkingSocket);

    vi.mocked(api.wsChannelUrl).mockResolvedValue("ws://x/channels/c1");
    const onGap = vi.fn();
    const conn = connectRoom("c1", vi.fn(), onGap);
    await vi.advanceTimersByTimeAsync(0);

    expect(constructCalls).toBe(1);
    expect(MockSocket.instances).toHaveLength(0); // the first attempt's constructor threw

    await vi.advanceTimersByTimeAsync(1000); // the standard 1s initial backoff
    expect(constructCalls).toBe(2);
    expect(MockSocket.instances).toHaveLength(1); // the retry's constructor succeeded

    latestSocket().open();
    expect(onGap).toHaveBeenCalledTimes(1);

    conn.close();
  });

  it("close() cancels a pending reconnect timer -- no zombie dial after teardown", async () => {
    vi.mocked(api.wsChannelUrl).mockResolvedValue("ws://x/channels/c1");
    const conn = connectRoom("c1", vi.fn(), vi.fn());
    await vi.advanceTimersByTimeAsync(0);

    latestSocket().emitClose(); // schedules a reconnect ~1s out
    conn.close(); // torn down (room switch / logout) BEFORE that timer fires

    await vi.advanceTimersByTimeAsync(60_000); // plenty of time for a would-be zombie dial
    expect(MockSocket.instances).toHaveLength(1); // still just the one -- no zombie reconnect
  });

  it("close() while connected closes the underlying socket and never reconnects", async () => {
    vi.mocked(api.wsChannelUrl).mockResolvedValue("ws://x/channels/c1");
    const conn = connectRoom("c1", vi.fn(), vi.fn());
    await vi.advanceTimersByTimeAsync(0);

    const socket = latestSocket();
    socket.open();
    conn.close();

    expect(socket.closeCalls).toBe(1); // the live socket itself was told to close
    await vi.advanceTimersByTimeAsync(60_000);
    expect(MockSocket.instances).toHaveLength(1); // the mock's own onclose->reconnect never fires
  });
});

describe("connectBell (task-4 brief: private mention-events feed)", () => {
  const SAMPLE_EVENT: MentionEvent = {
    event: "mention",
    mention_id: "e1",
    created_at: "2026-08-05T12:00:00",
    mentioned_member_id: "m1",
    message: SAMPLE_PAYLOAD,
  };

  it("delivers mention events and dials a fresh events URL on every (re)connect", async () => {
    vi.mocked(api.wsEventsUrl)
      .mockResolvedValueOnce("ws://x/events?token=t1")
      .mockResolvedValueOnce("ws://x/events?token=t2");
    const onEvent = vi.fn();

    const conn = connectBell(onEvent);
    await vi.advanceTimersByTimeAsync(0);
    expect(api.wsEventsUrl).toHaveBeenCalledTimes(1);

    latestSocket().open();
    latestSocket().message(SAMPLE_EVENT);
    expect(onEvent).toHaveBeenCalledWith(SAMPLE_EVENT);

    latestSocket().emitClose();
    await vi.advanceTimersByTimeAsync(1000);
    expect(api.wsEventsUrl).toHaveBeenCalledTimes(2); // fresh token on the reconnect too

    conn.close();
  });

  it("close() tears the bell down cleanly too -- no zombie timers", async () => {
    vi.mocked(api.wsEventsUrl).mockResolvedValue("ws://x/events");
    const conn = connectBell(vi.fn());
    await vi.advanceTimersByTimeAsync(0);

    latestSocket().emitClose();
    conn.close();

    await vi.advanceTimersByTimeAsync(60_000);
    expect(MockSocket.instances).toHaveLength(1);
  });
});
