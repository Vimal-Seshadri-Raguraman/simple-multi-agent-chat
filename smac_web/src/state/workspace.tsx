/**
 * The authed shell's data store: channels, unread/mention badges, the
 * member directory, the current room, and its loaded message window.
 * Hand-rolled React context + reducer (no react-query/redux -- same YAGNI
 * call as `state/auth.tsx`). One `<WorkspaceProvider>` wraps the whole
 * authed shell (`components/AuthedShell.tsx`); every shell component
 * reads/dispatches through `useWorkspace()`.
 *
 * **Task-4 seam (binding, task-3 brief):** live sockets aren't wired up
 * yet -- this task only fetches over REST. `refreshUnreads()` and
 * `refreshHistory()` are exposed on the context value specifically so
 * Task 4 can call them (on socket (re)connect, the "catch-up-then-live"
 * discipline the server + TUI already proved: spec §3) without reaching
 * into `Feed`'s internals or this file's private state shape. Likewise
 * `appendMessage()` is the one seam a socket handler needs to push a
 * freshly-arrived message into the current room's feed.
 *
 * **The mark-read seam:** `Feed` never calls `api.markRead` itself -- it
 * takes an injected `onView(channelId)` prop (task-3 brief) that this
 * store implements as `markRead`. That keeps `Feed`'s scroll/visibility
 * logic pure and independent of the API/store, and gives Task 4 one
 * place (`markRead` here) to extend if socket-driven read receipts need
 * different debouncing later.
 *
 * **The history-paging port:** the server's `GET .../messages` endpoint
 * only supports a forward `after` cursor (§ its own docstring) -- there
 * is no `before`/descending option. `smac_cli/app.py`'s `_walk_message_
 * pages` (module docstring) already solved "get the most recent page" and
 * "get the page immediately before an anchor" against that constraint by
 * walking forward from position 0 in pages and keeping only the last one
 * that matters; `walkMessagePages` below is a faithful TS port of that
 * same algorithm, reused for both `refreshHistory` (recent tail) and
 * `loadOlderMessages` (the page immediately preceding the oldest message
 * currently loaded).
 */

import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
} from "react";
import * as api from "../lib/api";
import type {
  ChannelOut,
  MemberOut,
  MemberSelfOut,
  MessagePayload,
  UnreadsRowOut,
} from "../lib/api";

export type WorkspaceState = {
  channels: ChannelOut[];
  /** Keyed by `channel_id`. */
  unreads: Record<string, UnreadsRowOut>;
  members: MemberOut[];
  /** The caller's own full profile (handle, admin flag, ...) -- `null`
   * until the initial fetch resolves. */
  self: MemberSelfOut | null;
  currentChannelId: string | null;
  /** The current channel's loaded message window (oldest first). */
  messages: MessagePayload[];
  /** True while there is more history to load ABOVE `messages[0]`. */
  hasMoreOlder: boolean;
  /** True while the initial channels/unreads/members/self fetch is in flight. */
  loading: boolean;
  /** True while a load-older-history request is in flight. */
  loadingOlder: boolean;
  error: string | null;
};

type Action =
  | { type: "LOAD_START" }
  | {
      type: "LOAD_DONE";
      channels: ChannelOut[];
      unreads: UnreadsRowOut[];
      members: MemberOut[];
      self: MemberSelfOut;
    }
  | { type: "LOAD_ERROR"; message: string }
  | { type: "UNREADS"; unreads: UnreadsRowOut[] }
  | { type: "UNREAD_ROW"; row: UnreadsRowOut }
  | { type: "MEMBERS"; members: MemberOut[] }
  | { type: "CHANNELS"; channels: ChannelOut[] }
  | { type: "SELECT_CHANNEL"; channelId: string }
  | { type: "HISTORY_START" }
  | { type: "HISTORY_DONE"; channelId: string; messages: MessagePayload[]; hasMoreOlder: boolean }
  | { type: "OLDER_START" }
  | { type: "OLDER_DONE"; channelId: string; messages: MessagePayload[]; hasMoreOlder: boolean }
  | { type: "APPEND_MESSAGE"; channelId: string; message: MessagePayload }
  | { type: "CHANNEL_CREATED"; channel: ChannelOut };

function reducer(state: WorkspaceState, action: Action): WorkspaceState {
  switch (action.type) {
    case "LOAD_START":
      return { ...state, loading: true, error: null };
    case "LOAD_DONE": {
      const unreads: Record<string, UnreadsRowOut> = {};
      for (const row of action.unreads) {
        unreads[row.channel_id] = row;
      }
      return {
        ...state,
        loading: false,
        error: null,
        channels: action.channels,
        unreads,
        members: action.members,
        self: action.self,
      };
    }
    case "LOAD_ERROR":
      return { ...state, loading: false, error: action.message };
    case "UNREADS": {
      const unreads: Record<string, UnreadsRowOut> = {};
      for (const row of action.unreads) {
        unreads[row.channel_id] = row;
      }
      return { ...state, unreads };
    }
    case "UNREAD_ROW":
      return {
        ...state,
        unreads: { ...state.unreads, [action.row.channel_id]: action.row },
      };
    case "MEMBERS":
      return { ...state, members: action.members };
    case "CHANNELS": {
      // Final review Finding 3 (MINOR, same bug class as T6's unreads
      // epoch fix): `refreshChannels` is fired unprompted by the bell
      // handler on ANY unknown-channel mention and can be in flight for a
      // while. A response that was in flight BEFORE the reader created a
      // channel (`CHANNEL_CREATED`) landing AFTER would otherwise
      // wholesale-clobber the rail, dropping the very channel the reader
      // is now sitting in (`currentChannelId` pointing outside `channels`
      // -- Room header shows "No channel"). There is no channel-DELETE
      // feature in this product, so a locally-known channel going missing
      // from a fresh fetch can only mean the fetch is stale, never that
      // the channel was legitimately removed -- union by `channel_id`
      // rather than replace, keeping the server's own ordering first.
      const freshIds = new Set(action.channels.map((c) => c.channel_id));
      const localOnly = state.channels.filter((c) => !freshIds.has(c.channel_id));
      return { ...state, channels: [...action.channels, ...localOnly] };
    }
    case "SELECT_CHANNEL":
      if (state.currentChannelId === action.channelId) {
        return state;
      }
      return {
        ...state,
        currentChannelId: action.channelId,
        messages: [],
        hasMoreOlder: false,
      };
    case "HISTORY_START":
      return { ...state, error: null };
    case "HISTORY_DONE": {
      if (action.channelId !== state.currentChannelId) {
        return state; // a room switch raced this fetch -- discard the stale result
      }
      // Final review Finding 3 (MINOR, same bug class as T6's unreads
      // epoch fix): `refreshHistory`'s walk is several sequential `await`s
      // (`walkMessagePages`); a live message (including the sender's OWN
      // send echo) can land via `APPEND_MESSAGE` mid-walk, after the
      // page(s) that became `action.messages` were already snapshotted --
      // a bare wholesale replace here would make that message vanish from
      // the feed until the next refresh. Union by `message_id` instead: the
      // fresh server snapshot, plus anything already in local state that
      // snapshot doesn't know about yet (a mid-walk append, OR a
      // previously-loaded-older scrollback page `loadOlderMessages`
      // prepended -- neither should be silently discarded by a background
      // catch-up refresh), re-sorted into chronological (oldest-first)
      // order by `timestamp`.
      const freshIds = new Set(action.messages.map((m) => m.Message.message_id));
      const notInFreshSnapshot = state.messages.filter(
        (m) => !freshIds.has(m.Message.message_id)
      );
      const messages =
        notInFreshSnapshot.length === 0
          ? action.messages
          : [...action.messages, ...notInFreshSnapshot].sort((a, b) =>
              a.timestamp < b.timestamp ? -1 : a.timestamp > b.timestamp ? 1 : 0
            );
      return { ...state, messages, hasMoreOlder: action.hasMoreOlder };
    }
    case "OLDER_START":
      return { ...state, loadingOlder: true };
    case "OLDER_DONE":
      if (action.channelId !== state.currentChannelId) {
        return { ...state, loadingOlder: false };
      }
      return {
        ...state,
        loadingOlder: false,
        messages: [...action.messages, ...state.messages],
        hasMoreOlder: action.hasMoreOlder,
      };
    case "APPEND_MESSAGE":
      if (action.channelId !== state.currentChannelId) {
        return state;
      }
      return { ...state, messages: [...state.messages, action.message] };
    case "CHANNEL_CREATED":
      return { ...state, channels: [...state.channels, action.channel] };
    default:
      return state;
  }
}

function initialState(): WorkspaceState {
  return {
    channels: [],
    unreads: {},
    members: [],
    self: null,
    currentChannelId: null,
    messages: [],
    hasMoreOlder: false,
    loading: false,
    loadingOlder: false,
    error: null,
  };
}

/**
 * `smac_cli/app.py`'s `_walk_message_pages`, ported: walk `channel_id`'s
 * history forward from the beginning in pages, returning every page
 * fetched. With no `stopBeforeId`, the walk runs to the very end (a short
 * -- shorter than `pageSize` -- page proves "caught up"); the LAST page
 * collected is therefore the channel's most recent messages. With
 * `stopBeforeId` set to a message id already on screen, the walk stops
 * the moment it finds that id in a page (keeping everything strictly
 * before it, excluding the anchor itself) -- the last page collected is
 * then exactly the page immediately preceding that anchor.
 */
async function walkMessagePages(
  channelId: string,
  stopBeforeId?: string
): Promise<MessagePayload[][]> {
  const pages: MessagePayload[][] = [];
  let after: string | undefined;
  const pageSize = api.DEFAULT_MESSAGE_LIMIT;
  for (;;) {
    const page = await api.messages(channelId, after, pageSize);
    if (page.length === 0) {
      break;
    }
    if (stopBeforeId !== undefined) {
      const anchorIndex = page.findIndex((m) => m.Message.message_id === stopBeforeId);
      if (anchorIndex !== -1) {
        if (anchorIndex > 0) {
          pages.push(page.slice(0, anchorIndex));
        }
        break;
      }
    }
    pages.push(page);
    after = page[page.length - 1].Message.message_id;
    if (page.length < pageSize) {
      break;
    }
  }
  return pages;
}

export type WorkspaceContextValue = WorkspaceState & {
  selectChannel: (channelId: string) => void;
  /** Look up a channel by exact (case-insensitive) name; `undefined` if none matches. */
  findChannelByName: (name: string) => ChannelOut | undefined;
  /** Re-fetch the unread/mention badge overview for every channel. */
  refreshUnreads: () => Promise<void>;
  /** Re-fetch the workspace's channel list. A channel someone else creates
   * is otherwise invisible to this client forever -- there is no "channel
   * created" broadcast on the live layer, only messages and mentions --
   * so a mention arriving for a channel this client doesn't know about
   * yet is exactly the signal to call this (`AuthedShell.tsx`'s bell
   * handler does, alongside `refreshUnreads`): otherwise the rail could
   * never show that channel at all, let alone its mention badge. */
  refreshChannels: () => Promise<void>;
  /** Re-fetch the member directory. */
  refreshMembers: () => Promise<void>;
  /** Re-load the CURRENT channel's most recent message window from
   * scratch (task-3 brief: the seam Task 4 re-runs on socket reconnect). */
  refreshHistory: () => Promise<void>;
  /** Fetch the page of history immediately before what's currently
   * loaded, prepending it. No-op if there's nothing more or a fetch is
   * already in flight. */
  loadOlderMessages: () => Promise<void>;
  /** Create a channel and switch to it. */
  createChannel: (name: string) => Promise<void>;
  /** Post a message to the current channel, appending it locally on success. */
  sendMessage: (text: string) => Promise<void>;
  /** Push a freshly-arrived LIVE message into `channelId`'s feed (task-4
   * seam, per this file's module docstring above) -- the counterpart to
   * `sendMessage`'s own local append, but for messages the socket
   * delivered rather than this tab's own POST. A no-op if `channelId`
   * isn't the channel currently on screen (`APPEND_MESSAGE`'s reducer
   * case already guards this), so a live.ts handler never needs to check
   * "is this still the current room?" itself before calling it. */
  appendMessage: (channelId: string, message: MessagePayload) => void;
  /** Advance the current channel's read cursor to caught-up-with-`messages`
   * and refresh the local unread badge for it. The `onView` callback
   * `Feed` is handed (task-3 brief's injected mark-read seam). */
  markRead: (channelId: string) => Promise<void>;
};

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, undefined, initialState);
  // Coalesces overlapping markRead calls for the same channel (Feed can
  // call onView repeatedly while the user sits at the bottom of a live
  // channel) into "only one in flight at a time per channel".
  const markReadInFlight = useRef<Set<string>>(new Set());
  // Fix round 1 (review finding): a stale-overwrite race between
  // `refreshUnreads`'s wholesale `UNREADS` dispatch and `markRead`'s
  // scoped, always-fresh `UNREAD_ROW` dispatch. `refreshUnreads` is
  // fired unprompted by the bell handler (`AuthedShell.tsx`, on ANY
  // other-room mention) and can be in flight for a while; if a `markRead`
  // call starts and resolves WHILE an earlier `refreshUnreads` request is
  // still in flight, that request's eventual response reflects
  // server state from BEFORE the mark-read landed -- dispatching it
  // clobbers the just-applied `UNREAD_ROW` update, pinning a badge back
  // to its pre-mark-read count (reproduced by the review: `npm run e2e`
  // run repeatedly, badge stuck at 1 after landing in the channel).
  //
  // `unreadsEpoch` is a monotonic counter: every `refreshUnreads` call
  // captures the counter's value at REQUEST time and only dispatches its
  // response if the counter is unchanged when the response arrives; every
  // `markRead` call that lands bumps the counter, so any
  // `refreshUnreads` response still in flight at that moment -- no matter
  // when it eventually arrives -- is provably stale and discarded instead
  // of overwriting `markRead`'s own (necessarily fresher) row update.
  // This also naturally resolves overlapping `refreshUnreads` calls
  // against EACH OTHER: only the most recently DISPATCHED one's response
  // can ever commit, regardless of arrival order.
  const unreadsEpoch = useRef(0);

  useEffect(() => {
    let cancelled = false;
    dispatch({ type: "LOAD_START" });
    Promise.all([api.channels(), api.unreads(), api.members(), api.whoami()])
      .then(([channels, unreadsOut, members, self]) => {
        if (cancelled) return;
        dispatch({ type: "LOAD_DONE", channels, unreads: unreadsOut.unreads, members, self });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : "Failed to load workspace.";
        dispatch({ type: "LOAD_ERROR", message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const refreshUnreads = useCallback(async () => {
    const requestEpoch = ++unreadsEpoch.current;
    const out = await api.unreads();
    if (unreadsEpoch.current !== requestEpoch) {
      // A `markRead` (or a newer `refreshUnreads`) landed while this
      // request was in flight -- this response is stale, discard it
      // rather than clobber fresher state. See `unreadsEpoch`'s own
      // docstring above for the full race this guards against.
      return;
    }
    dispatch({ type: "UNREADS", unreads: out.unreads });
  }, []);

  const refreshChannels = useCallback(async () => {
    const fetchedChannels = await api.channels();
    dispatch({ type: "CHANNELS", channels: fetchedChannels });
  }, []);

  const refreshMembers = useCallback(async () => {
    const members = await api.members();
    dispatch({ type: "MEMBERS", members });
  }, []);

  const selectChannel = useCallback((channelId: string) => {
    dispatch({ type: "SELECT_CHANNEL", channelId });
  }, []);

  const findChannelByName = useCallback(
    (name: string): ChannelOut | undefined => {
      const needle = name.trim().toLowerCase();
      return state.channels.find((c) => c.channel_name.toLowerCase() === needle);
    },
    [state.channels]
  );

  const refreshHistory = useCallback(async () => {
    const channelId = state.currentChannelId;
    if (channelId === null) return;
    dispatch({ type: "HISTORY_START" });
    const pages = await walkMessagePages(channelId);
    const recent = pages.length > 0 ? pages[pages.length - 1] : [];
    dispatch({ type: "HISTORY_DONE", channelId, messages: recent, hasMoreOlder: pages.length > 1 });
  }, [state.currentChannelId]);

  const loadOlderMessages = useCallback(async () => {
    const channelId = state.currentChannelId;
    if (channelId === null || state.loadingOlder || !state.hasMoreOlder) return;
    const oldest = state.messages[0]?.Message.message_id;
    if (oldest === undefined) return;
    dispatch({ type: "OLDER_START" });
    const pages = await walkMessagePages(channelId, oldest);
    const older = pages.length > 0 ? pages[pages.length - 1] : [];
    dispatch({
      type: "OLDER_DONE",
      channelId,
      messages: older,
      hasMoreOlder: pages.length > 1,
    });
  }, [state.currentChannelId, state.loadingOlder, state.hasMoreOlder, state.messages]);

  const createChannel = useCallback(async (name: string) => {
    const channel = await api.createChannel(name);
    dispatch({ type: "CHANNEL_CREATED", channel });
    dispatch({ type: "SELECT_CHANNEL", channelId: channel.channel_id });
  }, []);

  const sendMessage = useCallback(
    async (text: string) => {
      const channelId = state.currentChannelId;
      if (channelId === null) return;
      // Deliberately NO local append here -- mirrors `smac_cli/app.py`'s
      // `post_current` (see its own docstring: "the message itself
      // arrives back through the channel feed's own self-echo, never
      // appended directly here"). The server's broadcast
      // (`app/routers/messages.py::post_message` -> `manager.broadcast`)
      // goes to every connection subscribed to this room's socket,
      // including the SENDER's own -- `AuthedShell.tsx`'s `connectRoom`
      // effect is exactly that connection whenever this room is the one
      // on screen. Appending here too (as an earlier version of this
      // file did) double-posted every message the instant its own socket
      // echo landed a moment later, since neither side deduped by
      // `message_id` (found via SMAC-85 Task 6's e2e journey: a real
      // "hi" typed and sent showed up twice in the sender's own feed).
      await api.post(channelId, text);
    },
    [state.currentChannelId]
  );

  const appendMessage = useCallback((channelId: string, message: MessagePayload) => {
    dispatch({ type: "APPEND_MESSAGE", channelId, message });
  }, []);

  const markRead = useCallback(async (channelId: string) => {
    if (markReadInFlight.current.has(channelId)) return;
    markReadInFlight.current.add(channelId);
    try {
      const row = await api.markRead(channelId);
      // Bump BEFORE dispatching: any `refreshUnreads` response that was
      // already in flight when this landed is now provably stale (see
      // `unreadsEpoch`'s docstring) and must lose the race even if it
      // resolves a tick later than this dispatch.
      unreadsEpoch.current += 1;
      dispatch({ type: "UNREAD_ROW", row });
    } finally {
      markReadInFlight.current.delete(channelId);
    }
  }, []);

  // Load (or re-load) history whenever the current channel changes.
  useEffect(() => {
    if (state.currentChannelId !== null) {
      void refreshHistory();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.currentChannelId]);

  // Auto-select the first channel once channels load, if nothing is selected yet.
  useEffect(() => {
    if (state.currentChannelId === null && state.channels.length > 0) {
      dispatch({ type: "SELECT_CHANNEL", channelId: state.channels[0].channel_id });
    }
  }, [state.channels, state.currentChannelId]);

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      ...state,
      selectChannel,
      findChannelByName,
      refreshUnreads,
      refreshChannels,
      refreshMembers,
      refreshHistory,
      loadOlderMessages,
      createChannel,
      sendMessage,
      appendMessage,
      markRead,
    }),
    [
      state,
      selectChannel,
      findChannelByName,
      refreshUnreads,
      refreshChannels,
      refreshMembers,
      refreshHistory,
      loadOlderMessages,
      createChannel,
      sendMessage,
      appendMessage,
      markRead,
    ]
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (ctx === null) {
    throw new Error("useWorkspace() must be called within a <WorkspaceProvider>");
  }
  return ctx;
}

export type { ChannelOut, MemberOut, MemberSelfOut, MessagePayload, UnreadsRowOut };
