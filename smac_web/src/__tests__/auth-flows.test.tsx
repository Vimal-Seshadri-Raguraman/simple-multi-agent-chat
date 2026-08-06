import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "../App";
import * as api from "../lib/api";
import * as live from "../lib/live";
import type { Session } from "../lib/api";
import { CLIENT_VERSION } from "../version";

// Component-level flow tests: the api module is entirely mocked (task-2
// brief: "auth flow components with a FakeApi module mock"). Each screen
// under test talks to `api.*` directly, and `state/auth.tsx` reads
// `api.getSession()`/calls `api.logout()` -- mocking the one module
// covers both.
vi.mock("../lib/api");
// Task-4's live layer (`AuthedShell.tsx`'s socket wiring) mounts the
// moment a test reaches "authed" -- mocked here too (with a harmless
// no-op `Closeable`) so these tests, which only care about REACHING that
// screen, don't also open a real `WebSocket` against an undefined URL
// (`live.test.ts`/`live-wiring.test.tsx` own the real wiring's behavior).
vi.mock("../lib/live");

const BASE_SESSION: Session = {
  url: "http://localhost",
  email: "alice@example.com",
  accountAccess: "aat",
  accountRefresh: "art",
};

const WORKSPACE_SESSION: Session = {
  ...BASE_SESSION,
  workspaceId: "ws1",
  workspaceAccess: "wat",
  workspaceRefresh: "wrt",
};

beforeEach(() => {
  vi.mocked(api.getSession).mockReturnValue(null);
  // VersionBanner calls api.meta() unconditionally on every mount (it's
  // rendered inside <App/> regardless of auth screen) -- give every test
  // a default resolved value so an unrelated test doesn't have to stub
  // it just to avoid an unhandled rejection/undefined-.then() crash.
  vi.mocked(api.meta).mockResolvedValue({ server_version: CLIENT_VERSION, api_version: 1 });
  // Task 3's AuthedShell (+ its WorkspaceProvider) fetches all of these on
  // mount, the moment a test reaches the "authed" screen -- default them
  // to harmless empty results so tests that only care about REACHING
  // "authed" (this file's concern) don't also have to stub the shell's
  // own data plumbing (that's `rendering.test.tsx`/`rail.test.tsx`'s job).
  vi.mocked(api.channels).mockResolvedValue([]);
  vi.mocked(api.unreads).mockResolvedValue({ unreads: [] });
  vi.mocked(api.members).mockResolvedValue([]);
  vi.mocked(api.whoami).mockResolvedValue({
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
    role: "member",
    capabilities: [],
    workspace_visibility: null,
  });
  vi.mocked(api.accountMe).mockResolvedValue({
    account_id: "acc-1",
    email: "alice@example.com",
    created_at: "2026-01-01T00:00:00",
    memberships: [],
  });
  vi.mocked(live.connectRoom).mockReturnValue({ close: vi.fn() });
  vi.mocked(live.connectBell).mockReturnValue({ close: vi.fn() });
});

afterEach(() => {
  vi.clearAllMocks();
});

async function goToLogin() {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Log in" }));
  await screen.findByRole("button", { name: "Log in" }); // the submit button, distinct render pass
}

describe("page refresh with a saved session (state/auth.tsx's initialState)", () => {
  it("an ACCOUNT-ONLY session (no workspace yet) lands on create-or-join, not welcome (task-3 brief's deferred T2 fix)", async () => {
    vi.mocked(api.getSession).mockReturnValue(BASE_SESSION); // no workspaceId/workspaceAccess

    render(<App />);

    await screen.findByRole("heading", { name: /create or join a workspace/i });
    expect(screen.queryByText("Simple Multi-Agent Chat")).not.toBeInTheDocument(); // Welcome's tagline
  });

  it("a full WORKSPACE-tier session lands straight on the authed shell", async () => {
    vi.mocked(api.getSession).mockReturnValue(WORKSPACE_SESSION);

    render(<App />);

    await screen.findByRole("navigation", { name: /workspace navigation/i });
  });

  it("no saved session lands on welcome", async () => {
    vi.mocked(api.getSession).mockReturnValue(null);

    render(<App />);

    expect(screen.getByText("Simple Multi-Agent Chat")).toBeInTheDocument();
    await waitFor(() => expect(api.meta).toHaveBeenCalled()); // let VersionBanner's effect settle
  });
});

describe("the three login branches (web spec §2)", () => {
  it("0 memberships lands on create-or-join", async () => {
    vi.mocked(api.login).mockResolvedValue({ session: BASE_SESSION, workspaces: [] });

    await goToLogin();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "alice@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter2000" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    await screen.findByRole("heading", { name: /create or join a workspace/i });
    expect(api.enterWorkspace).not.toHaveBeenCalled();
  });

  it("1 membership auto-enters that workspace and lands authed", async () => {
    vi.mocked(api.login).mockResolvedValue({
      session: BASE_SESSION,
      workspaces: [{ workspace_id: "ws1", workspace_name: "Acme", member_id: "m1", handle: "alice" }],
    });
    vi.mocked(api.enterWorkspace).mockResolvedValue(undefined);
    vi.mocked(api.getSession).mockReturnValueOnce(null).mockReturnValue(WORKSPACE_SESSION);

    await goToLogin();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "alice@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter2000" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    await waitFor(() => expect(api.enterWorkspace).toHaveBeenCalledWith("ws1"));
    // Reached "authed" -- the Task 3 daily-driver shell (Rail/Room/Drawer)
    // is now on screen (this file only cares about REACHING that screen;
    // the shell's own content is `rail.test.tsx`/`rendering.test.tsx`'s job).
    await screen.findByRole("navigation", { name: /workspace navigation/i });
  });

  it(">1 memberships lands on the workspace picker, listing each by name + handle", async () => {
    vi.mocked(api.login).mockResolvedValue({
      session: BASE_SESSION,
      workspaces: [
        { workspace_id: "ws1", workspace_name: "Acme", member_id: "m1", handle: "alice" },
        { workspace_id: "ws2", workspace_name: "Widgets Co", member_id: "m2", handle: "alice2" },
      ],
    });

    await goToLogin();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "alice@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter2000" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    await screen.findByRole("heading", { name: /choose a workspace/i });
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("@alice")).toBeInTheDocument();
    expect(screen.getByText("Widgets Co")).toBeInTheDocument();
    expect(screen.getByText("@alice2")).toBeInTheDocument();

    vi.mocked(api.enterWorkspace).mockResolvedValue(undefined);
    vi.mocked(api.getSession).mockReturnValue({ ...WORKSPACE_SESSION, workspaceId: "ws2" });
    fireEvent.click(screen.getByRole("button", { name: /widgets co/i }));

    await waitFor(() => expect(api.enterWorkspace).toHaveBeenCalledWith("ws2"));
    await screen.findByRole("navigation", { name: /workspace navigation/i });
  });
});

describe("register: account-first two-step order (task-2 brief, binding)", () => {
  it("shows ONLY account fields on step 1, and only reaches the workspace step after signup succeeds", async () => {
    vi.mocked(api.signup).mockResolvedValue(BASE_SESSION);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Create an account" }));

    // Step 1 (Register.tsx): account fields only, no workspace field anywhere.
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByLabelText("Confirm password")).toBeInTheDocument();
    expect(screen.queryByLabelText(/workspace name/i)).not.toBeInTheDocument();
    expect(api.signup).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "new@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter2000" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "hunter2000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    // signup() must have been called with ONLY account fields, before any
    // workspace-step UI exists.
    await waitFor(() =>
      expect(api.signup).toHaveBeenCalledWith("new@example.com", "hunter2000")
    );

    // Step 2 (workspace step) only appears now, after account creation succeeded.
    await screen.findByRole("heading", { name: /create or join a workspace/i });
  });

  it("rejects a mismatched confirm-password before ever calling signup", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Create an account" }));
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "new@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter2000" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "different" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/do not match/i);
    expect(api.signup).not.toHaveBeenCalled();
  });
});

describe("join screen: live public search (debounced) + invite-code entry", () => {
  async function goToJoinScreen() {
    vi.mocked(api.login).mockResolvedValue({ session: BASE_SESSION, workspaces: [] });
    vi.mocked(api.searchPublic).mockResolvedValue([]);

    await goToLogin();
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "alice@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter2000" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    await screen.findByRole("heading", { name: /create or join a workspace/i });
    fireEvent.click(screen.getByRole("button", { name: /join a workspace/i }));
    await screen.findByRole("heading", { name: /join a workspace/i });
  }

  it("debounces rapid keystrokes into a single searchPublic call with the final query", async () => {
    await goToJoinScreen();
    const searchBox = screen.getByLabelText(/search public workspaces/i);

    fireEvent.change(searchBox, { target: { value: "a" } });
    fireEvent.change(searchBox, { target: { value: "ac" } });
    fireEvent.change(searchBox, { target: { value: "acme" } });

    // Nothing fired yet -- every keystroke reset the debounce window.
    expect(api.searchPublic).not.toHaveBeenCalled();

    await waitFor(() => expect(api.searchPublic).toHaveBeenCalledTimes(1), { timeout: 1000 });
    expect(api.searchPublic).toHaveBeenCalledWith("acme");
  });

  it("joins via invite code with the entered display name", async () => {
    await goToJoinScreen();
    vi.mocked(api.joinCode).mockResolvedValue({
      session: { ...WORKSPACE_SESSION },
      workspaceName: "Coded Co",
    });
    vi.mocked(api.getSession).mockReturnValue(WORKSPACE_SESSION);

    fireEvent.change(screen.getByLabelText("Your first name"), { target: { value: "Bob" } });
    fireEvent.change(screen.getByLabelText("Your last name"), { target: { value: "Builder" } });
    fireEvent.change(screen.getByLabelText("Invite code"), { target: { value: "abc123" } });
    fireEvent.click(screen.getByRole("button", { name: /^join$/i }));

    await waitFor(() =>
      expect(api.joinCode).toHaveBeenCalledWith("abc123", "Bob", "Builder")
    );
    await screen.findByRole("navigation", { name: /workspace navigation/i });
  });
});

describe("version banner + focus-poll toast (VersionBanner, minimal per task-2 brief)", () => {
  it("shows a dismissible mismatch banner when the server version differs from CLIENT_VERSION", async () => {
    vi.mocked(api.meta).mockResolvedValue({ server_version: "9.9.9", api_version: 1 });

    render(<App />);

    await screen.findByText(new RegExp(`client v${CLIENT_VERSION}`, "i"));
    expect(screen.getByText(/server v9\.9\.9/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(screen.queryByText(/versions differ/i)).not.toBeInTheDocument();
  });

  it("shows a 'SMAC updated' toast when a focus-triggered /meta poll sees a new server_version", async () => {
    vi.mocked(api.meta).mockResolvedValueOnce({ server_version: CLIENT_VERSION, api_version: 1 });
    render(<App />);
    await waitFor(() => expect(api.meta).toHaveBeenCalledTimes(1));

    vi.mocked(api.meta).mockResolvedValueOnce({ server_version: "9.9.10", api_version: 1 });
    fireEvent(window, new Event("focus"));

    await screen.findByText(/smac updated.*refresh/i);
  });

  it("Finding 5 (final review, MINOR): does NOT stack a second toast on a later focus poll reporting the SAME new version", async () => {
    vi.mocked(api.meta).mockResolvedValueOnce({ server_version: CLIENT_VERSION, api_version: 1 });
    render(<App />);
    await waitFor(() => expect(api.meta).toHaveBeenCalledTimes(1));

    // Three more focus events, each polling and finding the SAME already-
    // reported new version -- a tab left open, focused repeatedly, well
    // after the one redeploy that happened.
    vi.mocked(api.meta).mockResolvedValue({ server_version: "9.9.10", api_version: 1 });
    fireEvent(window, new Event("focus"));
    await screen.findByText(/smac updated.*refresh/i);
    fireEvent(window, new Event("focus"));
    fireEvent(window, new Event("focus"));
    await waitFor(() => expect(api.meta).toHaveBeenCalledTimes(4));

    expect(screen.getAllByText(/smac updated.*refresh/i)).toHaveLength(1); // one toast, not three
  });

  it("Finding 5 regression: a SECOND, later, DIFFERENT redeploy still gets its own toast", async () => {
    vi.mocked(api.meta).mockResolvedValueOnce({ server_version: CLIENT_VERSION, api_version: 1 });
    render(<App />);
    await waitFor(() => expect(api.meta).toHaveBeenCalledTimes(1));

    vi.mocked(api.meta).mockResolvedValueOnce({ server_version: "9.9.10", api_version: 1 });
    fireEvent(window, new Event("focus"));
    await screen.findByText(/smac updated.*refresh/i);

    vi.mocked(api.meta).mockResolvedValueOnce({ server_version: "9.9.11", api_version: 1 });
    fireEvent(window, new Event("focus"));

    await waitFor(() => expect(screen.getAllByText(/smac updated.*refresh/i)).toHaveLength(2));
  });
});

describe("Fix round 1: back-to-workspace affordance on CreateOrJoin/JoinScreen (task-5 review finding)", () => {
  // The palette's `/workspace create`/`/join` commands (and the Rail
  // switcher's "Create or join a workspace…" entry, same underlying
  // screens) land here via a bare NAVIGATE that does NOT clear the
  // session -- before this fix, neither screen offered any way back to
  // the workspace the user started in.
  const CURRENT_MEMBERSHIP = {
    workspace_id: "ws1",
    workspace_name: "Acme",
    member_id: "m1",
    handle: "alice",
  };

  function openPaletteAndRunCommand(commandText: string) {
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    fireEvent.mouseDown(screen.getByText(commandText));
  }

  it("/workspace create (palette) shows 'Back to Acme', which returns to the authed shell with the same workspace", async () => {
    vi.mocked(api.getSession).mockReturnValue(WORKSPACE_SESSION);
    vi.mocked(api.accountMe).mockResolvedValue({
      account_id: "acc-1",
      email: "alice@example.com",
      created_at: "2026-01-01T00:00:00",
      memberships: [CURRENT_MEMBERSHIP],
    });

    render(<App />);
    await screen.findByRole("navigation", { name: /workspace navigation/i });

    openPaletteAndRunCommand("/workspace create");

    await screen.findByRole("heading", { name: /create or join a workspace/i });
    const backButton = await screen.findByRole("button", { name: /back to acme/i });

    fireEvent.click(backButton);

    await screen.findByRole("navigation", { name: /workspace navigation/i });
  });

  it("/join (palette) shows 'Back to Acme', which returns to the authed shell with the same workspace", async () => {
    vi.mocked(api.getSession).mockReturnValue(WORKSPACE_SESSION);
    vi.mocked(api.accountMe).mockResolvedValue({
      account_id: "acc-1",
      email: "alice@example.com",
      created_at: "2026-01-01T00:00:00",
      memberships: [CURRENT_MEMBERSHIP],
    });
    vi.mocked(api.searchPublic).mockResolvedValue([]);

    render(<App />);
    await screen.findByRole("navigation", { name: /workspace navigation/i });

    openPaletteAndRunCommand("/join");

    await screen.findByRole("heading", { name: /join a workspace/i });
    const backButton = await screen.findByRole("button", { name: /back to acme/i });

    fireEvent.click(backButton);

    await screen.findByRole("navigation", { name: /workspace navigation/i });
  });

  it("shows NO back affordance during first-run onboarding (register step 1, a 0-membership login, or its Join screen) -- there's no workspace behind it", async () => {
    vi.mocked(api.signup).mockResolvedValue(BASE_SESSION);
    vi.mocked(api.searchPublic).mockResolvedValue([]);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Create an account" }));
    await screen.findByLabelText("Email");
    expect(screen.queryByRole("button", { name: /back to/i })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "new@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "hunter2000" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), {
      target: { value: "hunter2000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    await screen.findByRole("heading", { name: /create or join a workspace/i });
    expect(screen.queryByRole("button", { name: /back to/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /join a workspace/i }));
    await screen.findByRole("heading", { name: /join a workspace/i });
    expect(screen.queryByRole("button", { name: /back to/i })).not.toBeInTheDocument();
  });
});
