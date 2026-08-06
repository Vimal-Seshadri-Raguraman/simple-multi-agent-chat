import { useState } from "react";
import * as api from "../lib/api";
import { errorMessage } from "../lib/errors";
import { useAuth } from "../state/auth";

/**
 * Shown when a login returns >1 workspace membership (web spec §2).
 *
 * Unread hints (name + unread count/mention badge) were scoped as a
 * stretch goal ONLY if trivial to add (task-2 brief): lazily probing
 * every membership via `enterWorkspace` + `unreads()` just to populate a
 * picker is exactly the "too heavy" cost the brief calls out, so this
 * intentionally shows memberships PLAIN -- workspace name + handle only,
 * no probe. Task 3+ can layer live unread badges on top once the app
 * shell's socket/unreads plumbing already exists (redoing that fetch
 * here would be wasted work).
 */
export default function WorkspacePicker() {
  const { memberships, workspaceEntered, setPending, setError, pending, error, logout } =
    useAuth();
  const [enteringId, setEnteringId] = useState<string | null>(null);

  async function handleSelect(workspaceId: string) {
    setEnteringId(workspaceId);
    setPending();
    try {
      await api.enterWorkspace(workspaceId);
      const session = api.getSession();
      if (session) {
        workspaceEntered(session);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setEnteringId(null);
    }
  }

  return (
    <div className="auth-screen auth-screen--workspace-picker">
      <h1>Choose a workspace</h1>
      {error && (
        <p role="alert" className="auth-screen__error">
          {error}
        </p>
      )}
      <ul className="workspace-picker__list">
        {memberships.map((membership) => (
          <li key={membership.workspace_id}>
            <button
              type="button"
              disabled={pending}
              onClick={() => void handleSelect(membership.workspace_id)}
            >
              <span className="workspace-picker__name">{membership.workspace_name}</span>
              <span className="workspace-picker__handle">@{membership.handle}</span>
              {enteringId === membership.workspace_id && pending ? " …" : ""}
            </button>
          </li>
        ))}
      </ul>
      <button type="button" className="btn btn--quiet btn--block" onClick={() => void logout()}>
        Log out
      </button>
    </div>
  );
}
