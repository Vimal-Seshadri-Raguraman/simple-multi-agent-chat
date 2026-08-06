import type { ReactNode } from "react";
import type { MemberOut, MessagePayload } from "../lib/api";
import { initialsFor } from "../lib/avatar";

/**
 * One channel message, rendered per web spec §2's Room bullet:
 *  - `[HH:MM] @handle` metadata in mono type.
 *  - the handle tinted the constitution's `agent` color when the sender
 *    is an agent (member type comes from the WORKSPACE MEMBER DIRECTORY,
 *    not the payload -- `Sender` only ever carries `member_id`/
 *    `member_name`, mirroring `smac_cli/render.py`'s documented "sender-
 *    handle gap": the server has no per-message reason to resolve a
 *    handle or member type, so the client-side member directory is the
 *    only place either lives).
 *  - `<@member_id>` tokens in the message text resolved to `@handle`
 *    chips using the payload's OWN `mentions` array only (task-3 brief,
 *    binding for web -- deliberately simpler than the TUI's self-mention
 *    `extra_handles` fallback); a token with no matching entry in
 *    `mentions` is left exactly as written, literally, per the same rule.
 *  - a mention-of-me line (the caller's own `member_id` appears in
 *    `mentions`) gets the `mention-bg` token as a background.
 *
 * **Security (constitution §7.5 / web spec §4, mandatory):** message text
 * is untrusted input. Every piece of it -- literal runs, unresolved
 * tokens, chip labels -- is rendered as a plain React child (a string or
 * a `<span>` wrapping a string), i.e. TEXT NODES ONLY. There is no
 * `dangerouslySetInnerHTML` anywhere in this file (the eslint
 * `react/no-danger` rule also enforces this repo-wide) and no HTML
 * parsing of the message text at all -- a string that merely *looks*
 * like markup (`<img onerror=...>`, `<script>...`) is never treated as
 * anything but characters to display.
 */

const MENTION_TOKEN = /<@([^>]+)>/g;

/** Split `text` into a list of plain strings and mention-chip elements.
 * Only `<@id>` runs are ever treated specially; everything else --
 * including strings that look like HTML -- passes through as an ordinary
 * string child, which React renders as an inert text node. */
function renderMessageText(
  text: string,
  mentions: MessagePayload["mentions"]
): ReactNode[] {
  const handleById = new Map(mentions.map((m) => [m.member_id, m.handle]));
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let key = 0;
  MENTION_TOKEN.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = MENTION_TOKEN.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const handle = handleById.get(match[1]);
    if (handle !== undefined) {
      parts.push(
        <span className="message-line__mention-chip" key={`mention-${key}`}>
          @{handle}
        </span>
      );
      key += 1;
    } else {
      // Unknown token -- literal text, unchanged.
      parts.push(match[0]);
    }
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts;
}

/** `HH:MM` from an ISO 8601 timestamp -- no timezone conversion (mirrors
 * `smac_cli/render.py`'s `_format_hh_mm`: the server stores naive UTC and
 * this is a same-machine client, so the clock value is shown as recorded). */
function formatHHMM(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return "--:--";
  }
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

export type MessageLineProps = {
  payload: MessagePayload;
  /** The workspace member directory, keyed by `member_id` -- the only
   * source for the sender's handle and member type (see module docstring). */
  memberById: Record<string, MemberOut>;
  /** The viewer's own member id, for mention-of-me highlighting. */
  currentMemberId?: string;
};

export default function MessageLine({ payload, memberById, currentMemberId }: MessageLineProps) {
  const senderMember = memberById[payload.Sender.member_id];
  const handle = senderMember?.handle ?? payload.Sender.member_name;
  const isAgent = senderMember?.member_type !== undefined && senderMember.member_type !== "human";
  const mentionsMe =
    currentMemberId !== undefined &&
    payload.mentions.some((m) => m.member_id === currentMemberId);

  const className = [
    "message-line",
    mentionsMe ? "message-line--mention" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const avatarClassName = isAgent
    ? "message-line__avatar message-line__avatar--agent"
    : "message-line__avatar";

  return (
    <div className={className} data-testid="message-line">
      <span className={avatarClassName} aria-hidden="true">
        {initialsFor(senderMember?.member_name, handle)}
      </span>
      <div className="message-line__body">
        <span className="message-line__meta">
          <span className="message-line__time">[{formatHHMM(payload.timestamp)}]</span>{" "}
          <span
            className={
              isAgent ? "message-line__handle message-line__handle--agent" : "message-line__handle"
            }
          >
            @{handle}
          </span>
          {": "}
        </span>
        <span className="message-line__text">
          {renderMessageText(payload.Message.message_text, payload.mentions)}
        </span>
      </div>
    </div>
  );
}
