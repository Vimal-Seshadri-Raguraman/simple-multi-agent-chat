import { useEffect, useState } from "react";
import * as api from "../lib/api";
import { CLIENT_VERSION } from "../version";

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
 * Deliberately minimal: a fixed-position `<div>`, no animation, no
 * portal. Task 4 owns the real Toast component and is expected to unify
 * this with it -- do not invest further in this one's visuals.
 */
export default function VersionBanner() {
  const [serverVersion, setServerVersion] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [updateAvailable, setUpdateAvailable] = useState(false);

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
            setUpdateAvailable(true);
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
      {updateAvailable && (
        <div
          className="version-toast"
          role="status"
          style={{ position: "fixed", bottom: "1rem", right: "1rem", zIndex: 1000 }}
        >
          SMAC updated — refresh
        </div>
      )}
    </>
  );
}
