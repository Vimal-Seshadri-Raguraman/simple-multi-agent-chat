import { useState } from "react";
import { Cap } from "../lib/capabilities";
import { useWorkspace } from "../state/workspace";
import AgentsPanel from "./AgentsPanel";
import InvitesPanel from "./InvitesPanel";
import MembersAdminPanel from "./MembersAdminPanel";
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
 * **Capability gating (SMAC-92 Task 4, replaces the old `is_admin`-only
 * check):** every tab is computed from `workspace.hasCap(...)`, omitted
 * from the tab list entirely for a caller who lacks it -- not just
 * disabled -- so there is no client-rendered gated control a caller can
 * reach at all (the server's `require_cap` wall is still the real gate;
 * this is belt-and-suspenders UI hygiene, constitution §7.5). Per the
 * task-4 brief:
 *  - Agents: always present (`Cap.VIEW_AGENTS` is a baseline cap every
 *    role has) -- `Cap.MANAGE_AGENTS` decides whether it's the full panel
 *    or read-only (`AgentsPanel`'s own `readOnly` prop).
 *  - Invites: present if the caller holds EITHER mint cap (`Cap.
 *    MINT_HUMAN_INVITES` or `Cap.MINT_AGENT_INVITES`) -- `InvitesPanel`
 *    itself re-checks per-section, since an `agent_admin` holds only the
 *    agent one.
 *  - Members: present if the caller holds `Cap.ASSIGN_ROLES` or `Cap.
 *    REMOVE_MEMBERS` -- `MembersAdminPanel` (SMAC-92 Task 5) re-checks
 *    each capability independently, since a caller can hold just one of
 *    the two (role `<select>`s need `assign_roles`; the Remove flow needs
 *    `remove_members`).
 *  - Workspace: present only with `Cap.MANAGE_WORKSPACE`.
 */

export type SettingsSection = "agents" | "invites" | "members" | "workspace";

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
  members: "Members",
  workspace: "Workspace",
};

export default function Settings({ onBack, workspaceName, initialSection = "agents" }: SettingsProps) {
  const workspace = useWorkspace();
  const canManageAgents = workspace.hasCap(Cap.MANAGE_AGENTS);
  const canMintHuman = workspace.hasCap(Cap.MINT_HUMAN_INVITES);
  const canMintAgent = workspace.hasCap(Cap.MINT_AGENT_INVITES);
  const canManageMembers =
    workspace.hasCap(Cap.ASSIGN_ROLES) || workspace.hasCap(Cap.REMOVE_MEMBERS);
  const canManageWorkspace = workspace.hasCap(Cap.MANAGE_WORKSPACE);

  const sections: SettingsSection[] = ["agents"];
  if (canMintHuman || canMintAgent) sections.push("invites");
  if (canManageMembers) sections.push("members");
  if (canManageWorkspace) sections.push("workspace");
  // Requested tab, tracked as-is (not gated at mount time): `workspace.self`
  // is still `null` on Settings' very first render (`whoami()` hasn't
  // resolved yet, `state/workspace.tsx`'s own mount-effect), so gating
  // `initialSection` against the capability booleans above inside a
  // `useState` initializer would freeze in whatever they happened to be at
  // that first render -- always `false` -- and permanently downgrade an
  // admin's `initialSection="workspace"` (`/workspace delete`) to "agents"
  // the instant `self` loads a page later. Gating happens at RENDER time
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
            <AgentsPanel
              members={workspace.members}
              onRefresh={workspace.refreshMembers}
              readOnly={!canManageAgents}
            />
          )}
          {activeSection === "invites" && (
            <InvitesPanel canMintHuman={canMintHuman} canMintAgent={canMintAgent} />
          )}
          {activeSection === "members" && canManageMembers && (
            <MembersAdminPanel
              members={workspace.members}
              self={workspace.self}
              canAssignRoles={workspace.hasCap(Cap.ASSIGN_ROLES)}
              canRemoveMembers={workspace.hasCap(Cap.REMOVE_MEMBERS)}
              onRefresh={workspace.refreshMembers}
              onRefreshSelf={workspace.refreshWhoami}
            />
          )}
          {activeSection === "workspace" && canManageWorkspace && (
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
