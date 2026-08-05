/**
 * `Session` persistence over `localStorage` -- the web twin of
 * `smac_cli/api.py`'s `Session` dataclass (chmod-600 JSON file there;
 * `localStorage` here, constitution §7.5: accepted locally, httpOnly-
 * cookie migration is on the hosted backlog).
 *
 * Identity v2 shape: `accountAccess`/`accountRefresh` are always present
 * on a saved session (every login door mints them). The workspace-tier
 * fields are optional -- an account fresh off `signup()`/register-step-1
 * with no workspace yet has a session with only the account pair.
 */

const STORAGE_KEY = "smac.session";

export type Session = {
  /** The server origin this session belongs to (informational -- every
   * `fetch` call in `api.ts` uses same-origin relative paths). */
  url: string;
  accountAccess: string;
  accountRefresh: string;
  workspaceId?: string;
  workspaceAccess?: string;
  workspaceRefresh?: string;
  email: string;
};

function isSession(value: unknown): value is Session {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.url === "string" &&
    typeof candidate.accountAccess === "string" &&
    typeof candidate.accountRefresh === "string" &&
    typeof candidate.email === "string" &&
    (candidate.workspaceId === undefined || typeof candidate.workspaceId === "string") &&
    (candidate.workspaceAccess === undefined ||
      typeof candidate.workspaceAccess === "string") &&
    (candidate.workspaceRefresh === undefined ||
      typeof candidate.workspaceRefresh === "string")
  );
}

/**
 * Read a session back from `localStorage`, or `null` if absent/unreadable.
 * A missing key, unreadable value, corrupt JSON, or JSON missing a
 * required field are all treated the same way -- "no usable saved
 * session" -- rather than throwing, since every caller's fallback is
 * identical (fall through to the logged-out welcome screen).
 */
export function loadSession(): Session | null {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
  if (!raw) {
    return null;
  }
  try {
    const data: unknown = JSON.parse(raw);
    return isSession(data) ? data : null;
  } catch {
    return null;
  }
}

/** Write `session` to `localStorage` as JSON. */
export function saveSession(session: Session): void {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
}

/** Remove any saved session from `localStorage`. */
export function clearSession(): void {
  window.localStorage.removeItem(STORAGE_KEY);
}
