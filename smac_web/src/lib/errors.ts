/**
 * Typed errors raised by `lib/api.ts`.
 *
 * A straight TS port of `smac_cli/errors.py` -- see that module's
 * docstring for the full rationale. `SmacError.code` mirrors the
 * server's error envelope `{"error": {"code": ..., "message": ...}}`
 * (`app/errors.py`); several distinct server codes intentionally map to
 * the same client-side class because callers only ever need to branch on
 * the *kind* of failure, not the exact server code -- `.code` is kept
 * around verbatim for the rare caller that does care.
 *
 * `Unreachable` and `SessionExpired` are raised client-side (no server
 * response at all, or a failed token refresh) rather than mapped from an
 * envelope.
 */

export class SmacError extends Error {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "SmacError";
    this.code = code;
    // Restore the prototype chain -- TS-compiled classes extending
    // built-ins (Error) lose it under some target/lib combinations.
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/** Bad credentials or an invalid/expired token was rejected outright. */
export class AuthError extends SmacError {}

/** The requested resource (or an invite) doesn't exist -- or the caller
 * isn't allowed to know that it does. */
export class NotFoundError extends SmacError {}

/** The caller isn't a member of the workspace/channel it addressed. */
export class NotAMemberError extends SmacError {}

/** The caller is posting too fast; retry after a short pause. */
export class RateLimitedError extends SmacError {}

/** A workspace/channel/email/handle name collided with an existing one. */
export class NameTakenError extends SmacError {}

/** The request body failed validation, or a required confirmation was missing. */
export class ValidationError extends SmacError {}

/**
 * Refresh-on-401 was attempted and failed too. The caller's saved
 * session has already been cleared from `localStorage` by the time this
 * is raised -- there is nothing left to retry, only a fresh `/login`.
 */
export class SessionExpired extends SmacError {
  constructor(message = "Session expired — please log in again.") {
    super("session_expired", message);
    this.name = "SessionExpired";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/**
 * The caller has a live ACCOUNT session but hasn't entered a workspace
 * yet. Distinct from `SessionExpired`: the account itself is perfectly
 * valid, nothing needs clearing -- the caller just needs to create/join
 * a workspace. Raised purely client-side (never mapped from a server
 * envelope) whenever a workspace-tier call is attempted with no
 * workspace token in the session yet.
 */
export class NoWorkspaceError extends SmacError {
  constructor(
    message = "No workspace yet — create or join one first."
  ) {
    super("no_workspace", message);
    this.name = "NoWorkspaceError";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/**
 * The server could not be reached at all (no HTTP response) -- a
 * connection refused, DNS failure, timeout, etc.
 */
export class Unreachable extends SmacError {
  constructor(url: string) {
    super("unreachable", `SMAC server is not reachable at ${url}`);
    this.name = "Unreachable";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/**
 * Envelope `code` string -> client-side exception class. Codes not
 * listed here (e.g. `forbidden_member_type`, `already_a_member`,
 * `not_workspace_admin`, `last_admin`, `conflict`, `http_error`,
 * `internal_error`) fall through to the `SmacError` base class -- same
 * contract as `smac_cli/errors.py`.
 */
const CODE_TO_CLASS: Record<string, new (code: string, message: string) => SmacError> = {
  unauthorized: AuthError,
  invalid_credentials: AuthError,
  invalid_token: AuthError,
  workspace_token_required: AuthError,
  account_token_required: AuthError,
  not_found: NotFoundError,
  invalid_invite: NotFoundError,
  not_a_member: NotAMemberError,
  rate_limited: RateLimitedError,
  workspace_name_taken: NameTakenError,
  channel_name_taken: NameTakenError,
  email_taken: NameTakenError,
  handle_taken: NameTakenError,
  invalid_message: ValidationError,
  confirmation_required: ValidationError,
};

/**
 * Build the right `SmacError` subclass for a server error envelope.
 * Unrecognized codes (including future ones the client doesn't know
 * about yet) fall back to the `SmacError` base class.
 */
export function fromEnvelope(code: string, message: string): SmacError {
  const cls = CODE_TO_CLASS[code] ?? SmacError;
  return new cls(code, message);
}

/**
 * A screen-friendly message for anything a `try`/`catch` around an
 * `api.ts` call might catch. `SmacError.message` already carries the
 * server's own envelope text (or a client-side equivalent for
 * `Unreachable`/`SessionExpired`/`NoWorkspaceError`), so it's shown
 * as-is; anything that isn't a `SmacError` at all (a genuine bug, not a
 * modeled failure) falls back to a generic message instead of leaking
 * an unfamiliar stack/string into the UI.
 */
export function errorMessage(err: unknown): string {
  if (err instanceof SmacError) {
    return err.message;
  }
  return "Something went wrong. Please try again.";
}
