import type { MemberOut, MemberSelfOut } from "../lib/api";
import { initialsFor } from "../lib/avatar";
import { ROLE_LABELS } from "../lib/capabilities";

/**
 * The Drawer's members panel (web spec §2 / constitution §6): every
 * member grouped by type (humans, agents, and any future type -- "bot" is
 * named in the constitution's glossary but the server doesn't yet mint
 * any member with that type, so the grouping is generic rather than a
 * hardcoded human/agent pair), handles, agent-color rings.
 *
 * **Role badges (SMAC-92 Task 4, closes the task-3 report's follow-up):**
 * `GET /workspaces/{id}/members` (`MemberOut`) now carries `role` for
 * EVERY member, not just the caller's own -- roles are public
 * transparency (spec §3), only *managing* them is gated. Every member
 * whose `role` isn't the baseline `"member"` gets a badge with its UI
 * display name (`lib/capabilities.ts`'s `ROLE_LABELS`); plain members get
 * no badge at all, matching the old admin-only-mark convention this
 * replaces.
 */

const TYPE_ORDER = ["human", "agent"];

function sortTypes(types: string[]): string[] {
  return [...types].sort((a, b) => {
    const ai = TYPE_ORDER.indexOf(a);
    const bi = TYPE_ORDER.indexOf(b);
    if (ai === -1 && bi === -1) return a.localeCompare(b);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });
}

function pluralGroupLabel(type: string): string {
  if (type === "human") return "Humans";
  if (type === "agent") return "Agents";
  return `${type.charAt(0).toUpperCase()}${type.slice(1)}s`;
}

export type MembersPanelProps = {
  members: MemberOut[];
  self: MemberSelfOut | null;
};

export default function MembersPanel({ members, self }: MembersPanelProps) {
  const byType = new Map<string, MemberOut[]>();
  for (const member of members) {
    const list = byType.get(member.member_type) ?? [];
    list.push(member);
    byType.set(member.member_type, list);
  }
  const types = sortTypes([...byType.keys()]);

  return (
    <div className="members-panel">
      {types.map((type) => (
        <section className="members-panel__group" key={type}>
          <h3 className="members-panel__group-label">{pluralGroupLabel(type)}</h3>
          <ul className="members-panel__list">
            {(byType.get(type) ?? []).map((member) => {
              const isSelf = self !== null && member.member_id === self.member_id;
              const isAgent = member.member_type !== "human";
              const roleLabel = ROLE_LABELS[member.role];
              return (
                <li
                  key={member.member_id}
                  className={
                    isAgent
                      ? "members-panel__member members-panel__member--agent"
                      : "members-panel__member"
                  }
                >
                  <span className="members-panel__avatar-ring" aria-hidden="true">
                    {initialsFor(member.member_name, member.handle)}
                  </span>
                  <span className="members-panel__handle">
                    @{member.handle}
                    {isSelf && <span className="members-panel__you-mark"> (you)</span>}
                  </span>
                  {roleLabel && <span className="members-panel__role-badge">{roleLabel}</span>}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      {members.length === 0 && <p className="members-panel__empty">No members yet.</p>}
    </div>
  );
}
