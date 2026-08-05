import { type FormEvent, useState } from "react";
import * as api from "../lib/api";
import { errorMessage } from "../lib/errors";
import { useAuth } from "../state/auth";

/**
 * Settings' Workspace panel (web spec §2: "visibility toggle (admin),
 * delete (typed name + `delete`, admin) -> back to auth state").
 * `Settings.tsx` only mounts this for an admin (the Workspace tab is
 * absent from the tab list otherwise) -- this component doesn't
 * re-check `is_admin` itself, matching `MembersPanel`'s existing "the
 * caller already gated this" convention.
 *
 * **Visibility toggle:** kept as this component's own local state
 * (seeded from `initialVisibility`, updated from the `PATCH` response) --
 * `state/workspace.tsx`'s `self.workspace_visibility` isn't read anywhere
 * else this session, so there's no reducer action worth adding just to
 * keep it in sync; a fresh page load re-fetches `whoami()` anyway.
 *
 * **Delete (constitution §3's destructive-confirmation rule + task-5
 * brief's mandatory test): the button only enables once the typed name
 * matches the workspace's name exactly AND the typed word is exactly
 * "delete" (case-insensitive, trimmed -- typing "Delete" or "delete "
 * still counts; the server's own `?confirm=delete` query param is the
 * real gate and only ever receives the literal lowercase word,
 * `api.deleteWorkspace()` hardcodes it). On success: `api.
 * deleteWorkspace()` (server-side cascade delete), then `api.
 * clearWorkspaceTier()` (drops the now-dangling workspace tokens from
 * the session, keeping the account tier), then `auth.workspaceLeft(...)`
 * -- lands the WHOLE app on "create-or-join" (task-5 brief: "clearSession
 * -> auth state"), not just this panel; `App.tsx`'s `AppBody` swaps away
 * from `AuthedShell` (and this component with it) the instant `screen`
 * stops being `"authed"`.
 */

export type WorkspacePanelProps = {
  workspaceName: string;
  initialVisibility: "public" | "private" | null;
};

export default function WorkspacePanel({ workspaceName, initialVisibility }: WorkspacePanelProps) {
  const auth = useAuth();
  const [visibility, setVisibility] = useState<"public" | "private">(initialVisibility ?? "private");
  const [visPending, setVisPending] = useState(false);
  const [visError, setVisError] = useState<string | null>(null);

  const [confirmName, setConfirmName] = useState("");
  const [confirmWord, setConfirmWord] = useState("");
  const [deletePending, setDeletePending] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const canDelete =
    confirmName === workspaceName && confirmWord.trim().toLowerCase() === "delete";

  async function handleToggleVisibility() {
    const next = visibility === "public" ? "private" : "public";
    setVisPending(true);
    setVisError(null);
    try {
      const updated = await api.updateWorkspaceVisibility(next);
      setVisibility(updated.visibility === "public" ? "public" : "private");
    } catch (err) {
      setVisError(errorMessage(err));
    } finally {
      setVisPending(false);
    }
  }

  async function handleDelete(event: FormEvent) {
    event.preventDefault();
    if (!canDelete) return;
    setDeletePending(true);
    setDeleteError(null);
    try {
      await api.deleteWorkspace();
      const session = api.clearWorkspaceTier();
      if (session === null) {
        // No session to fall back into -- best-effort full local logout
        // rather than leaving the app in a session-less "authed" limbo.
        await auth.logout();
        return;
      }
      auth.workspaceLeft(session);
    } catch (err) {
      setDeleteError(errorMessage(err));
      setDeletePending(false);
    }
  }

  return (
    <div className="workspace-panel">
      <section className="workspace-panel__section">
        <h2>Visibility</h2>
        <p>
          This workspace is currently <strong>{visibility}</strong>
          {visibility === "public"
            ? " — listed in the public directory; anyone can find and join it."
            : " — hidden from the public directory; joining needs an invite code."}
        </p>
        {visError && (
          <p role="alert" className="workspace-panel__error">
            {visError}
          </p>
        )}
        <button type="button" onClick={() => void handleToggleVisibility()} disabled={visPending}>
          {visPending ? "Updating…" : visibility === "public" ? "Make private" : "Make public"}
        </button>
      </section>

      <section className="workspace-panel__section workspace-panel__section--danger">
        <h2>Delete workspace</h2>
        <p>
          This permanently deletes <strong>{workspaceName}</strong> — every channel, message,
          member, and invite. This cannot be undone.
        </p>
        <form onSubmit={handleDelete}>
          <label htmlFor="workspace-delete-name">
            Type the workspace name (<span className="mono">{workspaceName}</span>) to confirm
          </label>
          <input
            id="workspace-delete-name"
            value={confirmName}
            onChange={(event) => setConfirmName(event.target.value)}
            autoComplete="off"
          />
          <label htmlFor="workspace-delete-word">
            Then type <span className="mono">delete</span>
          </label>
          <input
            id="workspace-delete-word"
            value={confirmWord}
            onChange={(event) => setConfirmWord(event.target.value)}
            autoComplete="off"
          />
          {deleteError && (
            <p role="alert" className="workspace-panel__error">
              {deleteError}
            </p>
          )}
          <button
            type="submit"
            disabled={!canDelete || deletePending}
            className="workspace-panel__delete-button"
          >
            {deletePending ? "Deleting…" : `Delete ${workspaceName}`}
          </button>
        </form>
      </section>
    </div>
  );
}
