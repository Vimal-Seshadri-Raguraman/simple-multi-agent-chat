import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "../App";
import * as api from "../lib/api";
import * as live from "../lib/live";
import type { Closeable } from "../lib/live";
import type { MentionEvent, MessagePayload, Session } from "../lib/api";
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
let closeRoom: ReturnType<typeof vi.fn>;
let closeBell: ReturnType<typeof vi.fn>;

beforeEach(() => {
  capturedOnPayload = null;
  capturedOnGap = null;
  capturedOnEvent = null;
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
    is_admin: null,
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
});
