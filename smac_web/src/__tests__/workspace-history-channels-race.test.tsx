import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as api from "../lib/api";
import type { ChannelOut, MemberOut, MemberSelfOut, MessagePayload } from "../lib/api";
import { WorkspaceProvider, useWorkspace } from "../state/workspace";

/**
 * Final review Finding 3 (MINOR, same bug class as `workspace-unreads-
 * race.test.tsx`'s T6 fix): `HISTORY_DONE` and `CHANNELS` both wholesale-
 * replaced store state with no guard against a fetch that was in flight
 * BEFORE a locally-known change landed resolving AFTER it -- clobbering
 * that change back out. This scripts both interleavings directly against
 * the store, the same way `workspace-unreads-race.test.tsx` does.
 */
vi.mock("../lib/api");

beforeEach(() => {
  // Every `it()` below re-mounts its own `<WorkspaceProvider>` -- clear
  // accumulated call history so a later test's `mockReturnValueOnce`
  // queue lines up with ITS OWN calls, not leftovers from an earlier test
  // in this file (mock implementations set fresh in `mountProbe` each
  // time are untouched by `clearAllMocks`).
  vi.clearAllMocks();
});

const CHANNELS: ChannelOut[] = [{ channel_id: "c1", channel_name: "general" }];
const SELF: MemberSelfOut = {
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
};
const MEMBERS: MemberOut[] = [];

function msg(id: string, text: string, timestamp: string): MessagePayload {
  return {
    timestamp,
    workspace: { workspace_id: "ws1", workspace_name: "Acme" },
    Channel: { channel_id: "c1", channel_name: "general" },
    Sender: { member_id: "m1", member_name: "Alice Human" },
    Message: { message_id: id, message_text: text },
    mentions: [],
    channel_refs: [],
  };
}

/** Same helper `workspace-unreads-race.test.tsx` uses: a promise this
 * test can resolve on demand, to script a precise interleaving. */
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function Probe() {
  const workspace = useWorkspace();
  return (
    <div>
      <ul data-testid="messages">
        {workspace.messages.map((m) => (
          <li key={m.Message.message_id}>{m.Message.message_text}</li>
        ))}
      </ul>
      <ul data-testid="channel-names">
        {workspace.channels.map((c) => (
          <li key={c.channel_id}>{c.channel_name}</li>
        ))}
      </ul>
      <button onClick={() => void workspace.refreshHistory()}>refresh-history</button>
      <button
        onClick={() =>
          workspace.appendMessage("c1", msg("live-1", "live message", "2026-01-01T00:00:05"))
        }
      >
        append-live
      </button>
      <button onClick={() => void workspace.refreshChannels()}>refresh-channels</button>
      <button onClick={() => void workspace.createChannel("standup")}>create-channel</button>
    </div>
  );
}

async function mountProbe(): Promise<void> {
  vi.mocked(api.channels).mockResolvedValue(CHANNELS);
  vi.mocked(api.members).mockResolvedValue(MEMBERS);
  vi.mocked(api.whoami).mockResolvedValue(SELF);
  vi.mocked(api.unreads).mockResolvedValue({ unreads: [] });
  // Base default for every un-intercepted `api.messages()` call (the
  // initial auto-`refreshHistory` on channel auto-select, and the switch-
  // triggered one a `createChannel()` call causes below).
  vi.mocked(api.messages).mockResolvedValue([]);

  render(
    <WorkspaceProvider>
      <Probe />
    </WorkspaceProvider>
  );
  await screen.findByText("general"); // LOAD_DONE landed, first channel auto-selected
  // Let the auto-select's own `refreshHistory` (triggered by the
  // `currentChannelId` effect) settle before a test starts scripting its
  // own interleaving against the SAME `api.messages` mock.
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("workspace store: HISTORY_DONE vs a mid-walk live append", () => {
  it("doesn't drop a message appended while refreshHistory's walk is still in flight", async () => {
    await mountProbe();

    const pending = deferred<MessagePayload[]>();
    vi.mocked(api.messages).mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByText("refresh-history")); // dispatched, awaiting its page

    // A live message lands (socket echo, or another sender) WHILE the walk
    // above is still in flight -- `APPEND_MESSAGE` applies immediately,
    // independent of the pending refresh.
    fireEvent.click(screen.getByText("append-live"));
    await waitFor(() => expect(screen.getByTestId("messages")).toHaveTextContent("live message"));

    // The walk's own server snapshot resolves late -- it was taken BEFORE
    // the live message was posted, so it doesn't include it.
    await act(async () => {
      pending.resolve([msg("hist-1", "history message", "2026-01-01T00:00:01")]);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByTestId("messages")).toHaveTextContent("history message");
    expect(screen.getByTestId("messages")).toHaveTextContent("live message"); // NOT clobbered
  });

  it("a refreshHistory response with no intervening append still commits normally (no phantom duplicates)", async () => {
    await mountProbe();

    vi.mocked(api.messages).mockResolvedValueOnce([
      msg("hist-1", "history message", "2026-01-01T00:00:01"),
    ]);
    fireEvent.click(screen.getByText("refresh-history"));

    await waitFor(() =>
      expect(screen.getByTestId("messages")).toHaveTextContent("history message")
    );
    expect(screen.getByTestId("messages").children).toHaveLength(1);
  });
});

describe("workspace store: CHANNELS vs a just-created channel", () => {
  it("doesn't drop a just-created channel from a refreshChannels response dispatched before the create", async () => {
    await mountProbe();

    const pending = deferred<ChannelOut[]>();
    vi.mocked(api.channels).mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByText("refresh-channels")); // dispatched first, held pending (stale-to-be)

    vi.mocked(api.createChannel).mockResolvedValueOnce({
      channel_id: "c2",
      channel_name: "standup",
    });
    await act(async () => {
      fireEvent.click(screen.getByText("create-channel"));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByTestId("channel-names")).toHaveTextContent("standup"));

    // The stale `refreshChannels` response (from BEFORE the create) lands
    // late, still only knowing about "general".
    await act(async () => {
      pending.resolve(CHANNELS);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByTestId("channel-names")).toHaveTextContent("standup"); // NOT clobbered
    expect(screen.getByTestId("channel-names")).toHaveTextContent("general");
  });
});
