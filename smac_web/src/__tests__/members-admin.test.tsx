import { useState } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import MembersAdminPanel from "../screens/MembersAdminPanel";
import * as api from "../lib/api";
import type { MemberOut, MemberSelfOut } from "../lib/api";
import { SmacError } from "../lib/errors";

/**
 * Task 5: Settings' Members admin panel (SMAC-92) -- replaces the T4
 * placeholder. Mirrors `__tests__/members-panel.test.tsx`'s grouping/
 * avatar/badge fixtures, but this panel calls `api.updateMemberRole`/
 * `api.removeMember` directly, so `lib/api` is mocked here (same
 * convention `settings.test.tsx` uses for `AgentsPanel`/`InvitesPanel`).
 */
vi.mock("../lib/api");

const SELF: MemberSelfOut = {
  member_id: "m1",
  member_name: "Alice Human",
  member_type: "human",
  handle: "alice",
  workspace_id: "ws1",
  account_id: "acc-1",
  created_at: "2026-01-01T00:00:00",
  first_name: null,
  last_name: null,
  company: null,
  occupation: null,
  job_role: null,
  role: "admin",
  capabilities: ["assign_roles", "remove_members"],
  workspace_visibility: "private",
};

function member(overrides: Partial<MemberOut> = {}): MemberOut {
  return {
    member_id: "m2",
    member_name: "Bob Builder",
    member_type: "human",
    handle: "bob",
    created_at: "2026-01-01T00:00:00",
    account_id: "acc-2",
    role: "member",
    ...overrides,
  };
}

const AGENT: MemberOut = {
  member_id: "m3",
  member_name: "Analyst",
  member_type: "agent",
  handle: "analyst",
  created_at: "2026-01-01T00:00:00",
  account_id: "acc-agent",
  role: "member",
};

/** A thin stateful wrapper so `onRefresh`/`onRefreshSelf` drive a REAL
 * re-render off a fresh `api.members()`/`api.whoami()` fetch, the same
 * seam `Settings.tsx` wires in production, rather than asserting only
 * that the callbacks were invoked. */
function Harness({
  initialMembers,
  self = SELF,
  canAssignRoles = true,
  canRemoveMembers = true,
}: {
  initialMembers: MemberOut[];
  self?: MemberSelfOut | null;
  canAssignRoles?: boolean;
  canRemoveMembers?: boolean;
}) {
  const [members, setMembers] = useState(initialMembers);

  async function onRefresh() {
    const fresh = await api.members();
    setMembers(fresh);
  }
  async function onRefreshSelf() {
    await api.whoami();
  }

  return (
    <MembersAdminPanel
      members={members}
      self={self}
      canAssignRoles={canAssignRoles}
      canRemoveMembers={canRemoveMembers}
      onRefresh={onRefresh}
      onRefreshSelf={onRefreshSelf}
    />
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("MembersAdminPanel grouping + role controls (SMAC-92 Task 5)", () => {
  it("groups humans and agents, giving humans a role dropdown and agents a badge (no dropdown)", () => {
    const bob = member({ role: "admin" });
    render(<Harness initialMembers={[SELF, bob, AGENT]} />);

    expect(screen.getByText("Humans")).toBeInTheDocument();
    expect(screen.getByText("Agents")).toBeInTheDocument();
    expect(screen.getByLabelText("Role for @bob")).toBeInTheDocument();
    expect(screen.queryByLabelText("Role for @analyst")).not.toBeInTheDocument();
    // AGENT's role is baseline "member" here, so it gets no badge either
    // (ROLE_LABELS has no "member" entry) -- same convention MembersPanel
    // already uses, just proving the dropdown itself is truly absent.
  });

  it("changing a human's role select calls updateMemberRole, then re-renders the fresh role", async () => {
    const bob = member({ role: "member" });
    vi.mocked(api.updateMemberRole).mockResolvedValue({ ...bob, role: "admin" });
    vi.mocked(api.members).mockResolvedValue([SELF, { ...bob, role: "admin" }]);
    vi.mocked(api.whoami).mockResolvedValue(SELF);

    render(<Harness initialMembers={[SELF, bob]} />);

    const select = screen.getByLabelText("Role for @bob") as HTMLSelectElement;
    expect(select.value).toBe("member");
    fireEvent.change(select, { target: { value: "admin" } });

    expect(api.updateMemberRole).toHaveBeenCalledWith("m2", "admin");
    await waitFor(() => expect(api.members).toHaveBeenCalled());
    await waitFor(() => expect(api.whoami).toHaveBeenCalled());
    await waitFor(() => expect(select.value).toBe("admin"));
  });

  it("a role-change 403 (capability_denied) surfaces inline as the envelope message", async () => {
    const bob = member({ role: "member" });
    vi.mocked(api.updateMemberRole).mockRejectedValue(
      new SmacError("capability_denied", "This action requires assign_roles.")
    );

    render(<Harness initialMembers={[SELF, bob]} />);
    fireEvent.change(screen.getByLabelText("Role for @bob"), { target: { value: "admin" } });

    await screen.findByText("This action requires assign_roles.");
  });

  it("hides the role select (shows the badge instead) when the caller lacks assign_roles", () => {
    const bob = member({ role: "admin" });
    render(<Harness initialMembers={[SELF, bob]} canAssignRoles={false} />);

    expect(screen.queryByLabelText("Role for @bob")).not.toBeInTheDocument();
    expect(screen.getByText("@bob").closest("li")).toHaveTextContent("Workspace Admin");
  });
});

describe("MembersAdminPanel: self row + remove gating (SMAC-92 Task 5)", () => {
  it("marks the self row '(you)' and renders NO remove button for it", () => {
    const bob = member();
    render(<Harness initialMembers={[SELF, bob]} />);

    const selfRow = screen.getByText("@alice").closest("li")!;
    expect(selfRow).toHaveTextContent("(you)");
    expect(within(selfRow).queryByRole("button", { name: /remove/i })).not.toBeInTheDocument();

    const bobRow = screen.getByText("@bob").closest("li")!;
    expect(within(bobRow).getByRole("button", { name: "Remove" })).toBeInTheDocument();
  });

  it("hides every Remove control when the caller lacks remove_members", () => {
    const bob = member();
    render(<Harness initialMembers={[SELF, bob]} canRemoveMembers={false} />);
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  });
});

describe("MembersAdminPanel: typed-confirmation remove flow (constitution §3, mirrors WorkspacePanel)", () => {
  it("only enables the danger Remove button once BOTH the exact @handle and the word 'remove' are typed", () => {
    const bob = member();
    render(<Harness initialMembers={[SELF, bob]} />);

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    const dangerButton = screen.getByRole("button", { name: "Remove @bob" });
    expect(dangerButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/type @bob to confirm/i), {
      target: { value: "carol" }, // wrong handle
    });
    fireEvent.change(screen.getByLabelText(/then type/i), { target: { value: "remove" } });
    expect(dangerButton).toBeDisabled();

    // Reset the word field before proving "correct handle alone" isn't
    // enough either -- otherwise the "remove" typed above would still be
    // sitting in that field and both conditions would already be true.
    fireEvent.change(screen.getByLabelText(/then type/i), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText(/type @bob to confirm/i), {
      target: { value: "bob" },
    });
    expect(dangerButton).toBeDisabled(); // handle alone isn't enough

    fireEvent.change(screen.getByLabelText(/then type/i), { target: { value: "wrong" } });
    expect(dangerButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/then type/i), { target: { value: "remove" } });
    expect(dangerButton).not.toBeDisabled();
  });

  it("confirmed remove calls api.removeMember, then refreshes members + whoami", async () => {
    const bob = member();
    vi.mocked(api.removeMember).mockResolvedValue({ status: "removed" });
    vi.mocked(api.members).mockResolvedValue([SELF]);
    vi.mocked(api.whoami).mockResolvedValue(SELF);

    render(<Harness initialMembers={[SELF, bob]} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.change(screen.getByLabelText(/type @bob to confirm/i), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText(/then type/i), { target: { value: "remove" } });
    fireEvent.click(screen.getByRole("button", { name: "Remove @bob" }));

    await waitFor(() => expect(api.removeMember).toHaveBeenCalledWith("m2"));
    await waitFor(() => expect(api.members).toHaveBeenCalled());
    await waitFor(() => expect(api.whoami).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText("@bob")).not.toBeInTheDocument());
  });

  it("a last_admin 409 on remove surfaces inline as the envelope message (member stays listed)", async () => {
    const bob = member({ role: "admin" });
    vi.mocked(api.removeMember).mockRejectedValue(
      new SmacError("last_admin", "Cannot remove the workspace's only admin")
    );

    render(<Harness initialMembers={[SELF, bob]} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.change(screen.getByLabelText(/type @bob to confirm/i), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText(/then type/i), { target: { value: "remove" } });
    fireEvent.click(screen.getByRole("button", { name: "Remove @bob" }));

    await screen.findByText("Cannot remove the workspace's only admin");
    // Never optimistically removed -- one of the row handle spans (scoped
    // by class, since the still-open confirmation form's "Type @bob to
    // confirm" label also contains the literal text "@bob") is still bob's.
    const handles = Array.from(document.querySelectorAll(".members-admin__handle"));
    expect(handles.some((el) => el.textContent?.includes("@bob"))).toBe(true);
  });

  it("submitting the confirmation form with a mismatched handle never calls removeMember (Enter-safe guard)", async () => {
    const bob = member();
    render(<Harness initialMembers={[SELF, bob]} />);

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.change(screen.getByLabelText(/type @bob to confirm/i), { target: { value: "wrong" } });
    fireEvent.change(screen.getByLabelText(/then type/i), { target: { value: "remove" } });

    const form = screen.getByLabelText(/then type/i).closest("form")!;
    fireEvent.submit(form);

    // The submit handler's own guard re-checks the match; a mismatched
    // handle must never call removeMember, Enter or not -- the disabled
    // attribute on the button is belt-and-suspenders, not the only gate.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(api.removeMember).not.toHaveBeenCalled();
  });
});
