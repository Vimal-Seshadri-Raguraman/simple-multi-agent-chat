import { type FormEvent, type TouchEvent, useRef, useState } from "react";
import type { ChannelOut, MemberSelfOut, Membership, UnreadsRowOut } from "../lib/api";
import { initialsFor } from "../lib/avatar";
import { ROLE_LABELS } from "../lib/capabilities";

/**
 * The left rail (web spec §2): workspace name + switcher (memberships +
 * create/join), a gear button next to the workspace name that opens
 * Settings, the channel list with live unread/mention badges, "+"
 * create channel, and YOU at the bottom (avatar/handle -> menu: whoami
 * card, a "Settings" item, theme toggle, logout).
 *
 * SMAC-85: before this, Settings (`screens/Settings.tsx` -- agents/
 * invites/workspace admin, including workspace delete) was reachable
 * ONLY via Cmd-K palette commands (`/invite`, `/workspace delete`) --
 * no mouse-first affordance existed anywhere in the shell. The gear
 * button and the YOU menu's "Settings" item both call `onOpenSettings`
 * (owned by `AuthedShell.tsx`, same lifted-state pattern as the YOU
 * menu itself below), landing on Settings' default section (Agents).
 *
 * The YOU menu's open/closed state is lifted to the shell
 * (`AuthedShell.tsx`) rather than owned locally, because the Cmd-K
 * palette's `/whoami` command (constitution §4: "web -> avatar menu")
 * needs to be able to open the exact same menu from outside this
 * component.
 *
 * **Mobile tier (task-4 brief, web spec §1 "<900px"):** on desktop this
 * is a persistent flex column, as always. When `mobile` is true it
 * becomes a swipe/tap drawer instead -- `open` (driven by `AuthedShell`'s
 * hamburger-button state) toggles a CSS transform between on- and
 * off-screen, a backdrop appears behind it that closes it on click, and
 * a left-swipe on the drawer itself calls `onRequestClose` too (the
 * spec's "hamburger + swipe" bullet: hamburger opens, swipe dismisses).
 * The `<nav>` itself always stays mounted (never conditionally
 * rendered) so its own local state (the switcher/create-channel/you-menu
 * popovers) doesn't get wiped every time the drawer closes.
 */

export type RailProps = {
  workspaceName: string;
  memberships: Membership[];
  currentWorkspaceId?: string;
  onSwitchWorkspace: (workspaceId: string) => void;
  onCreateOrJoinAnother: () => void;
  channels: ChannelOut[];
  unreads: Record<string, UnreadsRowOut>;
  currentChannelId: string | null;
  onSelectChannel: (channelId: string) => void;
  onCreateChannel: (name: string) => void;
  self: MemberSelfOut | null;
  youMenuOpen: boolean;
  onSetYouMenuOpen: (open: boolean) => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onLogout: () => void;
  /** Opens Settings at its default section (web spec §2: the
   * administration home) -- SMAC-85's clickable entry points, wired to
   * BOTH the rail's gear button (next to the workspace name) and the
   * YOU menu's "Settings" item, since previously Settings was reachable
   * ONLY via Cmd-K palette commands with no mouse-first affordance at
   * all. */
  onOpenSettings: () => void;
  /** Mobile tier active (<900px, per `state/viewport.tsx`). Default
   * `false` -- every existing (desktop) caller/test is unaffected. */
  mobile?: boolean;
  /** Whether the drawer is open. Ignored (always effectively open) when
   * `mobile` is false. */
  open?: boolean;
  /** Called on backdrop click or a left-swipe past the threshold. Only
   * relevant when `mobile` is true. */
  onRequestClose?: () => void;
};

/** How far (px) a swipe must travel left before it counts as "dismiss
 * the drawer" rather than an incidental touch/scroll. */
const SWIPE_CLOSE_THRESHOLD_PX = 40;

export default function Rail({
  workspaceName,
  memberships,
  currentWorkspaceId,
  onSwitchWorkspace,
  onCreateOrJoinAnother,
  channels,
  unreads,
  currentChannelId,
  onSelectChannel,
  onCreateChannel,
  self,
  youMenuOpen,
  onSetYouMenuOpen,
  theme,
  onToggleTheme,
  onLogout,
  onOpenSettings,
  mobile = false,
  open = true,
  onRequestClose,
}: RailProps) {
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [showCreateChannel, setShowCreateChannel] = useState(false);
  const [newChannelName, setNewChannelName] = useState("");
  const touchStartXRef = useRef<number | null>(null);

  function handleTouchStart(event: TouchEvent<HTMLElement>) {
    touchStartXRef.current = event.touches[0]?.clientX ?? null;
  }

  function handleTouchEnd(event: TouchEvent<HTMLElement>) {
    const startX = touchStartXRef.current;
    touchStartXRef.current = null;
    if (startX === null || !onRequestClose) return;
    const endX = event.changedTouches[0]?.clientX ?? startX;
    if (startX - endX > SWIPE_CLOSE_THRESHOLD_PX) {
      onRequestClose();
    }
  }

  function submitCreateChannel(event: FormEvent) {
    event.preventDefault();
    const name = newChannelName.trim();
    if (!name) return;
    onCreateChannel(name);
    setNewChannelName("");
    setShowCreateChannel(false);
  }

  const navClassName = mobile
    ? `rail rail--drawer ${open ? "rail--drawer-open" : "rail--drawer-closed"}`
    : "rail";

  return (
    <>
      {mobile && open && (
        <div className="rail__backdrop" data-testid="rail-backdrop" onClick={onRequestClose} />
      )}
      <nav
        className={navClassName}
        aria-label="Workspace navigation"
        // `inert` (not just `aria-hidden`) when closed: `aria-hidden` alone
        // hides the drawer from assistive tech but leaves its buttons in
        // the tab order -- an off-screen, keyboard-reachable control is an
        // invalid ARIA state (a review finding, task-4 fix round 1).
        // `inert` removes it from both focus and the accessibility tree;
        // `aria-hidden` is kept alongside for the (now redundant, but
        // harmless) explicit AT signal on browsers with older `inert`
        // support.
        {...(mobile && !open
          ? // This React/ReactDOM version doesn't recognize `inert` as a
            // known boolean DOM property (a bare `true` is silently
            // dropped with a dev warning) -- the plain HTML boolean-
            // attribute form (any string value, presence is what
            // matters) is what actually lands in the DOM.
            { "aria-hidden": true, inert: "true" }
          : {})}
        onTouchStart={mobile ? handleTouchStart : undefined}
        onTouchEnd={mobile ? handleTouchEnd : undefined}
      >
        <div className="rail__workspace">
          <div className="rail__workspace-row">
            <button
              type="button"
              className="rail__workspace-name"
              onClick={() => setSwitcherOpen((v) => !v)}
              aria-expanded={switcherOpen}
            >
              {workspaceName}
            </button>
            {/* Design system constitution §2: a dim icon-button that
             * brightens on hover, not a colored/accented button -- Settings
             * is administration, not the product's one confident accent. */}
            <button
              type="button"
              className="rail__settings-button"
              aria-label="Settings"
              onClick={onOpenSettings}
            >
              ⚙
            </button>
          </div>
          {switcherOpen && (
            <div className="rail__switcher" role="menu">
              {memberships.map((m) => (
                <button
                  key={m.workspace_id}
                  type="button"
                  role="menuitem"
                  className={
                    m.workspace_id === currentWorkspaceId
                      ? "rail__switcher-item rail__switcher-item--current"
                      : "rail__switcher-item"
                  }
                  onClick={() => {
                    setSwitcherOpen(false);
                    if (m.workspace_id !== currentWorkspaceId) {
                      onSwitchWorkspace(m.workspace_id);
                    }
                  }}
                >
                  {m.workspace_name}
                </button>
              ))}
              <button
                type="button"
                role="menuitem"
                className="rail__switcher-item rail__switcher-item--action"
                onClick={() => {
                  setSwitcherOpen(false);
                  onCreateOrJoinAnother();
                }}
              >
                Create or join a workspace…
              </button>
            </div>
          )}
        </div>

        <div className="rail__channels-header">
          <span>Channels</span>
          <button
            type="button"
            className="rail__create-channel-toggle"
            aria-label="Create channel"
            onClick={() => setShowCreateChannel((v) => !v)}
          >
            +
          </button>
        </div>

        {showCreateChannel && (
          <form className="rail__create-channel-form" onSubmit={submitCreateChannel}>
            <label htmlFor="rail-new-channel-name">Channel name</label>
            <input
              id="rail-new-channel-name"
              value={newChannelName}
              onChange={(event) => setNewChannelName(event.target.value)}
              autoFocus
            />
            <button type="submit">Create</button>
          </form>
        )}

        <ul className="rail__channel-list">
          {channels.map((channel) => {
            const row = unreads[channel.channel_id];
            const unreadCount = row?.unread_count ?? 0;
            const mentionCount = row?.mention_count ?? 0;
            const active = channel.channel_id === currentChannelId;
            return (
              <li key={channel.channel_id}>
                <button
                  type="button"
                  className={active ? "rail__channel rail__channel--active" : "rail__channel"}
                  onClick={() => onSelectChannel(channel.channel_id)}
                >
                  <span className="rail__channel-name">#{channel.channel_name}</span>
                  {mentionCount > 0 && (
                    <span className="rail__channel-bell" aria-label={`${mentionCount} mentions`}>
                      🔔 {mentionCount}
                    </span>
                  )}
                  {unreadCount > 0 && (
                    <span className="rail__channel-badge" aria-label={`${unreadCount} unread`}>
                      {unreadCount}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>

        <div className="rail__you">
          <button
            type="button"
            className="rail__you-button"
            onClick={() => onSetYouMenuOpen(!youMenuOpen)}
            aria-expanded={youMenuOpen}
          >
            <span className="rail__you-avatar" aria-hidden="true">
              {self ? initialsFor(self.member_name, self.handle) : ""}
            </span>
            <span className="rail__you-handle">@{self?.handle ?? "…"}</span>
          </button>
          {youMenuOpen && (
            <div className="rail__you-menu" role="menu">
              {self && (
                <div className="rail__whoami-card" data-testid="whoami-card">
                  <p className="rail__whoami-name">{self.member_name}</p>
                  <p className="rail__whoami-handle">@{self.handle}</p>
                  <p className="rail__whoami-meta">
                    {workspaceName}
                    {ROLE_LABELS[self.role] ? ` · ${ROLE_LABELS[self.role]}` : ""}
                  </p>
                </div>
              )}
              <button type="button" role="menuitem" onClick={onOpenSettings}>
                Settings
              </button>
              <button type="button" role="menuitem" onClick={onToggleTheme}>
                {theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              </button>
              <button type="button" role="menuitem" onClick={onLogout}>
                Log out
              </button>
            </div>
          )}
        </div>
      </nav>
    </>
  );
}
