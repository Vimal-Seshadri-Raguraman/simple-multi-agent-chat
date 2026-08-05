import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as api from "../lib/api";
import type { ChannelOut, MemberOut, MemberSelfOut, UnreadsRowOut } from "../lib/api";
import { WorkspaceProvider, useWorkspace } from "../state/workspace";

/**
 * Fix round 1 (review finding): a stale-overwrite race between
 * `refreshUnreads`'s wholesale `UNREADS` dispatch and `markRead`'s
 * scoped, always-fresh `UNREAD_ROW` dispatch (`state/workspace.tsx`).
 * `refreshUnreads` is fired unprompted by the bell handler on ANY
 * other-room mention and can be in flight for a while; the review
 * reproduced (via `npm run e2e`, run repeatedly) a `refreshUnreads()`
 * response landing AFTER a `markRead()` call had already resolved and
 * applied, clobbering the just-marked-read state back to its stale
 * pre-mark-read count.
 *
 * This scripts that EXACT interleaving directly against the store (no
 * DOM/socket layer needed): dispatch a `refreshUnreads()` -> hold its
 * response pending -> `markRead()` lands and resolves first -> THEN the
 * held-back `refreshUnreads()` response finally arrives with the
 * pre-mark-read snapshot -- it must be discarded, not win the race.
 */
vi.mock("../lib/api");

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

const STALE_ROW: UnreadsRowOut = {
  channel_id: "c1",
  channel_name: "general",
  unread_count: 5,
  first_unread_message_id: "msg-1",
  mention_count: 0,
};
const FRESH_ROW: UnreadsRowOut = {
  channel_id: "c1",
  channel_name: "general",
  unread_count: 0,
  first_unread_message_id: null,
  mention_count: 0,
};

/** A promise this test can resolve on demand, from outside -- lets a
 * mocked `api.unreads()` call be held "in flight" for as long as needed
 * to script a precise interleaving against `markRead`. */
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

/** A minimal harness exposing just enough of the store's surface to
 * drive and observe this race -- no Rail/Room/Feed needed. */
function Probe() {
  const workspace = useWorkspace();
  return (
    <div>
      <div data-testid="c1-unread">{workspace.unreads["c1"]?.unread_count ?? "none"}</div>
      <button onClick={() => void workspace.refreshUnreads()}>refresh</button>
      <button onClick={() => void workspace.markRead("c1")}>mark-read</button>
    </div>
  );
}

async function mountProbe(initialUnreads: UnreadsRowOut[]): Promise<void> {
  vi.mocked(api.channels).mockResolvedValue(CHANNELS);
  vi.mocked(api.members).mockResolvedValue(MEMBERS);
  vi.mocked(api.whoami).mockResolvedValue(SELF);
  // `CHANNELS` above makes the store auto-select "c1", which fires its
  // own `refreshHistory()` fetch -- give it a real-shaped (empty) result
  // so that unrelated effect doesn't throw on an unmocked `undefined`.
  vi.mocked(api.messages).mockResolvedValue([]);
  vi.mocked(api.unreads).mockResolvedValueOnce({ unreads: initialUnreads });

  render(
    <WorkspaceProvider>
      <Probe />
    </WorkspaceProvider>
  );
  await screen.findByTestId("c1-unread");
}

describe("workspace store: refreshUnreads vs markRead sequencing", () => {
  it("discards a stale in-flight refreshUnreads response that resolves AFTER a markRead lands", async () => {
    await mountProbe([STALE_ROW]); // c1 starts at 5 unread
    expect(screen.getByTestId("c1-unread")).toHaveTextContent("5");

    // The bell-triggered refresh that will be stale by the time it lands
    // -- held pending until this test says otherwise.
    const staleRefresh = deferred<{ unreads: UnreadsRowOut[] }>();
    vi.mocked(api.unreads).mockReturnValueOnce(staleRefresh.promise);
    fireEvent.click(screen.getByText("refresh"));
    // refreshUnreads() has captured its epoch and is now awaiting the
    // still-pending `api.unreads()` call queued above.

    // markRead lands WHILE the refresh above is still in flight, and
    // must win: it's the fresher, more specific truth.
    vi.mocked(api.markRead).mockResolvedValueOnce(FRESH_ROW);
    fireEvent.click(screen.getByText("mark-read"));
    await waitFor(() => expect(screen.getByTestId("c1-unread")).toHaveTextContent("0"));

    // The stale refresh finally resolves with the PRE-mark-read snapshot
    // (unread_count: 5). Without the epoch guard this overwrites the
    // freshly-marked-read state right back to "5" -- the exact bug the
    // review reproduced.
    await act(async () => {
      staleRefresh.resolve({ unreads: [STALE_ROW] });
      // Flush the microtask chain inside `refreshUnreads` (the `await
      // api.unreads()` resuming, then its epoch check) so any (incorrect)
      // dispatch would have already landed before the assertion below.
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByTestId("c1-unread")).toHaveTextContent("0"); // NOT clobbered back to "5"
  });

  it("a refreshUnreads response with no intervening markRead still commits normally", async () => {
    await mountProbe([FRESH_ROW]); // c1 starts at 0 unread
    expect(screen.getByTestId("c1-unread")).toHaveTextContent("0");

    vi.mocked(api.unreads).mockResolvedValueOnce({ unreads: [STALE_ROW] }); // "5" is the new truth
    fireEvent.click(screen.getByText("refresh"));

    await waitFor(() => expect(screen.getByTestId("c1-unread")).toHaveTextContent("5"));
  });

  it("two overlapping refreshUnreads calls: only the more recently DISPATCHED response commits", async () => {
    await mountProbe([FRESH_ROW]);

    const firstRefresh = deferred<{ unreads: UnreadsRowOut[] }>();
    vi.mocked(api.unreads).mockReturnValueOnce(firstRefresh.promise);
    fireEvent.click(screen.getByText("refresh")); // dispatched first, held pending

    const secondRefresh = deferred<{ unreads: UnreadsRowOut[] }>();
    vi.mocked(api.unreads).mockReturnValueOnce(secondRefresh.promise);
    fireEvent.click(screen.getByText("refresh")); // dispatched second, also held pending

    // The SECOND (more recently dispatched) call resolves first.
    await act(async () => {
      secondRefresh.resolve({ unreads: [STALE_ROW] }); // "5"
      await Promise.resolve();
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByTestId("c1-unread")).toHaveTextContent("5"));

    // The FIRST (older) call resolves late, with different data ("0") --
    // it must lose, since a newer request was dispatched after it.
    await act(async () => {
      firstRefresh.resolve({ unreads: [FRESH_ROW] });
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("c1-unread")).toHaveTextContent("5"); // unchanged -- the older response lost
  });
});
