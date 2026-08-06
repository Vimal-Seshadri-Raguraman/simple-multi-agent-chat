/**
 * The web client's mirror of `app/capabilities.py`'s `Cap` enum + role
 * table (SMAC-92 Task 4). Deliberately NOT re-deriving capabilities from
 * `role` here -- `MemberSelfOut.capabilities` (the server's own
 * `caps_for(member)` output, `app/schemas.py`) is the single source of
 * truth this client renders from, exactly like the TUI/MCP are meant to.
 * This file only names the capability strings so call sites don't
 * hand-type them, plus the tiny pure `hasCap` helper and the role ->
 * display-label map `MembersPanel`/`Rail` use for badges.
 */

/** Capability name constants, spelled exactly like `Cap.value` on the
 * server (`app/capabilities.py`) -- string equality is the wire contract,
 * there is no enum on this side. */
export const Cap = {
  POST: "post",
  READ: "read",
  ACK_MENTIONS: "ack_mentions",
  CREATE_CHANNELS: "create_channels",
  VIEW_MEMBERS: "view_members",
  VIEW_AGENTS: "view_agents",
  MINT_HUMAN_INVITES: "mint_human_invites",
  MINT_AGENT_INVITES: "mint_agent_invites",
  MANAGE_AGENTS: "manage_agents",
  MANAGE_WORKSPACE: "manage_workspace",
  ASSIGN_ROLES: "assign_roles",
  REMOVE_MEMBERS: "remove_members",
} as const;

export type CapName = (typeof Cap)[keyof typeof Cap];

/** Pure membership check -- `undefined`/`null` (whoami hasn't resolved
 * yet) behaves as "no capabilities", never throws. */
export function hasCap(capabilities: string[] | null | undefined, cap: string): boolean {
  return (capabilities ?? []).includes(cap);
}

/** UI display names for roles (task-4 brief's Global Constraints): the
 * baseline `member` role is intentionally absent -- it gets no badge
 * anywhere (`MembersPanel`'s plain-member rows, the Rail whoami card). */
export const ROLE_LABELS: Record<string, string> = {
  admin: "Workspace Admin",
  agent_admin: "Agent Admin",
};
