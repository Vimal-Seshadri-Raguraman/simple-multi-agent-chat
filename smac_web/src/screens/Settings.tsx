import { useState } from "react";
import { useWorkspace } from "../state/workspace";
import AgentsPanel from "./AgentsPanel";
import InvitesPanel from "./InvitesPanel";
import WorkspacePanel from "./WorkspacePanel";

/**
 * Settings -- "the administration home" (web spec §2, constitution §6's
 * Web surface signature): the real Agents/Invites/Workspace panels this
 * task builds, replacing task-3's placeholder wholesale. Reached only via
 * the command palette (`lib/commands.ts`'s `/invite`/`/workspace delete`,
 * plus any `goToSettings()` call with no section, which defaults to
 * Agents) -- there is no separate top-level route; this screen fully
 * REPLACES the authed shell while open (`AuthedShell.tsx`'s early
 * return), same as the task-3 stub did.
 *
 * Reads `useWorkspace()` directly (it's rendered inside the SAME
 * `<WorkspaceProvider>` the shell uses -- `AuthedShell.tsx`'s early
 * return happens after that provider mounts) rather than having
 * `AuthedShell` thread `members`/`self` down as props, matching how
 * `CreateOrJoin`/`JoinScreen` read `useAuth()` directly instead of taking
 * auth state as props.
 *
 * Admin gating (web spec §2: visibility toggle + delete are admin-only;
 * task-5 brief: "whoami for admin gating of the workspace panel"): the
 * Workspace tab itself is omitted from the tab list for a non-admin --
 * not just disabled -- so there is no client-rendered admin-only control
 * a non-admin can reach at all (the server's `require_workspace_admin`
 * wall is still the real gate; this is belt-and-suspenders UI hygiene,
 * constitution §7.5).
 */

export type SettingsSection = "agents" | "invites" | "workspace";

export type SettingsProps = {
  onBack: () => void;
  /** The current workspace's display name -- `WorkspacePanel`'s delete
   * confirmation needs it (constitution §3: destructive confirmation is
   * the typed word `delete` + the entity's name); `AgentsPanel`/
   * `InvitesPanel` don't need it. */
  workspaceName: string;
  /** Which panel to land on when Settings first mounts (a palette command
   * picked a specific one, e.g. `/invite` -> `"invites"`). Defaults to
   * Agents, Settings' own first tab. Only consulted once, at mount --
   * subsequent tab switches are this component's own local state, exactly
   * like `initialQuery` on `components/Palette.tsx`. */
  initialSection?: SettingsSection;
};

const SECTION_LABELS: Record<SettingsSection, string> = {
  agents: "Agents",
  invites: "Invites",
  workspace: "Workspace",
};

export default function Settings({ onBack, workspaceName, initialSection = "agents" }: SettingsProps) {
  const workspace = useWorkspace();
  const isAdmin = workspace.self?.is_admin === true;
  const sections: SettingsSection[] = isAdmin
    ? ["agents", "invites", "workspace"]
    : ["agents", "invites"];
  // Requested tab, tracked as-is (not gated at mount time): `workspace.self`
  // is still `null` on Settings' very first render (`whoami()` hasn't
  // resolved yet, `state/workspace.tsx`'s own mount-effect), so gating
  // `initialSection` against `isAdmin` inside a `useState` initializer
  // would freeze in whatever `isAdmin` happened to be at that first
  // render -- always `false` -- and permanently downgrade an admin's
  // `initialSection="workspace"` (`/workspace delete`) to "agents" the
  // instant `self` loads a page later. Gating happens at RENDER time
  // instead, below, against whatever `sections` currently allows.
  const [section, setSection] = useState<SettingsSection>(initialSection);
  const activeSection = sections.includes(section) ? section : sections[0];

  return (
    <div className="settings">
      <header className="settings__header">
        <h1>Settings</h1>
        <button type="button" className="btn btn--quiet" onClick={onBack}>
          Back to the room
        </button>
      </header>
      <div className="settings__body">
        <nav className="settings__tabs" aria-label="Settings sections">
          {sections.map((key) => (
            <button
              key={key}
              type="button"
              className={
                activeSection === key ? "settings__tab settings__tab--active" : "settings__tab"
              }
              aria-current={activeSection === key ? "true" : undefined}
              onClick={() => setSection(key)}
            >
              {SECTION_LABELS[key]}
            </button>
          ))}
        </nav>
        <div className="settings__panel">
          {activeSection === "agents" && (
            <AgentsPanel members={workspace.members} onRefresh={workspace.refreshMembers} />
          )}
          {activeSection === "invites" && <InvitesPanel />}
          {activeSection === "workspace" && isAdmin && (
            <WorkspacePanel
              workspaceName={workspaceName}
              initialVisibility={
                workspace.self?.workspace_visibility === "public" ||
                workspace.self?.workspace_visibility === "private"
                  ? workspace.self.workspace_visibility
                  : null
              }
            />
          )}
        </div>
      </div>
    </div>
  );
}
