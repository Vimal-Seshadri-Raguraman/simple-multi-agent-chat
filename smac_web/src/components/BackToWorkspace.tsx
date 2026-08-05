import { useEffect, useState } from "react";
import * as api from "../lib/api";
import { useAuth } from "../state/auth";

/**
 * Fix round 1 (task-5 review finding, Important): `CreateOrJoin`/
 * `JoinScreen` are reachable while a live workspace session already
 * exists underneath -- the Rail switcher's "Create or join a workspace…"
 * entry, and the palette's `/workspace create`/`/join` commands, all land
 * here via a bare `navigateAuthScreen` NAVIGATE (`lib/commands.ts`) that
 * does NOT clear the session, only the screen. Before this fix, neither
 * screen offered any way back except completing a create/join of a
 * DIFFERENT workspace, or a full logout -- stranding the user out of the
 * workspace they started in, a regression from the task-3 Settings
 * stub's working "Back to the room" button.
 *
 * Renders nothing at all (not merely disabled) when there's no workspace
 * to go back to -- `session.workspaceId === undefined` covers both
 * first-run paths that must NOT show this: register step 1 -> create-or-
 * join, and a login landing on create-or-join with 0 memberships. In
 * both, the session is real (account-tier) but has never held a
 * workspace tier, so there is nothing behind this screen to return to.
 *
 * Fetches the workspace's own name for the label (best-effort -- `api.
 * accountMe()` is the same call `AuthedShell.tsx`'s Rail switcher already
 * uses for this exact lookup); falls back to a name-less label rather
 * than blocking the button on that fetch landing.
 */
export default function BackToWorkspace() {
  const { session, navigate } = useAuth();
  const workspaceId = session?.workspaceId;
  const [name, setName] = useState<string | null>(null);

  useEffect(() => {
    if (workspaceId === undefined) {
      return;
    }
    let cancelled = false;
    api
      .accountMe()
      .then((data) => {
        if (cancelled) return;
        const membership = data.memberships.find((m) => m.workspace_id === workspaceId);
        if (membership) {
          setName(membership.workspace_name);
        }
      })
      .catch(() => {
        // Best-effort label only -- the button still works without a name.
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  if (workspaceId === undefined) {
    return null;
  }

  return (
    <button
      type="button"
      className="auth-screen__back-to-workspace"
      onClick={() => navigate("authed")}
    >
      ← Back to {name ?? "your workspace"}
    </button>
  );
}
