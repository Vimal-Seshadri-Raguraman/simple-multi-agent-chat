import { useEffect, useState } from "react";
import * as api from "../lib/api";
import { CLIENT_VERSION } from "../version";
import Toast, { useToastQueue } from "./Toast";

/**
 * Server/client version handshake (web spec §2): `/meta` on mount, and
 * again on every window `focus` (a tab left open across a server
 * restart/redeploy should notice). Two independent signals:
 *  - a client/server version MISMATCH at load time -> a dismissible
 *    banner (informational -- the app still works, just possibly stale).
 *  - the server's OWN version changing since load (seen on a later focus
 *    poll -- i.e. the server was redeployed while this tab sat open) ->
 *    a "SMAC updated — refresh" toast, since this tab's already-loaded
 *    JS bundle may now be stale relative to what the server expects.
 *
 * The toast is the shared `Toast` component (task-4 brief) -- the task-2
 * placeholder this replaces explicitly deferred its real toast here.
 * `sticky: true` because this one is actionable (click -> reload the
 * page to pick up the new bundle) rather than a fire-and-forget notice,
 * so it shouldn't vanish on its own before the reader acts on it.
 */
export default function VersionBanner() {
  const [serverVersion, setServerVersion] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const toastQueue = useToastQueue();

  useEffect(() => {
    let cancelled = false;
    let loadedVersion: string | null = null;

    api
      .meta()
      .then((data) => {
        if (cancelled) return;
        loadedVersion = data.server_version;
        setServerVersion(data.server_version);
      })
      .catch(() => {
        // Unreachable at mount -- nothing to compare yet; a later focus
        // poll will pick it up once the server is reachable.
      });

    function onFocus() {
      api
        .meta()
        .then((data) => {
          if (cancelled) return;
          if (loadedVersion !== null && data.server_version !== loadedVersion) {
            toastQueue.push("SMAC updated — refresh", {
              sticky: true,
              onClick: () => window.location.reload(),
            });
          }
        })
        .catch(() => {
          // A transient failure on focus isn't worth surfacing to the user.
        });
    }

    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", onFocus);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const mismatch = serverVersion !== null && serverVersion !== CLIENT_VERSION;

  return (
    <>
      {mismatch && !dismissed && (
        <div className="version-banner" role="status">
          <span>
            Client v{CLIENT_VERSION} / server v{serverVersion} — versions differ.
          </span>
          <button type="button" onClick={() => setDismissed(true)} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}
      <Toast toasts={toastQueue.toasts} onDismiss={toastQueue.dismiss} />
    </>
  );
}
