import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../lib/api";
import type { Membership } from "../lib/api";
import type { CommandContext } from "../lib/commands";
import { connectBell, connectRoom } from "../lib/live";
import { useAuth } from "../state/auth";
import { ViewportProvider, useViewportTier } from "../state/viewport";
import { WorkspaceProvider, useWorkspace } from "../state/workspace";
import Settings from "../screens/Settings";
import "../styles/shell.css";
import Drawer from "./Drawer";
import MembersPanel from "./MembersPanel";
import Palette from "./Palette";
import Rail from "./Rail";
import Room from "./Room";
import Toast, { useToastQueue } from "./Toast";

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
 * channels/members/messages after switching. `<ViewportProvider>` sits
 * outside that keyed remount -- the mobile/desktop tier (task-4 brief)
 * has nothing to do with which workspace is open and shouldn't reset
 * alongside it.
 */
export default function AuthedShell(props: AuthedShellProps) {
  const auth = useAuth();
  return (
    <ViewportProvider>
      <WorkspaceProvider key={auth.session?.workspaceId ?? "none"}>
        <ShellBody {...props} />
      </WorkspaceProvider>
    </ViewportProvider>
  );
}

function ShellBody({ theme, onToggleTheme }: AuthedShellProps) {
  const auth = useAuth();
  const workspace = useWorkspace();
  const viewportTier = useViewportTier();
  const mobile = viewportTier === "mobile";
  const toastQueue = useToastQueue();
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [youMenuOpen, setYouMenuOpen] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [paletteQuery, setPaletteQuery] = useState("");

  const currentWorkspaceId = auth.session?.workspaceId;

  // -- Live layer (task-4 brief, web spec §3, constitution §5) -----------
  //
  // The current room's socket: reconnected (via the effect's own cleanup
  // + re-run) every time `currentChannelId` changes, never left dangling
  // on the PREVIOUS room's feed after a switch. `onGap` -- fired by
  // `live.ts` after every successful (re)connect, including the very
  // first -- runs the exact "catch-up-then-live" pair the task-3 brief
  // exposed `refreshUnreads`/`refreshHistory` for.
  useEffect(() => {
    const channelId = workspace.currentChannelId;
    if (channelId === null) {
      return;
    }
    const connection = connectRoom(
      channelId,
      (payload) => workspace.appendMessage(channelId, payload),
      () => {
        void workspace.refreshUnreads();
        void workspace.refreshHistory();
      }
    );
    return () => connection.close();
    // Deliberately NOT reacting to `workspace.appendMessage`/`refreshUnreads`/
    // `refreshHistory` identity changes -- this effect's only job is
    // "one live socket per current room", exactly like `workspace.tsx`'s
    // own history-reload effect a few lines below it in that file.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.currentChannelId]);

  // The bell: one connection for the whole authed session (reconnecting
  // on its own on a drop -- never torn down on a channel switch), so the
  // handler reads the CURRENT room out of a ref rather than closing over
  // a `currentChannelId` that would otherwise go stale the moment the
  // reader switches rooms without this effect re-running.
  const currentChannelIdRef = useRef(workspace.currentChannelId);
  useEffect(() => {
    currentChannelIdRef.current = workspace.currentChannelId;
  }, [workspace.currentChannelId]);

  useEffect(() => {
    const connection = connectBell((event) => {
      const eventChannelId = event.message.Channel.channel_id;
      if (eventChannelId === currentChannelIdRef.current) {
        // Already visible in place as an ordinary highlighted message
        // line (constitution §5: "bell for OTHER-room mentions") --
        // ringing the bell too would just duplicate what's on screen.
        return;
      }
      void workspace.refreshUnreads(); // bumps the rail's mention badge
      toastQueue.push(`🔔 New mention in #${event.message.Channel.channel_name}`, {
        onClick: () => {
          setShowSettings(false);
          workspace.selectChannel(eventChannelId);
        },
      });
    });
    return () => connection.close();
    // One bell per mount (see above) -- `workspace`/`toastQueue` are read
    // through refs/stable callbacks, not tracked as effect deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Unreads refetch on window focus (web spec §2's rail bullet, task-4
  // brief line 9 -- fix round 1). A backgrounded tab's bell socket can be
  // silently throttled or dropped by the browser, so returning to the tab
  // needs its own catch-up beyond whatever the socket delivered while
  // away; `VersionBanner.tsx` already does the analogous thing for the
  // `/meta` version poll, on the same `window` "focus" event.
  useEffect(() => {
    function onFocus() {
      void workspace.refreshUnreads();
    }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
    // `workspace.refreshUnreads` is a stable (`useCallback([])`) reference
    // -- see the room-socket effect above for the same non-reactive-deps
    // rationale.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
        onSelectChannel={(channelId) => {
          workspace.selectChannel(channelId);
          if (mobile) setRailOpen(false); // tapping a room closes the drawer behind it
        }}
        onCreateChannel={(name) => void workspace.createChannel(name)}
        self={workspace.self}
        youMenuOpen={youMenuOpen}
        onSetYouMenuOpen={setYouMenuOpen}
        theme={theme}
        onToggleTheme={onToggleTheme}
        onLogout={() => void auth.logout()}
        mobile={mobile}
        open={mobile ? railOpen : true}
        onRequestClose={() => setRailOpen(false)}
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
        mobile={mobile}
        onOpenRail={() => setRailOpen(true)}
      />
      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title="Members"
        mobile={mobile}
      >
        <MembersPanel members={workspace.members} self={workspace.self} />
      </Drawer>
      <Palette
        open={paletteOpen}
        initialQuery={paletteQuery}
        onClose={() => setPaletteOpen(false)}
        buildContext={buildCommandContext}
      />
      <Toast toasts={toastQueue.toasts} onDismiss={toastQueue.dismiss} />
    </div>
  );
}
