import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import Settings from "../screens/Settings";
import * as api from "../lib/api";
import type { MemberOut, MemberSelfOut, Session } from "../lib/api";
import { AuthProvider, useAuth } from "../state/auth";
import { WorkspaceProvider } from "../state/workspace";
import { setViewportWidth } from "../testing/viewportMock";

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * Task 5: Settings (agents/invites/workspace admin). Mirrors `auth-flows.
 * test.tsx`'s convention of mocking the whole `lib/api` module and
 * rendering real providers around the screen under test, rather than
 * reaching into internals -- `Settings`/`WorkspacePanel` read `useWorkspace
 * ()`/`useAuth()` directly (same pattern `CreateOrJoin`/`JoinScreen`
 * already use), so both providers are real here, only `api.*` is mocked.
 *
 * The one-time-key test (mandatory per the task-5 brief) is the strictest
 * one in this file: it spies on every `console.*` method and asserts the
 * key string never appears in any call, AND that the key is gone from
 * `document.body.innerHTML` after dismissal -- not just hidden.
 */
vi.mock("../lib/api");

const ACCOUNT_ONLY_SESSION: Session = {
  url: "http://localhost",
  email: "alice@example.com",
  accountAccess: "aat",
  accountRefresh: "art",
};

const WORKSPACE_SESSION: Session = {
  ...ACCOUNT_ONLY_SESSION,
  workspaceId: "ws1",
  workspaceAccess: "wat",
  workspaceRefresh: "wrt",
};

/**
 * Mirrors `app/capabilities.py`'s `ROLE_CAPS` table (SMAC-92) for a
 * `member_type: "human"` member -- the exact set every role grants,
 * kept here rather than imported so this test file independently proves
 * the fixture matches the contract, not just whatever the app happens to
 * compute.
 */
const CAPS_BY_ROLE: Record<string, string[]> = {
  member: ["post", "read", "ack_mentions", "create_channels", "view_members", "view_agents"],
  agent_admin: [
    "post",
    "read",
    "ack_mentions",
    "create_channels",
    "view_members",
    "view_agents",
    "manage_agents",
    "mint_agent_invites",
  ],
  admin: [
    "post",
    "read",
    "ack_mentions",
    "create_channels",
    "view_members",
    "view_agents",
    "mint_human_invites",
    "mint_agent_invites",
    "manage_agents",
    "manage_workspace",
    "assign_roles",
    "remove_members",
  ],
};

function selfFixture(overrides: Partial<MemberSelfOut> & { role?: string } = {}): MemberSelfOut {
  const role = overrides.role ?? "admin";
  return {
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
    role,
    capabilities: CAPS_BY_ROLE[role] ?? [],
    workspace_visibility: "private",
    ...overrides,
  };
}

const AGENT_MEMBER: MemberOut = {
  member_id: "m2",
  member_name: "Analyst",
  member_type: "agent",
  handle: "analyst",
  created_at: "2026-01-01T00:00:00",
  account_id: "acc-agent-1",
  role: "member",
};

function ScreenProbe() {
  const { screen: authScreen } = useAuth();
  return <div data-testid="auth-screen">{authScreen}</div>;
}

type RenderOpts = {
  self?: MemberSelfOut;
  section?: "agents" | "invites" | "members" | "workspace";
  members?: MemberOut[];
  withProbe?: boolean;
};

function renderSettings(opts: RenderOpts = {}) {
  vi.mocked(api.getSession).mockReturnValue(WORKSPACE_SESSION);
  vi.mocked(api.channels).mockResolvedValue([]);
  vi.mocked(api.unreads).mockResolvedValue({ unreads: [] });
  vi.mocked(api.members).mockResolvedValue(opts.members ?? [AGENT_MEMBER]);
  vi.mocked(api.whoami).mockResolvedValue(opts.self ?? selfFixture());

  render(
    <AuthProvider>
      <WorkspaceProvider>
        <Settings onBack={vi.fn()} workspaceName="Acme" initialSection={opts.section} />
      </WorkspaceProvider>
      {opts.withProbe && <ScreenProbe />}
    </AuthProvider>
  );
}

function stubClipboard(): ReturnType<typeof vi.fn> {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
  return writeText;
}

afterEach(() => {
  vi.clearAllMocks();
  // Test-only cleanup of the stubbed clipboard property (`stubClipboard`
  // defines it with `configurable: true` specifically so this can undo it).
  delete (navigator as unknown as { clipboard?: unknown }).clipboard;
});

describe("Agents panel (web spec §2, constitution §6)", () => {
  it("lists agents with name, handle, and account_id", async () => {
    renderSettings();

    await screen.findByText("Analyst");
    expect(screen.getByText("@analyst")).toBeInTheDocument();
    expect(screen.getByText("acc-agent-1")).toBeInTheDocument();
  });

  it(
    "creating an agent reveals the key exactly once (mono block, 'shown exactly once' " +
      "warning), then removes it from the DOM on dismiss and never logs it to console",
    async () => {
      const spiedMethods = ["log", "info", "warn", "error", "debug"] as const;
      const consoleSpies = spiedMethods.map((method) =>
        vi.spyOn(console, method).mockImplementation(() => undefined)
      );
      const SECRET = "SECRET-KEY-VALUE-abc123XYZ";
      vi.mocked(api.createAgent).mockResolvedValue({
        member_id: "m3",
        member_name: "NewAgent",
        member_type: "agent",
        handle: "newagent",
        api_key: SECRET,
      });

      renderSettings();
      await screen.findByText("Analyst");

      fireEvent.click(screen.getByRole("button", { name: "+ Create agent" }));
      fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "NewAgent" } });
      fireEvent.click(screen.getByRole("button", { name: "Create" }));

      await screen.findByText(SECRET);
      expect(screen.getByText(/shown exactly once/i)).toBeInTheDocument();
      expect(api.createAgent).toHaveBeenCalledWith("NewAgent");

      fireEvent.click(screen.getByRole("button", { name: /done/i }));

      // Gone from the DOM -- not just visually hidden.
      expect(screen.queryByText(SECRET)).not.toBeInTheDocument();
      expect(document.body.innerHTML).not.toContain(SECRET);

      // Never logged, on any console method, at any point (create, reveal,
      // dismiss, or the refresh in between).
      for (const spy of consoleSpies) {
        for (const call of spy.mock.calls) {
          for (const arg of call) {
            expect(String(arg)).not.toContain(SECRET);
          }
        }
        spy.mockRestore();
      }
    }
  );

  it("copying the key uses the clipboard API with the exact key string", async () => {
    const writeText = stubClipboard();
    const SECRET = "COPY-ME-KEY-999";
    vi.mocked(api.createAgent).mockResolvedValue({
      member_id: "m3",
      member_name: "NewAgent",
      member_type: "agent",
      handle: "newagent",
      api_key: SECRET,
    });

    renderSettings();
    await screen.findByText("Analyst");
    fireEvent.click(screen.getByRole("button", { name: "+ Create agent" }));
    fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "NewAgent" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByText(SECRET);

    fireEvent.click(screen.getByRole("button", { name: "Copy key" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(SECRET));
    await screen.findByRole("button", { name: "Copied!" });
  });

  it("attaching an existing agent account also mints and reveals a fresh key", async () => {
    vi.mocked(api.attachAgent).mockResolvedValue({
      member_id: "m4",
      member_name: "Analyst",
      member_type: "agent",
      handle: "analyst2",
      api_key: "ATTACHED-KEY-999",
    });

    renderSettings();
    await screen.findByText("Analyst");
    fireEvent.click(screen.getByRole("button", { name: "Attach existing" }));
    fireEvent.change(screen.getByLabelText("Account ID"), { target: { value: "acc-existing" } });
    fireEvent.click(screen.getByRole("button", { name: "Attach" }));

    await screen.findByText("ATTACHED-KEY-999");
    expect(api.attachAgent).toHaveBeenCalledWith("acc-existing");
  });
});

describe("Invites panel (web spec §2)", () => {
  it("mints a human invite code, offers a copy button, and shows the Bob instructions line", async () => {
    vi.mocked(api.mintInvite).mockResolvedValue({
      invite_id: "inv1",
      workspace_id: "ws1",
      invite_type: "code",
      email: null,
      code: "ABC123",
      created_by: "m1",
      created_at: "2026-01-01T00:00:00",
      expires_at: null,
    });

    renderSettings({ section: "invites" });
    // Invites is gated on whoami's capabilities (SMAC-92) -- the initial
    // fetch is async, so the tab (and this button) aren't there yet on
    // the very first render.
    fireEvent.click(await screen.findByRole("button", { name: "Mint invite code" }));

    expect(api.mintInvite).toHaveBeenCalledWith("human");
    const codeBlock = await screen.findByTestId("invite-code-human");
    expect(codeBlock).toHaveTextContent("ABC123");
    expect(screen.getByRole("button", { name: "Copy code" })).toBeInTheDocument();
    expect(screen.getByText(/tell them/i)).toBeInTheDocument();
  });

  it("mints an agent invite code for an agent_admin (no human-invite section)", async () => {
    vi.mocked(api.mintInvite).mockResolvedValue({
      invite_id: "inv2",
      workspace_id: "ws1",
      invite_type: "agent_code",
      email: null,
      code: "AGENT123",
      created_by: "m1",
      created_at: "2026-01-01T00:00:00",
      expires_at: null,
    });

    renderSettings({ self: selfFixture({ role: "agent_admin" }), section: "invites" });
    await screen.findByRole("heading", { name: "Invite an agent" });
    expect(screen.queryByRole("heading", { name: "Invite a person" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Mint agent invite code" }));

    expect(api.mintInvite).toHaveBeenCalledWith("agent");
    const codeBlock = await screen.findByTestId("invite-code-agent");
    expect(codeBlock).toHaveTextContent("AGENT123");
  });

  it("an agent_admin (only mint_agent_invites) sees no Human/Agent kind selector at all", async () => {
    renderSettings({ self: selfFixture({ role: "agent_admin" }), section: "invites" });
    await screen.findByRole("heading", { name: "Invite an agent" });
    expect(screen.queryByRole("button", { name: "Human" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Agent" })).not.toBeInTheDocument();
  });

  it("an admin (both mint caps) gets a Human/Agent kind selector, defaulting to Human, switching sections on click", async () => {
    renderSettings({ self: selfFixture({ role: "admin" }), section: "invites" });

    await screen.findByRole("heading", { name: "Invite a person" });
    expect(screen.queryByRole("heading", { name: "Invite an agent" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Human" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Agent" }));
    await screen.findByRole("heading", { name: "Invite an agent" });
    expect(screen.queryByRole("heading", { name: "Invite a person" })).not.toBeInTheDocument();
  });

  it("a minted agent invite shows the exact bootstrap line with the endpoint in mono", async () => {
    vi.mocked(api.mintInvite).mockResolvedValue({
      invite_id: "inv3",
      workspace_id: "ws1",
      invite_type: "agent_code",
      email: null,
      code: "BOOT123",
      created_by: "m1",
      created_at: "2026-01-01T00:00:00",
      expires_at: "2026-01-08T00:00:00",
    });

    renderSettings({ self: selfFixture({ role: "agent_admin" }), section: "invites" });
    fireEvent.click(await screen.findByRole("button", { name: "Mint agent invite code" }));

    await screen.findByTestId("invite-code-agent");
    expect(
      screen.getByText(/Put this code in your agent's config; its first call is/i)
    ).toBeInTheDocument();
    expect(screen.getByText("POST /agents/join")).toHaveClass("mono");
  });

  it("lists pending invites with type labels and revokes one via the API", async () => {
    vi.mocked(api.listInvites).mockResolvedValue([
      {
        invite_id: "inv-human",
        workspace_id: "ws1",
        invite_type: "code",
        email: null,
        code: "HUMANCODE",
        created_by: "m1",
        created_at: "2026-01-01T00:00:00",
        expires_at: null,
      },
      {
        invite_id: "inv-agent",
        workspace_id: "ws1",
        invite_type: "agent_code",
        email: null,
        code: "AGENTCODE",
        created_by: "m1",
        created_at: "2026-01-01T00:00:00",
        expires_at: "2026-01-08T00:00:00",
      },
    ]);
    vi.mocked(api.revokeInvite).mockResolvedValue({ status: "revoked" });

    renderSettings({ self: selfFixture({ role: "admin" }), section: "invites" });
    await screen.findByText("Human code");
    expect(screen.getByText("Agent code")).toBeInTheDocument();
    expect(screen.getByText("HUMANCODE")).toBeInTheDocument();
    expect(screen.getByText("AGENTCODE")).toBeInTheDocument();

    const revokeButtons = screen.getAllByRole("button", { name: "Revoke" });
    fireEvent.click(revokeButtons[0]);

    await waitFor(() => expect(api.revokeInvite).toHaveBeenCalledWith("inv-human"));
    await waitFor(() => expect(api.listInvites).toHaveBeenCalledTimes(2)); // initial + post-revoke refresh
  });
});

describe("Settings tab gating from capabilities (SMAC-92 task-4 brief, replaces is_admin gating)", () => {
  it("admin sees all four tabs: Agents, Invites, Members, Workspace", async () => {
    renderSettings({ self: selfFixture({ role: "admin" }) });

    await screen.findByText("Analyst");
    expect(screen.getByRole("button", { name: "Agents" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Invites" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Members" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Workspace" })).toBeInTheDocument();
  });

  it("agent_admin sees Agents (full) + Invites, but NOT Workspace or Members", async () => {
    renderSettings({ self: selfFixture({ role: "agent_admin" }) });

    await screen.findByText("Analyst");
    expect(screen.getByRole("button", { name: "Agents" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Invites" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Members" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Workspace" })).not.toBeInTheDocument();
    // "full" Agents -- mutation controls present.
    expect(screen.getByRole("button", { name: "+ Create agent" })).toBeInTheDocument();
  });

  it("member sees ONLY Agents (read-only) -- no Invites, Members, or Workspace tabs", async () => {
    renderSettings({ self: selfFixture({ role: "member" }) });

    await screen.findByText("Analyst");
    expect(screen.getByRole("button", { name: "Agents" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Invites" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Members" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Workspace" })).not.toBeInTheDocument();
    // Read-only -- list visible, mutation controls absent.
    expect(screen.queryByRole("button", { name: "+ Create agent" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Attach existing" })).not.toBeInTheDocument();
  });

  it("the Members tab (visible to a caller with assign_roles/remove_members) renders the real admin panel", async () => {
    renderSettings({
      self: selfFixture({ role: "admin" }),
      section: "members",
      members: [selfFixture({ role: "admin" }), AGENT_MEMBER],
    });
    await screen.findByText("Humans");
    expect(screen.getByRole("heading", { name: "Agents", level: 3 })).toBeInTheDocument();
  });
});

describe("Workspace panel: admin gating (web spec §2, task-5 brief; SMAC-92 task-4: now manage_workspace-gated)", () => {
  it("hides the Workspace tab entirely for a non-admin (not merely disabling its controls)", async () => {
    renderSettings({ self: selfFixture({ role: "member" }) });

    await screen.findByText("Analyst");
    expect(screen.queryByRole("button", { name: "Workspace" })).not.toBeInTheDocument();
  });

  it("shows the Workspace tab for an admin", async () => {
    renderSettings({ self: selfFixture({ role: "admin" }), section: "workspace" });

    await screen.findByRole("heading", { name: "Visibility" });
  });
});

describe("Workspace panel: visibility toggle (admin)", () => {
  it("flips private -> public via the API and reflects the server's response", async () => {
    vi.mocked(api.updateWorkspaceVisibility).mockResolvedValue({
      workspace_id: "ws1",
      workspace_name: "Acme",
      visibility: "public",
    });

    renderSettings({ section: "workspace" });
    await screen.findByRole("heading", { name: "Visibility" });
    expect(screen.getByText("private", { exact: false })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /make public/i }));

    await waitFor(() => expect(api.updateWorkspaceVisibility).toHaveBeenCalledWith("public"));
    await screen.findByRole("button", { name: /make private/i });
  });
});

describe("Workspace panel: typed-confirmation delete (constitution §3)", () => {
  it("only enables the delete button once BOTH the exact workspace name and the word 'delete' are typed", async () => {
    renderSettings({ section: "workspace" });
    await screen.findByRole("heading", { name: "Delete workspace" });

    const deleteButton = screen.getByRole("button", { name: "Delete Acme" });
    expect(deleteButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/type the workspace name/i), {
      target: { value: "Acme" },
    });
    expect(deleteButton).toBeDisabled(); // name alone isn't enough

    fireEvent.change(screen.getByLabelText(/then type/i), { target: { value: "wrong" } });
    expect(deleteButton).toBeDisabled(); // wrong confirmation word

    fireEvent.change(screen.getByLabelText(/then type/i), { target: { value: "delete" } });
    expect(deleteButton).not.toBeDisabled();
  });

  it("deletes the workspace, clears only the workspace-tier session, and lands back on create-or-join (account intact)", async () => {
    vi.mocked(api.deleteWorkspace).mockResolvedValue({ status: "deleted" });
    vi.mocked(api.clearWorkspaceTier).mockReturnValue(ACCOUNT_ONLY_SESSION);

    renderSettings({ section: "workspace", withProbe: true });
    await screen.findByRole("heading", { name: "Delete workspace" });
    await waitFor(() => expect(screen.getByTestId("auth-screen")).toHaveTextContent("authed"));

    fireEvent.change(screen.getByLabelText(/type the workspace name/i), {
      target: { value: "Acme" },
    });
    fireEvent.change(screen.getByLabelText(/then type/i), { target: { value: "delete" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete Acme" }));

    await waitFor(() => expect(api.deleteWorkspace).toHaveBeenCalled());
    expect(api.clearWorkspaceTier).toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByTestId("auth-screen")).toHaveTextContent("create-or-join")
    );
  });
});

describe("Settings' mobile tier (final review Finding 4, MINOR: the T4 responsive pass missed T5)", () => {
  it("renders the same .settings__body/.settings__tabs structure the mobile media query below targets, at a 390px width", async () => {
    // `Settings.tsx` itself is pure CSS-driven here (no `useViewportTier()`
    // read, unlike Rail/Drawer/Room in `responsive.test.tsx`) -- the fix is
    // a `shell.css` media query keying off these SAME class names, so this
    // just documents the hook points exist and stay stable at the phone
    // width the review's failure scenario used (390px), rather than
    // asserting jsdom-uncomputable `@media` behavior.
    setViewportWidth(390);
    renderSettings();
    await screen.findByRole("heading", { name: "Settings" });

    const tabs = document.querySelector(".settings__tabs");
    const body = document.querySelector(".settings__body");
    expect(tabs).toBeInTheDocument();
    expect(body).toBeInTheDocument();
    expect(tabs?.parentElement).toBe(body);
  });

  it("shell.css has a <900px rule collapsing .settings__tabs to a horizontal row inside a stacked .settings__body (regression guard)", () => {
    const css = readFileSync(resolve(__dirname, "../styles/shell.css"), "utf-8");
    // Isolate just the Settings-specific mobile block by its own comment
    // markers (there's more than one `@media (max-width: 899px)` block in
    // the file) rather than assuming it's the first/last one.
    const start = css.indexOf("Final review Finding 4");
    expect(start).toBeGreaterThan(-1); // sanity: the block's own doc comment is still there
    const end = css.indexOf("/* -- Agents panel", start);
    expect(end).toBeGreaterThan(start);
    const settingsBlock = css.slice(start, end);

    expect(settingsBlock).toContain("@media (max-width: 899px)");
    expect(settingsBlock).toMatch(/\.settings__body\s*\{[^}]*flex-direction:\s*column/);
    expect(settingsBlock).toMatch(/\.settings__tabs\s*\{[^}]*flex-direction:\s*row/);
  });
});
