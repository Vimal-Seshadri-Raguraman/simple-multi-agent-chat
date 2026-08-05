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
    case "HISTORY_DONE":
      if (action.channelId !== state.currentChannelId) {
        return state; // a room switch raced this fetch -- discard the stale result
      }
      return { ...state, messages: action.messages, hasMoreOlder: action.hasMoreOlder };
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
    const out = await api.unreads();
    dispatch({ type: "UNREADS", unreads: out.unreads });
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
      const message = await api.post(channelId, text);
      dispatch({ type: "APPEND_MESSAGE", channelId, message });
    },
    [state.currentChannelId]
  );

  const markRead = useCallback(async (channelId: string) => {
    if (markReadInFlight.current.has(channelId)) return;
    markReadInFlight.current.add(channelId);
    try {
      const row = await api.markRead(channelId);
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
      refreshMembers,
      refreshHistory,
      loadOlderMessages,
      createChannel,
      sendMessage,
      markRead,
    }),
    [
      state,
      selectChannel,
      findChannelByName,
      refreshUnreads,
      refreshMembers,
      refreshHistory,
      loadOlderMessages,
      createChannel,
      sendMessage,
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
