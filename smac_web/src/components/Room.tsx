import type { ChannelOut, MemberOut, MessagePayload } from "../lib/api";
import Composer from "./Composer";
import Feed from "./Feed";

/**
 * The center room (web spec §2): header (`#name`, member count that opens
 * the drawer), the message feed, and the composer. `Feed` is mounted with
 * `key={channel.channel_id}` -- see `Feed.tsx`'s module docstring for why
 * a fresh instance per room (rather than the same instance reacting to a
 * changed `channelId` prop) is what resets its scroll/follow state
 * cleanly on every room switch.
 *
 * **Mobile tier (task-4 brief, web spec §1):** when `mobile` is true, a
 * hamburger button appears at the head of the room header -- the rail's
 * open affordance now that the rail itself is off-screen by default
 * (`Rail.tsx`'s drawer mode).
 */

export type RoomProps = {
  channel: ChannelOut | null;
  memberCount: number;
  onOpenDrawer: () => void;
  messages: MessagePayload[];
  memberById: Record<string, MemberOut>;
  currentMemberId?: string;
  hasMoreOlder: boolean;
  loadingOlder: boolean;
  onLoadOlder: () => void;
  onView: (channelId: string) => void;
  members: MemberOut[];
  channels: ChannelOut[];
  onSend: (text: string) => Promise<void>;
  onOpenPalette: (prefilter: string) => void;
  /** Mobile tier active (<900px). Default `false` -- desktop callers/tests unaffected. */
  mobile?: boolean;
  /** Opens the rail drawer (only rendered/used when `mobile` is true). */
  onOpenRail?: () => void;
};

export default function Room({
  channel,
  memberCount,
  onOpenDrawer,
  messages,
  memberById,
  currentMemberId,
  hasMoreOlder,
  loadingOlder,
  onLoadOlder,
  onView,
  members,
  channels,
  onSend,
  onOpenPalette,
  mobile = false,
  onOpenRail,
}: RoomProps) {
  return (
    <div className="room">
      <header className="room__header">
        {mobile && (
          <button
            type="button"
            className="room__hamburger"
            aria-label="Open channels"
            onClick={onOpenRail}
          >
            ☰
          </button>
        )}
        <h2 className="room__title">{channel ? `#${channel.channel_name}` : "No channel"}</h2>
        <button type="button" className="room__member-count" onClick={onOpenDrawer}>
          {memberCount} member{memberCount === 1 ? "" : "s"}
        </button>
      </header>
      {channel ? (
        <Feed
          key={channel.channel_id}
          channelId={channel.channel_id}
          messages={messages}
          memberById={memberById}
          currentMemberId={currentMemberId}
          hasMoreOlder={hasMoreOlder}
          loadingOlder={loadingOlder}
          onLoadOlder={onLoadOlder}
          onView={onView}
        />
      ) : (
        <div className="room__empty">No channel yet -- create one from the rail.</div>
      )}
      <Composer
        members={members}
        channels={channels}
        onSend={onSend}
        onOpenPalette={onOpenPalette}
        disabled={channel === null}
      />
    </div>
  );
}
