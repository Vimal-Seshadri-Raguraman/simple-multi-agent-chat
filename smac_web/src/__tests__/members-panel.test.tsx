import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import MembersPanel from "../components/MembersPanel";
import type { MemberOut, MemberSelfOut } from "../lib/api";

/**
 * The Drawer's members panel role badges (SMAC-92 Task 4, closes the
 * task-3 report's follow-up): `GET /workspaces/{id}/members` now carries
 * `role` for every member, not just the caller's own, so a badge with the
 * UI display name (`lib/capabilities.ts`'s `ROLE_LABELS`) renders for
 * ANY member holding a non-baseline role -- not just the viewer's own row,
 * which is all the old `is_admin`-only wire contract allowed.
 */

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
  capabilities: [],
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

describe("MembersPanel role badges (task-4 brief, SMAC-92)", () => {
  it("shows a 'Workspace Admin' badge for an admin member -- not just the viewer's own row", () => {
    const other = member({ member_id: "m2", handle: "bob", role: "admin" });
    render(<MembersPanel members={[SELF, other]} self={SELF} />);
    const row = screen.getByText("@bob").closest("li")!;
    expect(row).toHaveTextContent("Workspace Admin");
  });

  it("shows an 'Agent Admin' badge for an agent_admin member", () => {
    const other = member({ member_id: "m2", handle: "carol", role: "agent_admin" });
    render(<MembersPanel members={[other]} self={null} />);
    const row = screen.getByText("@carol").closest("li")!;
    expect(row).toHaveTextContent("Agent Admin");
  });

  it("shows no badge at all for a plain member", () => {
    const other = member({ member_id: "m2", handle: "dave", role: "member" });
    render(<MembersPanel members={[other]} self={null} />);
    const row = screen.getByText("@dave").closest("li")!;
    expect(row).not.toHaveTextContent("Workspace Admin");
    expect(row).not.toHaveTextContent("Agent Admin");
  });

  it("marks the viewer's own row with '(you)', independent of the badge", () => {
    render(<MembersPanel members={[SELF]} self={SELF} />);
    const rows = screen.getAllByRole("listitem");
    const row = rows.find((r) => r.textContent?.includes("@alice"));
    expect(row).toBeDefined();
    expect(row).toHaveTextContent("(you)");
    expect(row).toHaveTextContent("Workspace Admin"); // SELF is an admin fixture
  });
});
