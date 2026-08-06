import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../lib/api";
import {
  NameTakenError,
  RateLimitedError,
  SessionExpired,
  SmacError,
  Unreachable,
  ValidationError,
} from "../lib/errors";
import type { Session } from "../lib/session";
import { installFetchMock } from "../testing/fetchMock";

const WORKSPACE_SESSION: Session = {
  url: "http://localhost",
  email: "alice@example.com",
  accountAccess: "account-access-1",
  accountRefresh: "account-refresh-1",
  workspaceId: "ws1",
  workspaceAccess: "workspace-access-1",
  workspaceRefresh: "workspace-refresh-1",
};

const ACCOUNT_ONLY_SESSION: Session = {
  url: "http://localhost",
  email: "alice@example.com",
  accountAccess: "account-access-1",
  accountRefresh: "account-refresh-1",
};

const TOKEN_PAIR = (accessToken: string, refreshToken: string) => ({
  access_token: accessToken,
  refresh_token: refreshToken,
  token_type: "bearer",
  expires_in: 900,
});

const ERROR_401 = { error: { code: "invalid_token", message: "expired" } };

beforeEach(() => {
  window.localStorage.clear();
  api.setSession(null);
});

afterEach(() => {
  window.localStorage.clear();
});

describe("account-birth doors: field names verified against app/routers/accounts.py", () => {
  it("signup POSTs email/password to /accounts and saves an account-only session", async () => {
    const mock = installFetchMock();
    mock.queue({
      status: 200,
      body: {
        account: { account_id: "acc1", email: "alice@example.com", created_at: "2026-01-01T00:00:00Z" },
        tokens: TOKEN_PAIR("aat", "art"),
      },
    });

    const session = await api.signup("alice@example.com", "hunter2000");

    expect(mock.calls).toHaveLength(1);
    expect(mock.calls[0]).toMatchObject({
      method: "POST",
      url: "/accounts",
      body: { email: "alice@example.com", password: "hunter2000" },
    });
    expect(session.accountAccess).toBe("aat");
    expect(session.accountRefresh).toBe("art");
    expect(session.workspaceId).toBeUndefined();
    expect(api.getSession()).toEqual(session);
    expect(JSON.parse(window.localStorage.getItem("smac.session") ?? "null")).toEqual(session);
  });

  it("login POSTs to /accounts/login (no workspace_id) and returns workspaces", async () => {
    const mock = installFetchMock();
    mock.queue({
      status: 200,
      body: {
        account: { account_id: "acc1", email: "alice@example.com", created_at: "2026-01-01T00:00:00Z" },
        tokens: TOKEN_PAIR("aat", "art"),
        workspaces: [
          { workspace_id: "ws1", workspace_name: "Acme", member_id: "m1", handle: "alice" },
        ],
      },
    });

    const { session, workspaces } = await api.login("alice@example.com", "hunter2000");

    expect(mock.calls[0]).toMatchObject({ method: "POST", url: "/accounts/login" });
    expect(mock.calls[0].body).not.toHaveProperty("workspace_id");
    expect(session.accountAccess).toBe("aat");
    expect(workspaces).toEqual([
      { workspace_id: "ws1", workspace_name: "Acme", member_id: "m1", handle: "alice" },
    ]);
  });
});

describe("workspace-birth doors: field names verified against app/routers/workspaces.py + invites.py", () => {
  beforeEach(() => {
    api.setSession(ACCOUNT_ONLY_SESSION);
  });

  it("createWorkspace sends display_first_name/display_last_name (NOT first_name/last_name)", async () => {
    const mock = installFetchMock();
    mock.queue({
      status: 200,
      body: {
        ...TOKEN_PAIR("wat", "wrt"),
        member: { member_id: "m1", workspace_id: "ws1" },
        workspace: { workspace_id: "ws1", workspace_name: "Acme", visibility: "private" },
      },
    });

    const { session, workspaceName } = await api.createWorkspace(
      "Acme",
      "private",
      "Ada",
      "Lovelace"
    );

    expect(mock.calls[0]).toMatchObject({ method: "POST", url: "/workspaces" });
    expect(mock.calls[0].body).toEqual({
      workspace_name: "Acme",
      visibility: "private",
      display_first_name: "Ada",
      display_last_name: "Lovelace",
    });
    expect(workspaceName).toBe("Acme");
    expect(session.workspaceId).toBe("ws1");
    expect(session.workspaceAccess).toBe("wat");
    // account fields/email carried over untouched
    expect(session.accountAccess).toBe(ACCOUNT_ONLY_SESSION.accountAccess);
    expect(session.email).toBe(ACCOUNT_ONLY_SESSION.email);
  });

  it("joinPublic POSTs first_name/last_name to /workspaces/{id}/register", async () => {
    const mock = installFetchMock();
    mock.queue({
      status: 200,
      body: {
        ...TOKEN_PAIR("wat", "wrt"),
        member: { member_id: "m1" },
        workspace: { workspace_id: "ws2", workspace_name: "Public Co", visibility: "public" },
      },
    });

    const { workspaceName } = await api.joinPublic("ws2", "Grace", "Hopper");

    expect(mock.calls[0]).toMatchObject({
      method: "POST",
      url: "/workspaces/ws2/register",
      body: { first_name: "Grace", last_name: "Hopper" },
    });
    expect(workspaceName).toBe("Public Co");
  });

  it("joinCode POSTs code + first_name/last_name to /workspaces/join", async () => {
    const mock = installFetchMock();
    mock.queue({
      status: 200,
      body: {
        ...TOKEN_PAIR("wat", "wrt"),
        member: { member_id: "m1" },
        workspace: { workspace_id: "ws3", workspace_name: "Coded Co", visibility: "private" },
      },
    });

    await api.joinCode("abc123", "Bob", "Builder");

    expect(mock.calls[0]).toMatchObject({
      method: "POST",
      url: "/workspaces/join",
      body: { code: "abc123", first_name: "Bob", last_name: "Builder" },
    });
  });

  it("mintInviteCode POSTs {invite_type: 'code'} to /workspaces/{id}/invites", async () => {
    api.setSession(WORKSPACE_SESSION);
    const mock = installFetchMock();
    mock.queue({
      status: 200,
      body: {
        invite_id: "inv1",
        workspace_id: "ws1",
        invite_type: "code",
        email: null,
        code: "xyz",
        created_by: "m1",
        created_at: "2026-01-01T00:00:00Z",
        expires_at: null,
      },
    });

    const invite = await api.mintInviteCode();

    expect(mock.calls[0]).toMatchObject({
      method: "POST",
      url: "/workspaces/ws1/invites",
      body: { invite_type: "code" },
    });
    expect(invite.code).toBe("xyz");
  });
});

describe("workspace-tier calls: field names verified against messages.py/unreads.py/members.py", () => {
  beforeEach(() => {
    api.setSession(WORKSPACE_SESSION);
  });

  it("post() sends message_text to the messages endpoint", async () => {
    const mock = installFetchMock();
    mock.queue({
      status: 200,
      body: {
        timestamp: "2026-01-01T00:00:00Z",
        workspace: { workspace_id: "ws1", workspace_name: "Acme" },
        Channel: { channel_id: "c1", channel_name: "general" },
        Sender: { member_id: "m1", member_name: "Alice" },
        Message: { message_id: "msg1", message_text: "hi" },
        mentions: [],
        channel_refs: [],
      },
    });

    await api.post("c1", "hi");

    expect(mock.calls[0]).toMatchObject({
      method: "POST",
      url: "/workspaces/ws1/channels/c1/messages",
      body: { message_text: "hi" },
    });
  });

  it("markRead sends last_read_message_id: null when no anchor is given", async () => {
    const mock = installFetchMock();
    mock.queue({
      status: 200,
      body: {
        channel_id: "c1",
        channel_name: "general",
        unread_count: 0,
        first_unread_message_id: null,
        mention_count: 0,
      },
    });

    await api.markRead("c1");

    expect(mock.calls[0]).toMatchObject({
      method: "POST",
      url: "/workspaces/ws1/channels/c1/read",
      body: { last_read_message_id: null },
    });
  });

  it("markRead sends the given anchor as last_read_message_id", async () => {
    const mock = installFetchMock();
    mock.queue({
      status: 200,
      body: {
        channel_id: "c1",
        channel_name: "general",
        unread_count: 0,
        first_unread_message_id: null,
        mention_count: 0,
      },
    });

    await api.markRead("c1", "msg-42");

    expect(mock.calls[0].body).toEqual({ last_read_message_id: "msg-42" });
  });

  it("messages() forwards after/limit as query params", async () => {
    const mock = installFetchMock();
    mock.queue({ status: 200, body: [] });

    await api.messages("c1", "msg-1", 5);

    expect(mock.calls[0].url).toBe("/workspaces/ws1/channels/c1/messages?limit=5&after=msg-1");
  });

  it("createAgent sends member_name; attachAgent sends account_id", async () => {
    const mock = installFetchMock();
    mock.queue({
      status: 200,
      body: { member_id: "m2", member_name: "bot", member_type: "agent", handle: "bot", api_key: "k1" },
    });
    mock.queue({
      status: 200,
      body: { member_id: "m3", member_name: "bot2", member_type: "agent", handle: "bot2", api_key: "k2" },
    });

    await api.createAgent("analyst");
    await api.attachAgent("acc-existing");

    expect(mock.calls[0]).toMatchObject({ url: "/members/agents", body: { member_name: "analyst" } });
    expect(mock.calls[1]).toMatchObject({
      url: "/members/agents",
      body: { account_id: "acc-existing" },
    });
  });

  it("deleteWorkspace sends ?confirm=delete", async () => {
    const mock = installFetchMock();
    mock.queue({ status: 200, body: { status: "deleted" } });

    await api.deleteWorkspace();

    expect(mock.calls[0]).toMatchObject({ method: "DELETE", url: "/workspaces/ws1?confirm=delete" });
  });
});

describe("refresh chain: EXACTLY smac_cli/api.py's _recover_workspace_session semantics", () => {
  it("a single workspace 401 recovers via workspace refresh, then the original call retries and succeeds", async () => {
    api.setSession(WORKSPACE_SESSION);
    const mock = installFetchMock();
    mock.queue({ status: 401, body: ERROR_401 }); // channels() attempt 1
    mock.queue({ status: 200, body: TOKEN_PAIR("new-wat", "new-wrt") }); // /auth/refresh (workspace)
    mock.queue({ status: 200, body: [{ channel_id: "c1", channel_name: "general" }] }); // channels() retry

    const result = await api.channels();

    expect(result).toEqual([{ channel_id: "c1", channel_name: "general" }]);
    expect(mock.calls[1]).toMatchObject({
      url: "/auth/refresh",
      body: { refresh_token: "workspace-refresh-1" },
    });
    expect(mock.calls[2].headers.Authorization).toBe("Bearer new-wat");
    expect(api.getSession()?.workspaceAccess).toBe("new-wat");
    expect(api.getSession()?.workspaceRefresh).toBe("new-wrt");
  });

  it("falls back to account refresh + workspace re-mint when the workspace refresh token is also stale", async () => {
    api.setSession(WORKSPACE_SESSION);
    const mock = installFetchMock();
    mock.queue({ status: 401, body: ERROR_401 }); // channels() attempt 1
    mock.queue({ status: 401, body: ERROR_401 }); // workspace /auth/refresh fails
    mock.queue({ status: 200, body: TOKEN_PAIR("new-aat", "new-art") }); // account /auth/refresh succeeds
    mock.queue({ status: 200, body: TOKEN_PAIR("remint-wat", "remint-wrt") }); // POST /workspaces/ws1/token
    mock.queue({ status: 200, body: [{ channel_id: "c1", channel_name: "general" }] }); // channels() retry

    const result = await api.channels();

    expect(result).toEqual([{ channel_id: "c1", channel_name: "general" }]);
    expect(mock.calls[2]).toMatchObject({
      url: "/auth/refresh",
      body: { refresh_token: "account-refresh-1" },
    });
    expect(mock.calls[3]).toMatchObject({ method: "POST", url: "/workspaces/ws1/token" });
    expect(mock.calls[3].headers.Authorization).toBe("Bearer new-aat");
    expect(mock.calls[4].headers.Authorization).toBe("Bearer remint-wat");
    expect(api.getSession()?.workspaceAccess).toBe("remint-wat");
    expect(api.getSession()?.accountAccess).toBe("new-aat");
  });

  it("raises SessionExpired and clears the saved session once every refresh option is exhausted", async () => {
    api.setSession(WORKSPACE_SESSION);
    const mock = installFetchMock();
    mock.queue({ status: 401, body: ERROR_401 }); // channels() attempt 1
    mock.queue({ status: 401, body: ERROR_401 }); // workspace /auth/refresh fails
    mock.queue({ status: 401, body: ERROR_401 }); // account /auth/refresh fails too

    await expect(api.channels()).rejects.toBeInstanceOf(SessionExpired);

    expect(api.getSession()).toBeNull();
    expect(window.localStorage.getItem("smac.session")).toBeNull();
  });

  it("account-tier calls (accountAuthedRequest) refresh-and-retry once, with no further fallback", async () => {
    api.setSession(ACCOUNT_ONLY_SESSION);
    const mock = installFetchMock();
    mock.queue({ status: 401, body: ERROR_401 }); // enterWorkspace attempt 1
    mock.queue({ status: 200, body: TOKEN_PAIR("new-aat", "new-art") }); // account /auth/refresh
    mock.queue({ status: 200, body: TOKEN_PAIR("wat", "wrt") }); // retried enterWorkspace

    await api.enterWorkspace("ws9");

    expect(mock.calls[1]).toMatchObject({
      url: "/auth/refresh",
      body: { refresh_token: "account-refresh-1" },
    });
    expect(api.getSession()?.workspaceId).toBe("ws9");
    expect(api.getSession()?.accountAccess).toBe("new-aat");
  });

  it("account-tier calls give up straight to SessionExpired when the account refresh itself fails", async () => {
    api.setSession(ACCOUNT_ONLY_SESSION);
    const mock = installFetchMock();
    mock.queue({ status: 401, body: ERROR_401 });
    mock.queue({ status: 401, body: ERROR_401 }); // account refresh fails, no further tier

    await expect(api.enterWorkspace("ws9")).rejects.toBeInstanceOf(SessionExpired);
    expect(api.getSession()).toBeNull();
  });
});

describe("session-invalidated handler (final review Finding 2a, IMPORTANT): the hook AuthProvider hangs its screen transition off", () => {
  afterEach(() => {
    api.setSessionInvalidatedHandler(null); // never leak a handler into another test
  });

  it("fires with the SessionExpired message once every workspace-tier refresh option is exhausted", async () => {
    api.setSession(WORKSPACE_SESSION);
    const handler = vi.fn();
    api.setSessionInvalidatedHandler(handler);
    const mock = installFetchMock();
    mock.queue({ status: 401, body: ERROR_401 }); // channels() attempt 1
    mock.queue({ status: 401, body: ERROR_401 }); // workspace /auth/refresh fails
    mock.queue({ status: 401, body: ERROR_401 }); // account /auth/refresh fails too

    await expect(api.channels()).rejects.toBeInstanceOf(SessionExpired);

    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith("Session expired — please log in again.");
  });

  it("fires once every account-tier refresh option is exhausted", async () => {
    api.setSession(ACCOUNT_ONLY_SESSION);
    const handler = vi.fn();
    api.setSessionInvalidatedHandler(handler);
    const mock = installFetchMock();
    mock.queue({ status: 401, body: ERROR_401 });
    mock.queue({ status: 401, body: ERROR_401 }); // account refresh fails, no further tier

    await expect(api.enterWorkspace("ws9")).rejects.toBeInstanceOf(SessionExpired);

    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does NOT fire on an explicit logout() -- that's not a surprise expiry", async () => {
    api.setSession(WORKSPACE_SESSION);
    const handler = vi.fn();
    api.setSessionInvalidatedHandler(handler);
    const mock = installFetchMock();
    mock.queue({ status: 200, body: { status: "logged_out" } });
    mock.queue({ status: 200, body: { status: "logged_out" } });

    await api.logout();

    expect(handler).not.toHaveBeenCalled();
  });

  it("does NOT fire on the setSession(null) test/store hook", () => {
    api.setSession(WORKSPACE_SESSION);
    const handler = vi.fn();
    api.setSessionInvalidatedHandler(handler);

    api.setSession(null);

    expect(handler).not.toHaveBeenCalled();
  });

  it("deregisters cleanly when passed null -- a later expiry has nothing to call", async () => {
    api.setSession(WORKSPACE_SESSION);
    const handler = vi.fn();
    api.setSessionInvalidatedHandler(handler);
    api.setSessionInvalidatedHandler(null);
    const mock = installFetchMock();
    mock.queue({ status: 401, body: ERROR_401 });
    mock.queue({ status: 401, body: ERROR_401 });
    mock.queue({ status: 401, body: ERROR_401 });

    await expect(api.channels()).rejects.toBeInstanceOf(SessionExpired);

    expect(handler).not.toHaveBeenCalled();
  });
});

describe("error mapping: server envelope code -> SmacError subclass", () => {
  it.each([
    ["email_taken", 409, NameTakenError],
    ["rate_limited", 429, RateLimitedError],
    ["invalid_message", 422, ValidationError],
    ["conflict", 409, SmacError], // unmapped code falls through to the base class
  ] as const)("maps envelope code %s (status %i) to %s", async (code, status, expectedClass) => {
    const mock = installFetchMock();
    mock.queue({ status, body: { error: { code, message: "boom" } } });

    await expect(api.signup("dup@example.com", "hunter2000")).rejects.toBeInstanceOf(
      expectedClass
    );
  });

  it("raises Unreachable when fetch itself rejects (no HTTP response at all)", async () => {
    const mock = installFetchMock();
    mock.queueNetworkError();

    await expect(api.meta()).rejects.toBeInstanceOf(Unreachable);
  });

  it("falls back to a generic http_error when the error body isn't envelope-shaped", async () => {
    const mock = installFetchMock();
    mock.queue({ status: 500, body: { oops: "not an envelope" } });

    const error = await api.meta().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(SmacError);
    expect((error as SmacError).code).toBe("http_error");
  });
});

describe("logout: best-effort revoke at both tiers, unconditional local clear", () => {
  it("revokes workspace and account refresh tokens, then clears locally", async () => {
    api.setSession(WORKSPACE_SESSION);
    const mock = installFetchMock();
    mock.queue({ status: 200, body: { status: "logged_out" } }); // workspace /auth/logout
    mock.queue({ status: 200, body: { status: "logged_out" } }); // account /auth/logout

    await api.logout();

    expect(mock.calls[0]).toMatchObject({
      url: "/auth/logout",
      body: { refresh_token: "workspace-refresh-1" },
    });
    expect(mock.calls[0].headers.Authorization).toBe("Bearer workspace-access-1");
    expect(mock.calls[1]).toMatchObject({
      url: "/auth/logout",
      body: { refresh_token: "account-refresh-1" },
    });
    expect(api.getSession()).toBeNull();
    expect(window.localStorage.getItem("smac.session")).toBeNull();
  });

  it("still clears the local session even when both server revoke calls fail", async () => {
    api.setSession(WORKSPACE_SESSION);
    const mock = installFetchMock();
    mock.queueNetworkError();
    mock.queueNetworkError();

    await api.logout();

    expect(api.getSession()).toBeNull();
    expect(window.localStorage.getItem("smac.session")).toBeNull();
  });
});

describe("WebSocket URLs: refresh-first, ws(s) scheme derived from location", () => {
  it("wsChannelUrl refreshes the workspace token first, then embeds it as ?token=", async () => {
    api.setSession(WORKSPACE_SESSION);
    const mock = installFetchMock();
    mock.queue({ status: 200, body: TOKEN_PAIR("fresh-wat", "fresh-wrt") }); // unconditional refresh

    const url = await api.wsChannelUrl("c1");

    expect(mock.calls[0]).toMatchObject({ url: "/auth/refresh" });
    expect(url).toBe(`ws://${window.location.host}/ws/workspaces/ws1/channels/c1?token=fresh-wat`);
  });

  it("wsEventsUrl builds the per-member events feed URL", async () => {
    api.setSession(WORKSPACE_SESSION);
    const mock = installFetchMock();
    mock.queue({ status: 200, body: TOKEN_PAIR("fresh-wat", "fresh-wrt") });

    const url = await api.wsEventsUrl();

    expect(url).toBe(`ws://${window.location.host}/ws/workspaces/ws1/members/me/events?token=fresh-wat`);
  });
});
