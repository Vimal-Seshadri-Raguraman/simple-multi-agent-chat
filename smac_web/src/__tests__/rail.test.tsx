import type { ComponentProps } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Rail from "../components/Rail";
import type { ChannelOut, MemberSelfOut, Membership, UnreadsRowOut } from "../lib/api";

const CHANNELS: ChannelOut[] = [
  { channel_id: "c1", channel_name: "general" },
  { channel_id: "c2", channel_name: "reports" },
];

const UNREADS: Record<string, UnreadsRowOut> = {
  c1: { channel_id: "c1", channel_name: "general", unread_count: 3, first_unread_message_id: "m1", mention_count: 0 },
  c2: { channel_id: "c2", channel_name: "reports", unread_count: 5, first_unread_message_id: "m2", mention_count: 2 },
};

const MEMBERSHIPS: Membership[] = [
  { workspace_id: "w1", workspace_name: "Acme", member_id: "m1", handle: "alice" },
  { workspace_id: "w2", workspace_name: "Widgets Co", member_id: "m9", handle: "alice2" },
];

const SELF: MemberSelfOut = {
  member_id: "m1",
  member_name: "Alice Human",
  member_type: "human",
  handle: "alice",
  workspace_id: "w1",
  account_id: "acc-1",
  created_at: "2026-01-01T00:00:00",
  first_name: "Alice",
  last_name: "Human",
  company: null,
  occupation: null,
  job_role: null,
  role: "admin",
  capabilities: [],
  workspace_visibility: "private",
};

function renderRail(overrides: Partial<ComponentProps<typeof Rail>> = {}) {
  const props: ComponentProps<typeof Rail> = {
    workspaceName: "Acme",
    memberships: MEMBERSHIPS,
    currentWorkspaceId: "w1",
    onSwitchWorkspace: vi.fn(),
    onCreateOrJoinAnother: vi.fn(),
    channels: CHANNELS,
    unreads: UNREADS,
    currentChannelId: "c1",
    onSelectChannel: vi.fn(),
    onCreateChannel: vi.fn(),
    self: SELF,
    youMenuOpen: false,
    onSetYouMenuOpen: vi.fn(),
    theme: "dark",
    onToggleTheme: vi.fn(),
    onLogout: vi.fn(),
    onOpenSettings: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<Rail {...props} />) };
}

describe("Rail (web spec §2)", () => {
  it("lists channels with live unread badges and mention bell badges", () => {
    renderRail();
    expect(screen.getByText("#general")).toBeInTheDocument();
    expect(screen.getByText("#reports")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument(); // general's unread count
    expect(screen.getByText("5")).toBeInTheDocument(); // reports' unread count
    expect(screen.getByText("🔔 2")).toBeInTheDocument(); // reports' mention badge
    expect(screen.queryByLabelText("0 mentions")).not.toBeInTheDocument(); // no badge for zero mentions
  });

  it("clicking a channel calls onSelectChannel with its id", () => {
    const { props } = renderRail();
    fireEvent.click(screen.getByText("#reports"));
    expect(props.onSelectChannel).toHaveBeenCalledWith("c2");
  });

  it("the '+' toggle reveals a create-channel form that calls onCreateChannel on submit", () => {
    const { props } = renderRail();
    fireEvent.click(screen.getByLabelText("Create channel"));
    fireEvent.change(screen.getByLabelText("Channel name"), { target: { value: "engineering" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(props.onCreateChannel).toHaveBeenCalledWith("engineering");
  });

  it("the workspace switcher lists every membership and highlights the current one", () => {
    renderRail();
    fireEvent.click(screen.getByRole("button", { name: "Acme" }));
    expect(screen.getByRole("menuitem", { name: "Acme" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Widgets Co" })).toBeInTheDocument();
  });

  it("picking a different workspace from the switcher calls onSwitchWorkspace", () => {
    const { props } = renderRail();
    fireEvent.click(screen.getByRole("button", { name: "Acme" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Widgets Co" }));
    expect(props.onSwitchWorkspace).toHaveBeenCalledWith("w2");
  });

  it("the switcher's create/join entry calls onCreateOrJoinAnother", () => {
    const { props } = renderRail();
    fireEvent.click(screen.getByRole("button", { name: "Acme" }));
    fireEvent.click(screen.getByText(/create or join a workspace/i));
    expect(props.onCreateOrJoinAnother).toHaveBeenCalled();
  });

  it("clicking YOU toggles the menu via onSetYouMenuOpen", () => {
    const { props } = renderRail({ youMenuOpen: false });
    fireEvent.click(screen.getByText("@alice"));
    expect(props.onSetYouMenuOpen).toHaveBeenCalledWith(true);
  });

  it("the YOU menu shows a whoami card, a theme toggle, and logout when open", () => {
    const { props } = renderRail({ youMenuOpen: true });
    const card = screen.getByTestId("whoami-card");
    expect(card).toHaveTextContent("Alice Human");
    expect(card).toHaveTextContent("@alice");

    fireEvent.click(screen.getByRole("menuitem", { name: /switch to light mode/i }));
    expect(props.onToggleTheme).toHaveBeenCalled();

    fireEvent.click(screen.getByRole("menuitem", { name: "Log out" }));
    expect(props.onLogout).toHaveBeenCalled();
  });

  // SMAC-92 Task 4: the whoami card's role suffix now comes from
  // `MemberSelfOut.role` (via `lib/capabilities.ts`'s `ROLE_LABELS`)
  // instead of the removed boolean `is_admin` flag.
  it("the whoami card shows the UI role label for an admin", () => {
    renderRail({ youMenuOpen: true, self: { ...SELF, role: "admin" } });
    expect(screen.getByTestId("whoami-card")).toHaveTextContent("Workspace Admin");
  });

  it("the whoami card shows the UI role label for an agent_admin", () => {
    renderRail({ youMenuOpen: true, self: { ...SELF, role: "agent_admin" } });
    expect(screen.getByTestId("whoami-card")).toHaveTextContent("Agent Admin");
  });

  it("the whoami card shows no role suffix for a plain member", () => {
    renderRail({ youMenuOpen: true, self: { ...SELF, role: "member" } });
    const card = screen.getByTestId("whoami-card");
    expect(card).not.toHaveTextContent("Workspace Admin");
    expect(card).not.toHaveTextContent("Agent Admin");
  });

  // SMAC-85: Settings (agents/invites/workspace admin, including workspace
  // delete) was previously reachable ONLY via Cmd-K palette commands --
  // these two are the rail's mouse-first entry points.
  it("the rail's gear button opens Settings via onOpenSettings", () => {
    const { props } = renderRail();
    fireEvent.click(screen.getByLabelText("Settings"));
    expect(props.onOpenSettings).toHaveBeenCalled();
  });

  it("the YOU menu's Settings entry opens Settings via onOpenSettings", () => {
    const { props } = renderRail({ youMenuOpen: true });
    fireEvent.click(screen.getByRole("menuitem", { name: "Settings" }));
    expect(props.onOpenSettings).toHaveBeenCalled();
  });
});
