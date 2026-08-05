import { type FormEvent, useState } from "react";
import type { ChannelOut, MemberSelfOut, Membership, UnreadsRowOut } from "../lib/api";

/**
 * The left rail (web spec §2): workspace name + switcher (memberships +
 * create/join), the channel list with live unread/mention badges, "+"
 * create channel, and YOU at the bottom (avatar/handle -> menu: whoami
 * card, theme toggle, logout).
 *
 * The YOU menu's open/closed state is lifted to the shell
 * (`AuthedShell.tsx`) rather than owned locally, because the Cmd-K
 * palette's `/whoami` command (constitution §4: "web -> avatar menu")
 * needs to be able to open the exact same menu from outside this
 * component.
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
};

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
}: RailProps) {
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [showCreateChannel, setShowCreateChannel] = useState(false);
  const [newChannelName, setNewChannelName] = useState("");

  function submitCreateChannel(event: FormEvent) {
    event.preventDefault();
    const name = newChannelName.trim();
    if (!name) return;
    onCreateChannel(name);
    setNewChannelName("");
    setShowCreateChannel(false);
  }

  return (
    <nav className="rail" aria-label="Workspace navigation">
      <div className="rail__workspace">
        <button
          type="button"
          className="rail__workspace-name"
          onClick={() => setSwitcherOpen((v) => !v)}
          aria-expanded={switcherOpen}
        >
          {workspaceName}
        </button>
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
          <span className="rail__you-avatar" aria-hidden="true" />
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
                  {self.is_admin ? " · admin" : ""}
                </p>
              </div>
            )}
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
  );
}
