import { useCallback, useEffect, useState } from "react";
import * as api from "../lib/api";
import type { Membership } from "../lib/api";
import type { CommandContext } from "../lib/commands";
import { useAuth } from "../state/auth";
import { WorkspaceProvider, useWorkspace } from "../state/workspace";
import Settings from "../screens/Settings";
import "../styles/shell.css";
import Drawer from "./Drawer";
import MembersPanel from "./MembersPanel";
import Palette from "./Palette";
import Rail from "./Rail";
import Room from "./Room";

type Theme = "light" | "dark";

export type AuthedShellProps = {
  theme: Theme;
  onToggleTheme: () => void;
};

/**
 * The authed daily-driver shell (web spec §2 / §5's layout grammar): Rail
 * -> Room -> Drawer, with the Cmd-K command palette floating above all of
 * it. Owns exactly the cross-cutting UI state no single child owns alone
 * (which drawer panel is open, whether the palette is open and with what
 * prefilter, the Settings-stub toggle) and wires the workspace store's
 * data down into each piece. Wrapped in its own `<WorkspaceProvider>`,
 * keyed on the current workspace id: `WorkspaceProvider`'s data-loading
 * effect only runs once per MOUNT (empty deps -- task-3 brief's Task-4
 * seam is `refreshUnreads`/`refreshHistory` being re-run on socket
 * reconnect, not this effect re-running on every render), so switching
 * workspaces from the Rail's switcher menu -- which changes
 * `auth.session.workspaceId` WITHOUT unmounting `AuthedShell` itself --
 * needs the `key` to force a fresh provider instance (the same
 * "remount resets state" pattern `Room.tsx` uses for `Feed` per channel).
 * Without it, the shell would keep showing the PREVIOUS workspace's
 * channels/members/messages after switching.
 */
export default function AuthedShell(props: AuthedShellProps) {
  const auth = useAuth();
  return (
    <WorkspaceProvider key={auth.session?.workspaceId ?? "none"}>
      <ShellBody {...props} />
    </WorkspaceProvider>
  );
}

function ShellBody({ theme, onToggleTheme }: AuthedShellProps) {
  const auth = useAuth();
  const workspace = useWorkspace();
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [youMenuOpen, setYouMenuOpen] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState("");

  const currentWorkspaceId = auth.session?.workspaceId;

  // The Rail workspace switcher's "your memberships" list -- re-fetched
  // whenever the entered workspace changes (including right after this
  // effect's own `enterWorkspace` calls below land a new session).
  useEffect(() => {
    let cancelled = false;
    api
      .accountMe()
      .then((data) => {
        if (!cancelled) setMemberships(data.memberships);
      })
      .catch(() => {
        // Best-effort -- the switcher just shows the current workspace alone.
      });
    return () => {
      cancelled = true;
    };
  }, [currentWorkspaceId]);

  // Cmd-K / Ctrl-K opens the palette from anywhere in the shell.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const isCmdK = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k";
      if (isCmdK) {
        event.preventDefault();
        setPaletteQuery("");
        setPaletteOpen((open) => !open);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const openPaletteWithQuery = useCallback((query: string) => {
    setPaletteQuery(query);
    setPaletteOpen(true);
  }, []);

  const buildCommandContext = useCallback(
    (args: string): CommandContext => ({
      args,
      navigateAuthScreen: auth.navigate,
      logout: auth.logout,
      switchChannelByName: (name) => {
        const found = workspace.findChannelByName(name);
        if (found) {
          workspace.selectChannel(found.channel_id);
        }
      },
      createChannel: workspace.createChannel,
      refreshUnreads: workspace.refreshUnreads,
      showWhoami: () => setYouMenuOpen(true),
      goToSettings: () => setShowSettings(true),
    }),
    [auth.navigate, auth.logout, workspace]
  );

  async function handleSwitchWorkspace(workspaceId: string) {
    await api.enterWorkspace(workspaceId);
    const session = api.getSession();
    if (session) {
      auth.workspaceEntered(session);
    }
  }

  if (showSettings) {
    return <Settings onBack={() => setShowSettings(false)} />;
  }

  const currentMembership = memberships.find((m) => m.workspace_id === currentWorkspaceId);
  const workspaceName = currentMembership?.workspace_name ?? "SMAC";
  const currentChannel =
    workspace.channels.find((c) => c.channel_id === workspace.currentChannelId) ?? null;
  const memberById: Record<string, (typeof workspace.members)[number]> = {};
  for (const member of workspace.members) {
    memberById[member.member_id] = member;
  }

  return (
    <div className="shell" data-theme={theme}>
      <Rail
        workspaceName={workspaceName}
        memberships={memberships}
        currentWorkspaceId={currentWorkspaceId}
        onSwitchWorkspace={(id) => void handleSwitchWorkspace(id)}
        onCreateOrJoinAnother={() => auth.navigate("create-or-join")}
        channels={workspace.channels}
        unreads={workspace.unreads}
        currentChannelId={workspace.currentChannelId}
        onSelectChannel={workspace.selectChannel}
        onCreateChannel={(name) => void workspace.createChannel(name)}
        self={workspace.self}
        youMenuOpen={youMenuOpen}
        onSetYouMenuOpen={setYouMenuOpen}
        theme={theme}
        onToggleTheme={onToggleTheme}
        onLogout={() => void auth.logout()}
      />
      <Room
        channel={currentChannel}
        memberCount={workspace.members.length}
        onOpenDrawer={() => setDrawerOpen(true)}
        messages={workspace.messages}
        memberById={memberById}
        currentMemberId={workspace.self?.member_id}
        hasMoreOlder={workspace.hasMoreOlder}
        loadingOlder={workspace.loadingOlder}
        onLoadOlder={() => void workspace.loadOlderMessages()}
        onView={(channelId) => void workspace.markRead(channelId)}
        members={workspace.members}
        channels={workspace.channels}
        onSend={workspace.sendMessage}
        onOpenPalette={openPaletteWithQuery}
      />
      <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="Members">
        <MembersPanel members={workspace.members} self={workspace.self} />
      </Drawer>
      <Palette
        open={paletteOpen}
        initialQuery={paletteQuery}
        onClose={() => setPaletteOpen(false)}
        buildContext={buildCommandContext}
      />
    </div>
  );
}
