/**
 * The one place `smac_web` speaks HTTP to `app/`'s REST surface --
 * everything else (stores, screens) goes through this module rather
 * than touching `fetch` directly. A TS port of `smac_cli/api.py`'s
 * `SmacApi`: same two-tier token model (ACCOUNT tokens, global, no
 * workspace; WORKSPACE tokens, member-scoped) and the SAME refresh-chain
 * semantics (workspace 401 -> workspace refresh -> account-refresh
 * fallback -> `SessionExpired` + clear), ported faithfully rather than
 * reinvented (`smac_cli/api.py`'s module docstring + `_recover_
 * workspace_session`'s docstring are the canonical descriptions of the
 * chain this file mirrors).
 *
 * Unlike `SmacApi` (an instantiable class -- the TUI can hold several,
 * one per connected server), this module is a browser-app singleton:
 * one tab talks to exactly one server (its own origin, spec §1 --
 * same-origin, no CORS), so there is exactly one live session, held in
 * module-level state and mirrored to `localStorage` (`lib/session.ts`)
 * on every mutation.
 *
 * Every exported function is async and raises a `SmacError` subclass
 * (`lib/errors.ts`) instead of letting a raw `fetch` rejection or error
 * envelope escape.
 */

import { NoWorkspaceError, SessionExpired, Unreachable, fromEnvelope } from "./errors";
import { type Session, clearSession, loadSession, saveSession } from "./session";
import type {
  ChannelOut,
  InviteOut,
  Membership,
  MemberOut,
  MemberRegisterOut,
  MemberSelfOut,
  MessagePayload,
  MetaOut,
  TokenPairOut,
  UnreadsOut,
  UnreadsRowOut,
  WorkspaceOut,
  WorkspaceSearchOut,
} from "./types";

export type { Session } from "./session";
export type * from "./types";

/** Mirrors `smac_cli.api.DEFAULT_MESSAGE_LIMIT` -- the server's own
 * `MAX_LIMIT` (`app/routers/messages.py`) clamps anything higher anyway. */
export const DEFAULT_MESSAGE_LIMIT = 15;

const _DELETE_CONFIRMATION = "delete";

// -- module-held session state -------------------------------------------

let currentSession: Session | null = loadSession();

/** The live session, or `null` if logged out. */
export function getSession(): Session | null {
  return currentSession;
}

/** Test/store hook: replace the in-memory + persisted session directly
 * (e.g. hydrating the auth store from a session restored elsewhere, or a
 * test priming a workspace-tier session before exercising an authed
 * call). Passing `null` is equivalent to a full local logout. */
export function setSession(session: Session | null): void {
  if (session === null) {
    invalidateSession();
    return;
  }
  currentSession = session;
  saveSession(session);
}

function invalidateSession(): void {
  currentSession = null;
  clearSession();
}

// -- low-level plumbing ---------------------------------------------------

type SendOpts = {
  jsonBody?: unknown;
  params?: Record<string, string | number | undefined>;
  bearer?: string;
};

function buildUrl(path: string, params?: Record<string, string | number | undefined>): string {
  if (!params) {
    return path;
  }
  const usp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) {
      usp.set(key, String(value));
    }
  }
  const qs = usp.toString();
  return qs ? `${path}?${qs}` : path;
}

/**
 * Issue one HTTP request, translating connection failures. Any failure
 * that never produced an HTTP response (refused connection, DNS
 * failure, timeout, offline, ...) becomes `Unreachable` -- the one error
 * class that isn't mapped from a server envelope, mirroring `SmacApi.
 * _send`'s handling of `httpx.TransportError`.
 */
async function send(method: string, path: string, opts: SendOpts = {}): Promise<Response> {
  const url = buildUrl(path, opts.params);
  const headers: Record<string, string> = {};
  if (opts.jsonBody !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (opts.bearer) {
    headers.Authorization = `Bearer ${opts.bearer}`;
  }
  try {
    return await fetch(url, {
      method,
      headers,
      body: opts.jsonBody !== undefined ? JSON.stringify(opts.jsonBody) : undefined,
    });
  } catch {
    throw new Unreachable(currentSession?.url ?? window.location.origin);
  }
}

/**
 * Return a successful response's JSON body, or raise a typed error. A
 * 2xx with an empty body returns `null`. A non-2xx is expected to carry
 * the standard `{"error": {"code", "message"}}` envelope (`app/main.py`'s
 * exception handlers); anything that doesn't parse that way still
 * raises a `SmacError`, just without a specific server-provided code.
 */
async function parseResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  if (response.status < 400) {
    return (text ? JSON.parse(text) : null) as T;
  }
  let code = "http_error";
  let message = text || `HTTP ${response.status}`;
  try {
    const body = JSON.parse(text) as { error?: { code?: unknown; message?: unknown } };
    if (body.error && body.error.code !== undefined && body.error.message !== undefined) {
      code = String(body.error.code);
      message = String(body.error.message);
    }
  } catch {
    // not JSON (or not envelope-shaped) -- keep the fallback set above
  }
  throw fromEnvelope(code, message);
}

// -- refresh chain ----------------------------------------------------
//
// Two independent token pairs can each be rotated: `tryRefreshWorkspace`/
// `tryRefreshAccount`. Neither invalidates the session on failure by
// itself -- they just report success/failure -- so `recoverWorkspaceSession`
// can fall through from a failed workspace refresh to an account-refresh-
// and-re-mint attempt before giving up. Only the top-level callers
// (`authedRequest`, `accountAuthedRequest`, `recoverWorkspaceSession`)
// ever actually invalidate.
//
// `withRefreshLock` serializes refresh attempts the way `SmacApi.
// _refresh_lock` does for concurrent worker threads in the TUI: a
// browser tab has no threads, but concurrent in-flight requests can
// still race two 401s into two refresh attempts for the SAME token, and
// only the first redemption of a rotating refresh token succeeds
// server-side. Chaining through one promise serializes them; the
// "presented token still current?" recheck after acquiring the lock
// reports a loser's redundant attempt as a (no-op) success rather than
// re-presenting an already-spent token to the server.

let refreshChain: Promise<unknown> = Promise.resolve();

function withRefreshLock<T>(fn: () => Promise<T>): Promise<T> {
  const result = refreshChain.then(fn, fn);
  refreshChain = result.then(
    () => undefined,
    () => undefined
  );
  return result;
}

async function tryRefreshWorkspace(): Promise<boolean> {
  if (currentSession === null || currentSession.workspaceRefresh === undefined) {
    return false;
  }
  const presented = currentSession.workspaceRefresh;
  return withRefreshLock(async () => {
    if (currentSession === null) {
      return false;
    }
    if (currentSession.workspaceRefresh !== presented) {
      return true; // a concurrent call already refreshed this
    }
    const response = await send("POST", "/auth/refresh", {
      jsonBody: { refresh_token: presented },
    });
    if (response.status !== 200) {
      return false;
    }
    const data = (await response.json()) as TokenPairOut;
    currentSession = {
      ...currentSession,
      workspaceAccess: data.access_token,
      workspaceRefresh: data.refresh_token,
    };
    saveSession(currentSession);
    return true;
  });
}

async function tryRefreshAccount(): Promise<boolean> {
  if (currentSession === null) {
    return false;
  }
  const presented = currentSession.accountRefresh;
  return withRefreshLock(async () => {
    if (currentSession === null) {
      return false;
    }
    if (currentSession.accountRefresh !== presented) {
      return true; // a concurrent call already refreshed this
    }
    const response = await send("POST", "/auth/refresh", {
      jsonBody: { refresh_token: presented },
    });
    if (response.status !== 200) {
      return false;
    }
    const data = (await response.json()) as TokenPairOut;
    currentSession = {
      ...currentSession,
      accountAccess: data.access_token,
      accountRefresh: data.refresh_token,
    };
    saveSession(currentSession);
    return true;
  });
}

/**
 * A workspace-tier request 401'd: try to make the session usable again,
 * or raise `SessionExpired` (and wipe the session) once every option is
 * exhausted. The chain, EXACTLY `SmacApi._recover_workspace_session`'s
 * (binding, per the task brief): workspace refresh -> account-refresh
 * fallback (rotate the account pair, then re-mint a fresh workspace pair
 * via `POST /workspaces/{id}/token`) -> `SessionExpired`.
 */
async function recoverWorkspaceSession(): Promise<void> {
  if (await tryRefreshWorkspace()) {
    return;
  }
  if (currentSession !== null && currentSession.workspaceId !== undefined) {
    const workspaceId = currentSession.workspaceId;
    if (await tryRefreshAccount() && currentSession !== null) {
      const response = await send("POST", `/workspaces/${workspaceId}/token`, {
        bearer: currentSession.accountAccess,
      });
      if (response.status === 200) {
        const data = (await response.json()) as TokenPairOut;
        if (currentSession !== null) {
          currentSession = {
            ...currentSession,
            workspaceId,
            workspaceAccess: data.access_token,
            workspaceRefresh: data.refresh_token,
          };
          saveSession(currentSession);
          return;
        }
      }
    }
  }
  invalidateSession();
  throw new SessionExpired();
}

/**
 * Issue a WORKSPACE-tier authenticated request, recovering-and-retrying
 * once on a 401. Raises `NoWorkspaceError` immediately (no request sent)
 * if the session has no workspace token yet. A 401 on the first attempt
 * triggers exactly one `recoverWorkspaceSession()` + retry; a 401 on the
 * retry (or a failed recovery) raises `SessionExpired` and clears the
 * saved session -- recovery is never attempted more than once per call.
 */
async function authedRequest<T>(
  method: string,
  path: string,
  opts: { jsonBody?: unknown; params?: Record<string, string | number | undefined> } = {}
): Promise<T> {
  if (currentSession === null) {
    throw new SessionExpired("No active session.");
  }
  if (currentSession.workspaceAccess === undefined) {
    throw new NoWorkspaceError();
  }
  let response = await send(method, path, { ...opts, bearer: currentSession.workspaceAccess });
  if (response.status === 401) {
    await recoverWorkspaceSession(); // raises SessionExpired on failure
    if (currentSession === null || currentSession.workspaceAccess === undefined) {
      throw new SessionExpired();
    }
    response = await send(method, path, { ...opts, bearer: currentSession.workspaceAccess });
    if (response.status === 401) {
      invalidateSession();
      throw new SessionExpired();
    }
  }
  return parseResponse<T>(response);
}

/**
 * The ACCOUNT-tier twin of `authedRequest`: one refresh-and-retry on a
 * 401, via `tryRefreshAccount` only -- there is no further fallback tier
 * above "account", so a failed refresh here goes straight to
 * `SessionExpired`.
 */
async function accountAuthedRequest<T>(
  method: string,
  path: string,
  opts: { jsonBody?: unknown; params?: Record<string, string | number | undefined> } = {}
): Promise<T> {
  if (currentSession === null) {
    throw new SessionExpired("No active session.");
  }
  let response = await send(method, path, { ...opts, bearer: currentSession.accountAccess });
  if (response.status === 401) {
    if (!(await tryRefreshAccount())) {
      invalidateSession();
      throw new SessionExpired();
    }
    if (currentSession === null) {
      throw new SessionExpired();
    }
    response = await send(method, path, { ...opts, bearer: currentSession.accountAccess });
    if (response.status === 401) {
      invalidateSession();
      throw new SessionExpired();
    }
  }
  return parseResponse<T>(response);
}

function requireWorkspaceId(): string {
  if (currentSession === null) {
    throw new SessionExpired("No active session.");
  }
  if (currentSession.workspaceId === undefined) {
    throw new NoWorkspaceError();
  }
  return currentSession.workspaceId;
}

type WorkspaceAuthOut = TokenPairOut & { member: MemberSelfOut; workspace: WorkspaceOut };

/**
 * Fold a `WorkspaceAuthOut`-shaped response (every workspace-birth door:
 * `POST /workspaces`, `.../register`, `/workspaces/join`) into the
 * CURRENT session -- account fields/email untouched, workspace fields
 * overwritten.
 */
function applyWorkspaceAuthOut(data: WorkspaceAuthOut): {
  session: Session;
  workspaceName: string;
} {
  if (currentSession === null) {
    throw new SessionExpired("No active session.");
  }
  currentSession = {
    ...currentSession,
    workspaceId: data.workspace.workspace_id,
    workspaceAccess: data.access_token,
    workspaceRefresh: data.refresh_token,
  };
  saveSession(currentSession);
  return { session: currentSession, workspaceName: data.workspace.workspace_name };
}

// -- unauthenticated endpoints ---------------------------------------

/** `GET /meta`: the server/API version handshake. */
export async function meta(): Promise<MetaOut> {
  return parseResponse<MetaOut>(await send("GET", "/meta"));
}

/** `GET /workspaces/search`: public workspaces matching `q` (or all, if blank). */
export async function searchPublic(q = ""): Promise<WorkspaceSearchOut[]> {
  const response = await send("GET", "/workspaces/search", {
    params: q ? { name: q } : undefined,
  });
  return parseResponse<WorkspaceSearchOut[]>(response);
}

// -- account-birth doors ------------------------------------------------

/**
 * `POST /accounts`: create a global account. Account-tier tokens only --
 * no workspace yet, so `workspaceId`/`workspaceAccess`/`workspaceRefresh`
 * are all absent from the returned session until a workspace door mints
 * them in.
 */
export async function signup(email: string, password: string): Promise<Session> {
  const response = await send("POST", "/accounts", { jsonBody: { email, password } });
  const data = await parseResponse<{ tokens: TokenPairOut }>(response);
  const session: Session = {
    url: window.location.origin,
    email,
    accountAccess: data.tokens.access_token,
    accountRefresh: data.tokens.refresh_token,
  };
  currentSession = session;
  saveSession(session);
  return session;
}

/**
 * `POST /accounts/login`: global login -- no `workspace_id` needed.
 * Returns the saved account-only session plus every workspace this
 * account already has a profile in.
 */
export async function login(
  email: string,
  password: string
): Promise<{ session: Session; workspaces: Membership[] }> {
  const response = await send("POST", "/accounts/login", { jsonBody: { email, password } });
  const data = await parseResponse<{ tokens: TokenPairOut; workspaces: Membership[] }>(response);
  const session: Session = {
    url: window.location.origin,
    email,
    accountAccess: data.tokens.access_token,
    accountRefresh: data.tokens.refresh_token,
  };
  currentSession = session;
  saveSession(session);
  return { session, workspaces: data.workspaces };
}

// -- account-tier endpoints -------------------------------------------

/**
 * `POST /workspaces/{id}/token` (account bearer): exchange the account
 * token for a fresh WORKSPACE token pair for a workspace this account
 * already belongs to, minting it into the current session.
 */
export async function enterWorkspace(workspaceId: string): Promise<void> {
  const data = await accountAuthedRequest<TokenPairOut>(
    "POST",
    `/workspaces/${workspaceId}/token`
  );
  if (currentSession === null) {
    throw new SessionExpired();
  }
  currentSession = {
    ...currentSession,
    workspaceId,
    workspaceAccess: data.access_token,
    workspaceRefresh: data.refresh_token,
  };
  saveSession(currentSession);
}

/**
 * `POST /workspaces` (account bearer): found a brand-new workspace,
 * linking the caller's existing account as its admin.
 */
export async function createWorkspace(
  name: string,
  visibility: "public" | "private",
  firstName: string,
  lastName: string
): Promise<{ session: Session; workspaceName: string }> {
  const data = await accountAuthedRequest<WorkspaceAuthOut>("POST", "/workspaces", {
    jsonBody: {
      workspace_name: name,
      visibility,
      display_first_name: firstName,
      display_last_name: lastName,
    },
  });
  return applyWorkspaceAuthOut(data);
}

/**
 * `POST /workspaces/{id}/register` (account bearer): join a workspace
 * directly by id (public: open door; private: only with a reserved
 * seat matching the caller's account email).
 */
export async function joinPublic(
  workspaceId: string,
  firstName: string,
  lastName: string
): Promise<{ session: Session; workspaceName: string }> {
  const data = await accountAuthedRequest<WorkspaceAuthOut>(
    "POST",
    `/workspaces/${workspaceId}/register`,
    { jsonBody: { first_name: firstName, last_name: lastName } }
  );
  return applyWorkspaceAuthOut(data);
}

/** `POST /workspaces/join` (account bearer): join by shareable invite code. */
export async function joinCode(
  code: string,
  firstName: string,
  lastName: string
): Promise<{ session: Session; workspaceName: string }> {
  const data = await accountAuthedRequest<WorkspaceAuthOut>("POST", "/workspaces/join", {
    jsonBody: { code, first_name: firstName, last_name: lastName },
  });
  return applyWorkspaceAuthOut(data);
}

// -- workspace-tier endpoints ------------------------------------------

/** `GET /members/me`: the logged-in member's own full profile. */
export async function whoami(): Promise<MemberSelfOut> {
  return authedRequest<MemberSelfOut>("GET", "/members/me");
}

/** `GET /workspaces/{workspace_id}/channels`: every channel in the workspace. */
export async function channels(): Promise<ChannelOut[]> {
  const workspaceId = requireWorkspaceId();
  return authedRequest<ChannelOut[]>("GET", `/workspaces/${workspaceId}/channels`);
}

/** `GET /workspaces/{workspace_id}/unreads`: per-channel unread state. */
export async function unreads(): Promise<UnreadsOut> {
  const workspaceId = requireWorkspaceId();
  return authedRequest<UnreadsOut>("GET", `/workspaces/${workspaceId}/unreads`);
}

/** `GET /workspaces/{workspace_id}/members`: every member in the workspace. */
export async function members(): Promise<MemberOut[]> {
  const workspaceId = requireWorkspaceId();
  return authedRequest<MemberOut[]>("GET", `/workspaces/${workspaceId}/members`);
}

/** `POST /workspaces/{workspace_id}/channels`: create a new channel. */
export async function createChannel(name: string): Promise<ChannelOut> {
  const workspaceId = requireWorkspaceId();
  return authedRequest<ChannelOut>("POST", `/workspaces/${workspaceId}/channels`, {
    jsonBody: { channel_name: name },
  });
}

/** `GET .../channels/{channel_id}/messages`: a page of message history. */
export async function messages(
  channelId: string,
  after?: string,
  limit: number = DEFAULT_MESSAGE_LIMIT
): Promise<MessagePayload[]> {
  const workspaceId = requireWorkspaceId();
  const params: Record<string, string | number | undefined> = { limit };
  if (after !== undefined) {
    params.after = after;
  }
  return authedRequest<MessagePayload[]>(
    "GET",
    `/workspaces/${workspaceId}/channels/${channelId}/messages`,
    { params }
  );
}

/** `POST .../channels/{channel_id}/messages`: post a message. */
export async function post(channelId: string, text: string): Promise<MessagePayload> {
  const workspaceId = requireWorkspaceId();
  return authedRequest<MessagePayload>(
    "POST",
    `/workspaces/${workspaceId}/channels/${channelId}/messages`,
    { jsonBody: { message_text: text } }
  );
}

/**
 * `POST .../channels/{channel_id}/read`: advance the read cursor.
 * `anchor` omitted (or `undefined`) means "caught up to latest",
 * matching the server's `last_read_message_id: null` convention.
 */
export async function markRead(channelId: string, anchor?: string): Promise<UnreadsRowOut> {
  const workspaceId = requireWorkspaceId();
  return authedRequest<UnreadsRowOut>(
    "POST",
    `/workspaces/${workspaceId}/channels/${channelId}/read`,
    { jsonBody: { last_read_message_id: anchor ?? null } }
  );
}

/** `POST /members/agents` with `member_name`: create a brand-new agent. */
export async function createAgent(name: string): Promise<MemberRegisterOut> {
  return authedRequest<MemberRegisterOut>("POST", "/members/agents", {
    jsonBody: { member_name: name },
  });
}

/** `POST /members/agents` with `account_id`: attach an existing agent account. */
export async function attachAgent(accountId: string): Promise<MemberRegisterOut> {
  return authedRequest<MemberRegisterOut>("POST", "/members/agents", {
    jsonBody: { account_id: accountId },
  });
}

/**
 * `POST /workspaces/{workspace_id}/invites` (admin): mint a fresh
 * shareable multi-use join code.
 */
export async function mintInviteCode(): Promise<InviteOut> {
  const workspaceId = requireWorkspaceId();
  return authedRequest<InviteOut>("POST", `/workspaces/${workspaceId}/invites`, {
    jsonBody: { invite_type: "code" },
  });
}

/** `DELETE /workspaces/{workspace_id}?confirm=delete`: destroy the workspace. */
export async function deleteWorkspace(): Promise<{ status: string }> {
  const workspaceId = requireWorkspaceId();
  return authedRequest<{ status: string }>("DELETE", `/workspaces/${workspaceId}`, {
    params: { confirm: _DELETE_CONFIRMATION },
  });
}

/**
 * Best-effort revoke of every refresh token the current session holds
 * (`POST /auth/logout` at whichever tier(s) are present), then an
 * unconditional local clear -- a failed/unreachable revoke must never
 * leave the browser still "logged in" locally.
 */
export async function logout(): Promise<void> {
  const session = currentSession;
  if (session !== null) {
    if (session.workspaceAccess !== undefined && session.workspaceRefresh !== undefined) {
      try {
        await send("POST", "/auth/logout", {
          jsonBody: { refresh_token: session.workspaceRefresh },
          bearer: session.workspaceAccess,
        });
      } catch {
        // best-effort -- local logout proceeds regardless
      }
    }
    try {
      await send("POST", "/auth/logout", {
        jsonBody: { refresh_token: session.accountRefresh },
        bearer: session.accountAccess,
      });
    } catch {
      // best-effort, see above
    }
  }
  invalidateSession();
}

// -- WebSocket URLs ---------------------------------------------------

function wsBaseUrl(path: string): string {
  if (currentSession === null || currentSession.workspaceAccess === undefined) {
    throw new SessionExpired();
  }
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${path}?token=${currentSession.workspaceAccess}`;
}

/**
 * The `ws(s)://` URL for a channel's live message feed, with a fresh
 * token. Refreshes unconditionally first (mirroring `SmacApi._ws_url`):
 * a WebSocket authenticates only once, at connect time, so there is no
 * retry-on-401 available once it's open -- this is what makes the
 * "fresh token" guarantee hold even right after a session was restored
 * from `localStorage`, when the access token may already be stale.
 */
export async function wsChannelUrl(channelId: string): Promise<string> {
  const workspaceId = requireWorkspaceId();
  await recoverWorkspaceSession();
  return wsBaseUrl(`/ws/workspaces/${workspaceId}/channels/${channelId}`);
}

/** The `ws(s)://` URL for the caller's private mention-events feed. */
export async function wsEventsUrl(): Promise<string> {
  const workspaceId = requireWorkspaceId();
  await recoverWorkspaceSession();
  return wsBaseUrl(`/ws/workspaces/${workspaceId}/members/me/events`);
}
