import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "../App";
import * as api from "../lib/api";
import * as live from "../lib/live";
import type { Closeable } from "../lib/live";
import type { MentionEvent, MessagePayload, Session } from "../lib/api";
import { Unreachable } from "../lib/errors";
import { setViewportWidth } from "../testing/viewportMock";
import { CLIENT_VERSION } from "../version";

/**
 * The AuthedShell-level wiring this task adds on top of `live.ts`
 * (unit-tested in isolation in `live.test.ts`) and `Toast.tsx`: the
 * current room's socket opens/closes as the reader switches channels,
 * a live payload lands in the feed, `onGap` re-runs the store's own
 * catch-up fetches, and a bell mention either stays silent (in-room,
 * already visible) or becomes a toast that routes to the right room on
 * click (task-4 brief's "toast routing test"). `../lib/live` is mocked
 * entirely -- there is no real WebSocket here, just captured callbacks
 * this file drives by hand, the same shape of test `auth-flows.test.tsx`
 * already uses for reaching the authed shell via a mocked `../lib/api`.
 */
vi.mock("../lib/api");
vi.mock("../lib/live");

const WORKSPACE_SESSION: Session = {
  url: "http://localhost",
  email: "alice@example.com",
  accountAccess: "aat",
  accountRefresh: "art",
  workspaceId: "ws1",
  workspaceAccess: "wat",
  workspaceRefresh: "wrt",
};

function samplePayload(
  channelId: string,
  channelName: string,
  messageId: string,
  text = "hello from the socket"
): MessagePayload {
  return {
    timestamp: "2026-08-05T12:00:00",
    workspace: { workspace_id: "ws1", workspace_name: "Acme" },
    Channel: { channel_id: channelId, channel_name: channelName },
    Sender: { member_id: "m2", member_name: "Bob Agent" },
    Message: { message_id: messageId, message_text: text },
    mentions: [],
    channel_refs: [],
  };
}

function sampleMentionEvent(channelId: string, channelName: string): MentionEvent {
  return {
    event: "mention",
    mention_id: "e1",
    created_at: "2026-08-05T12:00:00",
    mentioned_member_id: "m1",
    message: samplePayload(channelId, channelName, "msg-mention", "hey @alice"),
  };
}

let capturedOnPayload: ((payload: MessagePayload) => void) | null;
let capturedOnGap: (() => void) | null;
let capturedOnEvent: ((event: MentionEvent) => void) | null;
// Captured the same way as the live.ts callbacks above -- via the mock's own
// `mockImplementation`, not by indexing into `.mock.calls` after the fact.
// Under vitest 3, `@testing-library/react`'s auto-registered `afterEach`
// cleanup (which unmounts AuthProvider and so fires its effect cleanup,
// `api.setSessionInvalidatedHandler(null)`) now runs AFTER this file's own
// `afterEach(() => vi.clearAllMocks())` -- afterEach hooks run in reverse
// registration order, and RTL's cleanup hook is registered first (at
// import time, before this file's own afterEach below). That leaves a
// stray `null` call sitting in `setSessionInvalidatedHandler`'s `.mock.calls`
// *before* the next test's own registration, so reading `.mock.calls[0]`
// (as this test used to) picks up the previous test's leftover `null`
// instead of this test's real handler. Capturing via a closure reset in
// `beforeEach`, like every other callback in this file, always reflects
// the latest registration and is immune to that cross-test ordering.
let capturedInvalidatedHandler: ((message: string) => void) | null;
let closeRoom: ReturnType<typeof vi.fn>;
let closeBell: ReturnType<typeof vi.fn>;

beforeEach(() => {
  capturedOnPayload = null;
  capturedOnGap = null;
  capturedOnEvent = null;
  capturedInvalidatedHandler = null;
  closeRoom = vi.fn();
  closeBell = vi.fn();

  vi.mocked(live.connectRoom).mockImplementation((_channelId, onPayload, onGap): Closeable => {
    capturedOnPayload = onPayload;
    capturedOnGap = onGap;
    return { close: closeRoom };
  });
  vi.mocked(live.connectBell).mockImplementation((onEvent): Closeable => {
    capturedOnEvent = onEvent;
    return { close: closeBell };
  });
  vi.mocked(api.setSessionInvalidatedHandler).mockImplementation((handler) => {
    capturedInvalidatedHandler = handler;
  });

  vi.mocked(api.getSession).mockReturnValue(WORKSPACE_SESSION);
  vi.mocked(api.meta).mockResolvedValue({ server_version: CLIENT_VERSION, api_version: 1 });
  vi.mocked(api.channels).mockResolvedValue([
    { channel_id: "c1", channel_name: "general" },
    { channel_id: "c2", channel_name: "reports" },
  ]);
  vi.mocked(api.unreads).mockResolvedValue({ unreads: [] });
  vi.mocked(api.members).mockResolvedValue([]);
  vi.mocked(api.whoami).mockResolvedValue({
    member_id: "m1",
    member_name: "Alice Human",
    member_type: "human",
    handle: "alice",
    workspace_id: "ws1",
    account_id: "acc-1",
    created_at: "2026-01-01T00:00:00",
    first_name: null,
    last_name: null,
    company: null,
    occupation: null,
    job_role: null,
    role: "member",
    capabilities: [],
    workspace_visibility: null,
  });
  vi.mocked(api.accountMe).mockResolvedValue({
    account_id: "acc-1",
    email: "alice@example.com",
    created_at: "2026-01-01T00:00:00",
    memberships: [{ workspace_id: "ws1", workspace_name: "Acme", member_id: "m1", handle: "alice" }],
  });
  vi.mocked(api.messages).mockResolvedValue([]);
  // Feed's onView (mark-read) seam fires the moment the feed mounts at
  // the bottom -- give it a real-shaped resolution so the workspace
  // store's UNREAD_ROW reducer case never sees an undefined `row`.
  vi.mocked(api.markRead).mockResolvedValue({
    channel_id: "c1",
    channel_name: "general",
    unread_count: 0,
    first_unread_message_id: null,
    mention_count: 0,
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

// "#general"/"#reports" appear TWICE once the shell is fully mounted (the
// rail's channel-list entry AND the room's `<h2>` title) -- every
// assertion below targets one or the other by role rather than by text,
// to stay unambiguous.
async function goToAuthedShell() {
  render(<App />);
  await screen.findByRole("heading", { name: "#general" }); // the first channel auto-selects
  // The room socket effect's dependency (`currentChannelId`) lands in the
  // same render pass as the heading above, but its passive effect can
  // still flush a tick after `findByRole` resolves -- wait for it
  // explicitly so every test starts from "the socket is already open"
  // rather than racing the effect.
  await waitFor(() => expect(live.connectRoom).toHaveBeenCalled());
}

describe("AuthedShell's room socket wiring", () => {
  it("opens connectRoom for the current channel, and reconnects (closing the old one) on a channel switch", async () => {
    await goToAuthedShell();
    expect(live.connectRoom).toHaveBeenCalledTimes(1);
    expect(vi.mocked(live.connectRoom).mock.calls[0][0]).toBe("c1");

    fireEvent.click(screen.getByRole("button", { name: "#reports" }));
    await screen.findByRole("heading", { name: "#reports" }); // room title updates once the switch lands

    expect(closeRoom).toHaveBeenCalledTimes(1); // c1's socket was closed, not left dangling
    expect(live.connectRoom).toHaveBeenCalledTimes(2);
    expect(vi.mocked(live.connectRoom).mock.calls[1][0]).toBe("c2");
  });

  it("appends a live payload into the feed via the workspace store's appendMessage seam", async () => {
    await goToAuthedShell();
    expect(capturedOnPayload).not.toBeNull();

    act(() => {
      capturedOnPayload?.(samplePayload("c1", "general", "live-1", "a message straight off the socket"));
    });

    await screen.findByText("a message straight off the socket");
  });

  it("onGap re-runs the catch-up-then-live fetches (refreshUnreads + refreshHistory)", async () => {
    await goToAuthedShell();
    const unreadsCallsBefore = vi.mocked(api.unreads).mock.calls.length;
    const messagesCallsBefore = vi.mocked(api.messages).mock.calls.length;

    expect(capturedOnGap).not.toBeNull();
    act(() => {
      capturedOnGap?.();
    });

    await screen.findByRole("heading", { name: "#general" }); // let the re-fetch effects settle
    expect(vi.mocked(api.unreads).mock.calls.length).toBeGreaterThan(unreadsCallsBefore);
    expect(vi.mocked(api.messages).mock.calls.length).toBeGreaterThan(messagesCallsBefore);
  });

  // SMAC-92 Task 4: whoami refreshes on the SAME seam unreads/history
  // already do -- a role change made elsewhere takes effect the next
  // time this client catches up from a reconnect gap.
  it("onGap also re-runs refreshWhoami", async () => {
    await goToAuthedShell();
    const whoamiCallsBefore = vi.mocked(api.whoami).mock.calls.length;

    act(() => {
      capturedOnGap?.();
    });

    await waitFor(() =>
      expect(vi.mocked(api.whoami).mock.calls.length).toBeGreaterThan(whoamiCallsBefore)
    );
  });
});

describe("AuthedShell's bell wiring (task-4 brief: toast routing test)", () => {
  it("an other-room mention shows a toast; clicking it routes to that room", async () => {
    await goToAuthedShell(); // lands on c1 ("general")
    expect(capturedOnEvent).not.toBeNull();

    act(() => {
      capturedOnEvent?.(sampleMentionEvent("c2", "reports"));
    });

    const toast = await screen.findByText("🔔 New mention in #reports");
    fireEvent.click(toast);

    await screen.findByRole("heading", { name: "#reports" }); // routed to the mentioned room
    expect(screen.queryByText("🔔 New mention in #reports")).not.toBeInTheDocument(); // dismissed on click
  });

  it("bumps the unread/badge state on an other-room mention (refreshUnreads)", async () => {
    await goToAuthedShell();
    const unreadsCallsBefore = vi.mocked(api.unreads).mock.calls.length;

    act(() => {
      capturedOnEvent?.(sampleMentionEvent("c2", "reports"));
    });
    await screen.findByText("🔔 New mention in #reports");

    expect(vi.mocked(api.unreads).mock.calls.length).toBeGreaterThan(unreadsCallsBefore);
  });

  it("an IN-room mention (same channel as the one open) shows no toast", async () => {
    await goToAuthedShell(); // current channel is c1 ("general")

    act(() => {
      capturedOnEvent?.(sampleMentionEvent("c1", "general"));
    });

    // Give any (incorrect) toast a chance to render before asserting its absence.
    await screen.findByRole("heading", { name: "#general" });
    expect(screen.queryByText(/New mention in/)).not.toBeInTheDocument();
  });
});

describe("AuthedShell's workspace loading/error states (final review Finding 2b, IMPORTANT)", () => {
  // Before this fix, `state/workspace.tsx` dispatched `LOAD_START`/
  // `LOAD_ERROR` into a void -- nothing rendered `workspace.loading` or
  // `workspace.error`, so a non-`SessionExpired` initial-load failure
  // (server unreachable, dev-server DB reset, ...) left the reader
  // staring at a silent, empty shell.
  it("shows a loading message while the initial channels/unreads/members/self fetch is in flight", async () => {
    let resolveChannels!: (value: { channel_id: string; channel_name: string }[]) => void;
    vi.mocked(api.channels).mockReturnValue(
      new Promise((resolve) => {
        resolveChannels = resolve;
      })
    );

    render(<App />);

    await screen.findByText("Loading workspace…");

    resolveChannels([{ channel_id: "c1", channel_name: "general" }]);
    await screen.findByRole("heading", { name: "#general" }); // resolves into the normal shell
  });

  it("renders workspace.error (e.g. an unreachable server) instead of a silent empty shell", async () => {
    vi.mocked(api.channels).mockRejectedValue(new Unreachable("http://localhost"));

    render(<App />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("SMAC server is not reachable at http://localhost");
  });
});

describe("Session-expiry recovery (final review Finding 2, IMPORTANT)", () => {
  // Reproduces the review's failure scenario at the plumbing boundary this
  // task adds: a real expired refresh chain (exhaustively covered against
  // real HTTP semantics in `api.test.ts`'s own "session-invalidated
  // handler" describe block) ends by calling whatever `AuthProvider`
  // registered via `api.setSessionInvalidatedHandler` -- since `../lib/api`
  // is mocked wholesale in this file (like every other test here), that
  // registration call is captured directly and invoked by hand to drive
  // the exact same signal a real failed refresh chain would raise.
  it("closes both sockets and lands on the login screen with the session-expired notice", async () => {
    await goToAuthedShell();
    expect(closeRoom).not.toHaveBeenCalled();
    expect(closeBell).not.toHaveBeenCalled();

    expect(capturedInvalidatedHandler).toBeTypeOf("function");

    act(() => {
      capturedInvalidatedHandler?.("Session expired — please log in again.");
    });

    // Leaving "authed" unmounts `AuthedShell` (and the `WorkspaceProvider`/
    // room+bell sockets it owns) -- there is no separate "close sockets"
    // call to wire up, React's own unmount runs each socket effect's
    // cleanup.
    await screen.findByRole("heading", { name: "Log in" });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Session expired — please log in again."
    );
    expect(closeRoom).toHaveBeenCalledTimes(1);
    expect(closeBell).toHaveBeenCalledTimes(1);
  });
});

describe("AuthedShell's window-focus unreads refetch (fix round 1: web spec §2's rail bullet)", () => {
  it("refetches unreads when the window regains focus", async () => {
    await goToAuthedShell();
    const unreadsCallsBefore = vi.mocked(api.unreads).mock.calls.length;

    act(() => {
      fireEvent(window, new Event("focus"));
    });

    await waitFor(() =>
      expect(vi.mocked(api.unreads).mock.calls.length).toBeGreaterThan(unreadsCallsBefore)
    );
  });

  it("stops listening after the shell unmounts (clean teardown, no leaked listener)", async () => {
    const { unmount } = render(<App />);
    await screen.findByRole("heading", { name: "#general" });
    await waitFor(() => expect(live.connectRoom).toHaveBeenCalled());

    unmount();
    const unreadsCallsAfterUnmount = vi.mocked(api.unreads).mock.calls.length;

    fireEvent(window, new Event("focus"));

    // Nothing to await -- a leaked listener would call refreshUnreads()
    // synchronously off this same dispatch; asserting immediately (no
    // new call landed) is exactly what proves the teardown ran.
    expect(vi.mocked(api.unreads).mock.calls.length).toBe(unreadsCallsAfterUnmount);
  });

  // SMAC-92 Task 4: same seam as unreads (task-4 brief, "verify which
  // handlers exist ... and extend them, don't duplicate listeners").
  it("also refetches whoami when the window regains focus", async () => {
    await goToAuthedShell();
    const whoamiCallsBefore = vi.mocked(api.whoami).mock.calls.length;

    act(() => {
      fireEvent(window, new Event("focus"));
    });

    await waitFor(() =>
      expect(vi.mocked(api.whoami).mock.calls.length).toBeGreaterThan(whoamiCallsBefore)
    );
  });
});

// SMAC-85: Settings (`screens/Settings.tsx`) was previously reachable ONLY
// via Cmd-K palette commands (`/invite`, `/workspace delete`) -- there was
// no clickable entry anywhere in the shell for a mouse-first reader.
// `rail.test.tsx` covers the rail gear/YOU-menu buttons calling
// `onOpenSettings` in isolation; this describe block covers the one thing
// that needs the FULL shell mounted to observe: on mobile, opening Settings
// closes the swipe/tap drawer behind it (mirroring the existing
// `onSelectChannel` mobile-close wiring), so returning via Settings' own
// "Back to the room" button doesn't land back on an unexpectedly-still-open
// drawer.
describe("AuthedShell's Settings entry points (SMAC-85 fix: clickable settings)", () => {
  afterEach(() => {
    act(() => setViewportWidth(1024)); // back to desktop -- avoid bleeding into other tests in this file
  });

  it("desktop: the rail gear button opens Settings", async () => {
    await goToAuthedShell();
    fireEvent.click(screen.getByLabelText("Settings"));
    await screen.findByRole("heading", { name: "Settings" });
  });

  it("desktop: the YOU menu's Settings entry opens Settings", async () => {
    await goToAuthedShell();
    fireEvent.click(screen.getByText("@alice"));
    fireEvent.click(screen.getByRole("menuitem", { name: "Settings" }));
    await screen.findByRole("heading", { name: "Settings" });
  });

  it("mobile: opening Settings via the rail gear closes the drawer behind it", async () => {
    await goToAuthedShell();
    act(() => setViewportWidth(400));

    // Open the mobile drawer via the room's hamburger, same as a reader would.
    fireEvent.click(screen.getByLabelText("Open channels"));
    expect(screen.getByTestId("rail-backdrop")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Settings"));
    await screen.findByRole("heading", { name: "Settings" });

    fireEvent.click(screen.getByRole("button", { name: "Back to the room" }));
    await screen.findByRole("heading", { name: "#general" });

    // If the drawer's `railOpen` state hadn't been reset when Settings
    // opened, it would still read "open" here and show the backdrop again.
    expect(screen.queryByTestId("rail-backdrop")).not.toBeInTheDocument();
  });
});
