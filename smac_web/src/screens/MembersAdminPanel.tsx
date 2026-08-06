import { useState } from "react";
import * as api from "../lib/api";
import type { MemberOut, MemberSelfOut } from "../lib/api";
import { initialsFor } from "../lib/avatar";
import { ROLE_LABELS } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";

/**
 * Settings' Members panel (SMAC-92 Task 5, replaces Task 4's placeholder):
 * every member grouped humans/agents, same grouping+avatar-ring pattern as
 * the Drawer's `components/MembersPanel.tsx` (deliberately NOT shared as a
 * component -- that one is read-only chrome for every viewer; this one
 * adds mutation controls only a caller with `Cap.ASSIGN_ROLES`/`Cap.
 * REMOVE_MEMBERS` ever reaches, per `Settings.tsx`'s tab gating).
 *
 * **Role changes:** a HUMAN member gets a role `<select>` (agents are a
 * member TYPE, not a role -- the server rejects assigning a role to one,
 * `app/routers/workspaces.py::update_member_role`'s `ForbiddenMemberTypeError`
 * -- so agents render their badge only, never a dropdown, matching the
 * wall exactly). The select is uncontrolled-by-us beyond `value=
 * {member.role}`: on success this component calls both `onRefresh`
 * (member list) and `onRefreshSelf` (whoami) and lets the fresh props
 * drive the next render -- there's no local optimistic mutation to
 * roll back if the server rejects it (e.g. `last_admin` demoting the
 * workspace's only admin), so a failed change just leaves the select
 * showing its still-accurate previous value, with the error text below it.
 *
 * **Removal (constitution §3's destructive-confirmation rule, mirrors
 * `WorkspacePanel.tsx`'s typed-delete exactly):** clicking "Remove" opens
 * an inline typed-confirmation form for THAT row only (one at a time,
 * same "only one mode open" convention `AgentsPanel`'s create/attach
 * toggle uses) -- the danger button only enables once the typed text
 * matches the target's `@handle` exactly AND the typed word is "remove"
 * (case-insensitive, trimmed). The self row never gets a Remove control
 * at all (the server's own `SelfRemovalError` wall says use workspace
 * deletion or transfer admin first -- omitting the control here is
 * belt-and-suspenders UI hygiene, not the real gate).
 *
 * **Self-affecting changes (task-5 brief, mandatory):** every successful
 * mutation refreshes BOTH the member list and whoami -- an admin who just
 * demoted or removed themselves (self-demotion via the role select IS
 * possible; only self-REMOVAL is blocked) needs their own capabilities to
 * react live, exactly like `state/workspace.tsx`'s `refreshWhoami` doc
 * comment describes. If that leaves the caller without `assign_roles`/
 * `remove_members`, `Settings.tsx`'s own capability-driven tab list drops
 * "Members" on the very next render -- no extra code needed here for that.
 */

const ROLE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "member", label: "Member" },
  { value: "agent_admin", label: "Agent Admin" },
  { value: "admin", label: "Workspace Admin" },
];

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

export type MembersAdminPanelProps = {
  members: MemberOut[];
  self: MemberSelfOut | null;
  /** `Cap.ASSIGN_ROLES` -- decides whether HUMAN rows get a role `<select>`
   * (vs. just their badge, same as an agent row). */
  canAssignRoles: boolean;
  /** `Cap.REMOVE_MEMBERS` -- decides whether non-self rows get a Remove
   * control at all. */
  canRemoveMembers: boolean;
  /** Re-fetch the member directory (`state/workspace.tsx`'s `refreshMembers`). */
  onRefresh: () => Promise<void>;
  /** Re-fetch the caller's own profile (`refreshWhoami`) -- see this
   * file's module docstring on why every mutation calls this too. */
  onRefreshSelf: () => Promise<void>;
};

export default function MembersAdminPanel({
  members,
  self,
  canAssignRoles,
  canRemoveMembers,
  onRefresh,
  onRefreshSelf,
}: MembersAdminPanelProps) {
  const [rolePending, setRolePending] = useState<Record<string, boolean>>({});
  const [roleError, setRoleError] = useState<Record<string, string | null>>({});

  const [removingId, setRemovingId] = useState<string | null>(null);
  const [removeHandleInput, setRemoveHandleInput] = useState("");
  const [removeWordInput, setRemoveWordInput] = useState("");
  const [removePending, setRemovePending] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);

  async function handleRoleChange(member: MemberOut, role: string) {
    setRoleError((prev) => ({ ...prev, [member.member_id]: null }));
    setRolePending((prev) => ({ ...prev, [member.member_id]: true }));
    try {
      await api.updateMemberRole(member.member_id, role);
      await Promise.all([onRefresh(), onRefreshSelf()]);
    } catch (err) {
      setRoleError((prev) => ({ ...prev, [member.member_id]: errorMessage(err) }));
    } finally {
      setRolePending((prev) => ({ ...prev, [member.member_id]: false }));
    }
  }

  function startRemove(memberId: string) {
    setRemovingId(memberId);
    setRemoveHandleInput("");
    setRemoveWordInput("");
    setRemoveError(null);
  }

  function cancelRemove() {
    setRemovingId(null);
    setRemoveHandleInput("");
    setRemoveWordInput("");
    setRemoveError(null);
  }

  async function confirmRemove(member: MemberOut) {
    const canConfirm =
      removeHandleInput === member.handle &&
      removeWordInput.trim().toLowerCase() === "remove";
    if (!canConfirm) return; // Enter-safe: the guard fires even if the (disabled) button was somehow submitted.
    setRemovePending(true);
    setRemoveError(null);
    try {
      await api.removeMember(member.member_id);
      cancelRemove();
      await Promise.all([onRefresh(), onRefreshSelf()]);
    } catch (err) {
      setRemoveError(errorMessage(err));
      setRemovePending(false);
    }
  }

  const byType = new Map<string, MemberOut[]>();
  for (const member of members) {
    const list = byType.get(member.member_type) ?? [];
    list.push(member);
    byType.set(member.member_type, list);
  }
  const types = sortTypes([...byType.keys()]);

  return (
    <div className="members-admin">
      <h2>Members</h2>
      {types.map((type) => (
        <section className="members-admin__group" key={type}>
          <h3 className="members-admin__group-label">{pluralGroupLabel(type)}</h3>
          <ul className="members-admin__list">
            {(byType.get(type) ?? []).map((member) => {
              const isSelf = self !== null && member.member_id === self.member_id;
              const isAgent = member.member_type !== "human";
              const isRemoving = removingId === member.member_id;
              const canConfirmRemove =
                removeHandleInput === member.handle &&
                removeWordInput.trim().toLowerCase() === "remove";

              return (
                <li key={member.member_id} className="members-admin__row">
                  <div className="members-admin__identity">
                    <span className="members-admin__avatar-ring" aria-hidden="true">
                      {initialsFor(member.member_name, member.handle)}
                    </span>
                    <span className="members-admin__handle">
                      @{member.handle}
                      {isSelf && <span className="members-admin__you-mark"> (you)</span>}
                    </span>
                  </div>

                  <div className="members-admin__role">
                    {!isAgent && canAssignRoles ? (
                      <select
                        aria-label={`Role for @${member.handle}`}
                        className="members-admin__role-select"
                        value={member.role}
                        disabled={rolePending[member.member_id] === true}
                        onChange={(event) => void handleRoleChange(member, event.target.value)}
                      >
                        {ROLE_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>
                            {opt.label}
                          </option>
                        ))}
                      </select>
                    ) : (
                      ROLE_LABELS[member.role] && (
                        <span className="members-admin__role-badge">
                          {ROLE_LABELS[member.role]}
                        </span>
                      )
                    )}
                    {roleError[member.member_id] && (
                      <p role="alert" className="members-admin__error">
                        {roleError[member.member_id]}
                      </p>
                    )}
                  </div>

                  {canRemoveMembers && !isSelf && (
                    <div className="members-admin__remove">
                      {isRemoving ? (
                        <form
                          className="members-admin__remove-form"
                          onSubmit={(event) => {
                            event.preventDefault();
                            void confirmRemove(member);
                          }}
                        >
                          <label htmlFor={`members-admin-remove-handle-${member.member_id}`}>
                            Type <span className="mono">@{member.handle}</span> to confirm
                          </label>
                          <input
                            id={`members-admin-remove-handle-${member.member_id}`}
                            value={removeHandleInput}
                            onChange={(event) => setRemoveHandleInput(event.target.value)}
                            autoComplete="off"
                          />
                          <label htmlFor={`members-admin-remove-word-${member.member_id}`}>
                            Then type <span className="mono">remove</span>
                          </label>
                          <input
                            id={`members-admin-remove-word-${member.member_id}`}
                            value={removeWordInput}
                            onChange={(event) => setRemoveWordInput(event.target.value)}
                            autoComplete="off"
                          />
                          {removeError && (
                            <p role="alert" className="members-admin__error">
                              {removeError}
                            </p>
                          )}
                          <div className="members-admin__remove-actions">
                            <button
                              type="submit"
                              className="btn btn--danger btn--sm"
                              disabled={!canConfirmRemove || removePending}
                            >
                              {removePending ? "Removing…" : `Remove @${member.handle}`}
                            </button>
                            <button
                              type="button"
                              className="btn btn--quiet btn--sm"
                              onClick={cancelRemove}
                              disabled={removePending}
                            >
                              Cancel
                            </button>
                          </div>
                        </form>
                      ) : (
                        <button
                          type="button"
                          className="btn btn--quiet btn--sm"
                          onClick={() => startRemove(member.member_id)}
                        >
                          Remove
                        </button>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      {members.length === 0 && <p className="members-admin__empty">No members yet.</p>}
    </div>
  );
}
