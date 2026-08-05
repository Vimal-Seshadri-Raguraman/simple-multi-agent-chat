import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { MemberOut, MessagePayload } from "../lib/api";
import MessageLine from "./MessageLine";

/**
 * The scrollable message feed for the current room (web spec §2's Room
 * bullet): day dividers, auto-follow that pauses the moment the reader
 * scrolls away from the bottom (surfacing a "N new below ↓" pill instead
 * of yanking their scroll position), scrollback that loads older pages
 * once they scroll to the top, and "viewing marks read" -- but only
 * while following, i.e. suppressed while scrolled away (the TUI-proven
 * semantics the constitution calls out by name).
 *
 * **The `onView` seam (task-3 brief, binding):** this component never
 * calls the mark-read API itself -- it calls the injected `onView(channelId)`
 * prop whenever "following the bottom, with something loaded" becomes
 * true, so Task 4 (live sockets) can extend/observe read behavior without
 * touching this file.
 *
 * **Why `key={channelId}` at the call site matters:** all of the scroll
 * state below (`following`, `newBelowCount`, the tail-tracking refs) is
 * per-ROOM state that must reset to "freshly following, no new-below
 * count" the moment the room changes -- `Room.tsx` mounts a fresh `Feed`
 * per channel (`key={channelId}`) rather than this component trying to
 * detect and reset on a `channelId` prop change itself.
 */

const BOTTOM_THRESHOLD_PX = 24;
const TOP_THRESHOLD_PX = 40;

type Row =
  | { kind: "divider"; key: string; label: string }
  | { kind: "message"; key: string; payload: MessagePayload };

/** No timezone conversion (mirrors `smac_cli/render.py`'s stance): the
 * server stores/display a naive wall-clock value, so grouping/labeling
 * uses the same local-time reading `MessageLine` uses for `[HH:MM]`. */
function dayLabel(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toDateString();
}

function buildRows(messages: MessagePayload[]): Row[] {
  const rows: Row[] = [];
  let lastLabel: string | null = null;
  for (const payload of messages) {
    const label = dayLabel(payload.timestamp);
    if (label !== lastLabel) {
      rows.push({ kind: "divider", key: `divider-${payload.Message.message_id}`, label });
      lastLabel = label;
    }
    rows.push({ kind: "message", key: payload.Message.message_id, payload });
  }
  return rows;
}

export type FeedProps = {
  channelId: string;
  messages: MessagePayload[];
  memberById: Record<string, MemberOut>;
  currentMemberId?: string;
  hasMoreOlder: boolean;
  loadingOlder: boolean;
  onLoadOlder: () => void;
  /** Injected mark-read callback (task-3 brief) -- called whenever the
   * reader is following the bottom of a non-empty feed. */
  onView: (channelId: string) => void;
};

export default function Feed({
  channelId,
  messages,
  memberById,
  currentMemberId,
  hasMoreOlder,
  loadingOlder,
  onLoadOlder,
  onView,
}: FeedProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [following, setFollowing] = useState(true);
  const [newBelowCount, setNewBelowCount] = useState(0);
  const prevLengthRef = useRef(0);
  const prevTailIdRef = useRef<string | null>(null);

  // Auto-scroll to bottom on a tail append while following; otherwise
  // bump the "N new below" count. A head-only prepend (load-older) never
  // changes the tail id, so it falls through untouched -- exactly what
  // the top-load-older behavior wants (no jump, no pill bump).
  useLayoutEffect(() => {
    const newTailId = messages.length > 0 ? messages[messages.length - 1].Message.message_id : null;
    const grew = messages.length > prevLengthRef.current;
    const tailChanged = newTailId !== prevTailIdRef.current;
    if (grew && tailChanged) {
      const appendedCount = messages.length - prevLengthRef.current;
      if (following) {
        const el = scrollRef.current;
        if (el) {
          el.scrollTop = el.scrollHeight;
        }
        setNewBelowCount(0);
      } else {
        setNewBelowCount((n) => n + appendedCount);
      }
    }
    prevLengthRef.current = messages.length;
    prevTailIdRef.current = newTailId;
  }, [messages, following]);

  // View-marks-read, suppressed while scrolled: fires whenever
  // "following the bottom, with a loaded message" becomes true --
  // initial mount at the bottom, a fresh message arriving while
  // following, or scrolling back down all funnel through here.
  useEffect(() => {
    if (following && messages.length > 0) {
      onView(channelId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [following, messages.length, channelId]);

  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const atBottom = distanceFromBottom <= BOTTOM_THRESHOLD_PX;
    setFollowing(atBottom);
    if (atBottom) {
      setNewBelowCount(0);
    }
    if (el.scrollTop <= TOP_THRESHOLD_PX && hasMoreOlder && !loadingOlder) {
      onLoadOlder();
    }
  }

  function jumpToBottom() {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
    setFollowing(true);
    setNewBelowCount(0);
  }

  const rows = buildRows(messages);

  return (
    <div className="feed">
      <div
        className="feed__scroll"
        ref={scrollRef}
        onScroll={handleScroll}
        data-testid="feed-scroll"
      >
        {loadingOlder && <div className="feed__loading-older">Loading older messages…</div>}
        {rows.map((row) =>
          row.kind === "divider" ? (
            <div className="feed__day-divider" key={row.key}>
              <span>{row.label}</span>
            </div>
          ) : (
            <MessageLine
              key={row.key}
              payload={row.payload}
              memberById={memberById}
              currentMemberId={currentMemberId}
            />
          )
        )}
      </div>
      {!following && newBelowCount > 0 && (
        <button type="button" className="feed__new-below-pill" onClick={jumpToBottom}>
          {newBelowCount} new below ↓
        </button>
      )}
    </div>
  );
}
