/**
 * The live layer (task-4 brief, web spec §3, constitution §5): a browser
 * `WebSocket` port of `smac_cli/live.py`'s `_ReconnectingFeed` machinery --
 * same reconnect discipline (exponential backoff, 1s doubling to a 30s
 * cap, +0-25% jitter so many clients reconnecting at once don't
 * thunder-herd), same "every dial gets a fresh token" contract (the
 * caller-supplied url provider is awaited fresh on every connect
 * attempt, never cached -- `api.wsChannelUrl`/`wsEventsUrl` each do a
 * `recoverWorkspaceSession()` internally before minting the URL, exactly
 * mirroring `SmacApi.ws_channel_url`/`.ws_events_url`'s docstrings).
 *
 * Two differences from the Python client, both deliberate simplifications
 * the task-4 brief calls for:
 *  - No synthetic `{"event": "disconnected"}"/"reconnected"}` control
 *    payloads. The TUI uses those to print system lines mid-feed; the web
 *    UI's reconnect UI need is fully covered by `onGap` (below) -- a
 *    disconnect itself needs no user-visible line, just the eventual
 *    catch-up.
 *  - `onGap` fires after EVERY successful (re)connect, including the very
 *    first one of this call's lifetime -- not just subsequent reconnects
 *    (contrast `smac_cli`'s `_on_reconnected`, which is a no-op on the
 *    first connect because the TUI already loads its own initial history
 *    before ever starting the feed). Here, `connectRoom` can be dialed
 *    the instant a room is entered, racing `workspace.tsx`'s own initial
 *    `refreshHistory()` fetch; firing `onGap` unconditionally on connect
 *    closes that race for free (`refreshUnreads`/`refreshHistory` are
 *    both idempotent GETs, so an extra call right after the store's own
 *    is harmless) rather than trying to detect and special-case "was this
 *    truly the first connect."
 */

import { wsChannelUrl, wsEventsUrl } from "./api";
import type { MentionEvent, MessagePayload } from "./types";

const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30_000;
const JITTER_FRACTION = 0.25;

export type Closeable = { close: () => void };

/**
 * Shared reconnect-loop machinery behind `connectRoom`/`connectBell`.
 * `urlProvider` is called fresh on every (re)connect attempt -- see this
 * module's docstring for why that matters. `onOpen` fires after every
 * successful connect (including the first); `onPayload` fires once per
 * decoded JSON frame.
 *
 * Teardown (`close()`) is synchronous and total: it flips `stopped`
 * BEFORE touching the socket or the pending-reconnect timer, so neither
 * the socket's own `onclose` handler nor an in-flight `urlProvider()`
 * promise can schedule a new reconnect after `close()` returns -- no
 * zombie timers, no dial landing after teardown.
 */
class ReconnectingSocket<TPayload> {
  private stopped = false;
  private socket: WebSocket | null = null;
  private backoff = INITIAL_BACKOFF_MS;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly urlProvider: () => Promise<string>,
    private readonly onPayload: (payload: TPayload) => void,
    private readonly onOpen?: () => void
  ) {}

  start(): void {
    void this.attemptConnect();
  }

  close(): void {
    this.stopped = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const socket = this.socket;
    this.socket = null;
    if (socket !== null) {
      // Detach the handlers first -- some WebSocket implementations
      // (including the test double) invoke `onclose` synchronously from
      // `close()`; `stopped` is already `true` above, so that handler's
      // own check is a belt-and-suspenders second guard, not the only one.
      socket.onopen = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      try {
        socket.close();
      } catch {
        // best-effort -- there is nothing left to do with a socket that
        // refuses to close cleanly during teardown.
      }
    }
  }

  private async attemptConnect(): Promise<void> {
    if (this.stopped) {
      return;
    }
    let url: string;
    try {
      url = await this.urlProvider();
    } catch {
      // A failed token mint/refresh is just another kind of connect
      // failure (mirrors `smac_cli/live.py`'s deliberately broad `except
      // Exception` -- the reconnect loop must never itself give up).
      this.scheduleReconnect();
      return;
    }
    if (this.stopped) {
      return;
    }

    let socket: WebSocket;
    try {
      socket = new WebSocket(url);
    } catch {
      // A malformed/rejected URL (or any other constructor failure) is
      // just another connect failure -- same broad-catch philosophy as
      // the `urlProvider()` rejection above, so a bad URL can never
      // crash the reconnect loop or surface as an unhandled rejection.
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      if (this.stopped) return;
      this.backoff = INITIAL_BACKOFF_MS; // a healthy connect resets the backoff
      this.onOpen?.();
    };

    socket.onmessage = (event: MessageEvent) => {
      if (this.stopped) return;
      try {
        const data = JSON.parse(event.data as string) as TPayload;
        this.onPayload(data);
      } catch {
        // A malformed frame is dropped, not fatal -- the socket stays up.
      }
    };

    socket.onclose = () => {
      if (this.socket === socket) {
        this.socket = null;
      }
      if (this.stopped) return;
      this.scheduleReconnect();
    };

    socket.onerror = () => {
      // A browser WebSocket always follows "error" with "close" -- the
      // reconnect is scheduled from `onclose` above. This handler exists
      // solely so the "error" event is never left unhandled.
    };
  }

  private scheduleReconnect(): void {
    if (this.stopped) {
      return;
    }
    const jitter = Math.random() * this.backoff * JITTER_FRACTION;
    const delay = this.backoff + jitter;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.attemptConnect();
    }, delay);
    this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS);
  }
}

/**
 * The live feed for one channel (`/ws/.../channels/{channelId}`).
 * `onPayload` is called with every broadcast `MessagePayload`; `onGap`
 * fires after every successful (re)connect -- the caller's job (per the
 * task-4 brief) is to re-run `workspace.tsx`'s `refreshUnreads()` +
 * `refreshHistory()` from there, the "catch-up-then-live" discipline
 * (constitution §5) applied to a channel that may have missed messages
 * while its socket was down (or not yet open).
 */
export function connectRoom(
  channelId: string,
  onPayload: (payload: MessagePayload) => void,
  onGap: () => void
): Closeable {
  const socket = new ReconnectingSocket<MessagePayload>(
    () => wsChannelUrl(channelId),
    onPayload,
    onGap
  );
  socket.start();
  return { close: () => socket.close() };
}

/**
 * The caller's private mention-events feed
 * (`/ws/.../members/me/events`) -- delivers every `MentionEvent` to
 * `onEvent`. No gap callback: unlike a channel's message history, a feed
 * of point-in-time bell notifications has nothing to "catch up" on a
 * missed mention that already happened is simply gone -- there is
 * nothing to re-fetch (mirrors `smac_cli/live.py`'s `EventBell`, which reconnects
 * silently for the same reason).
 */
export function connectBell(onEvent: (event: MentionEvent) => void): Closeable {
  const socket = new ReconnectingSocket<MentionEvent>(() => wsEventsUrl(), onEvent);
  socket.start();
  return { close: () => socket.close() };
}
