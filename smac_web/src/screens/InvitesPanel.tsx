import { useState } from "react";
import * as api from "../lib/api";
import { errorMessage } from "../lib/errors";

/**
 * Settings' Invites panel (web spec §2: "mint shareable code -> copy +
 * the Bob instructions line; email invites listed as API-only for now").
 * Mints a fresh multi-use code (`api.mintInviteCode()`) on demand -- no
 * list of past codes here (email invites/history are API-only per spec).
 *
 * The "Bob instructions line" mirrors `smac_cli/commands.py`'s own invite
 * command copy (`"tell them: smac → /register → /join {code}"`), adapted
 * to the web surface's equivalent path (there's no `/join` CLI verb in a
 * browser -- the Join screen's "have an invite code?" field is it).
 */
export default function InvitesPanel() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function handleMint() {
    setPending(true);
    setError(null);
    try {
      const invite = await api.mintInviteCode();
      setCode(invite.code);
      setCopied(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPending(false);
    }
  }

  async function copyCode() {
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
    } catch {
      // Best-effort -- the mono block still lets the reader select-and-copy.
    }
  }

  return (
    <div className="invites-panel">
      <h2>Invites</h2>
      <p className="invites-panel__hint">
        Mint a shareable code so a teammate can join this workspace. Anyone with the code can
        join without an admin approving each request.
      </p>
      <button type="button" className="btn btn--primary" onClick={() => void handleMint()} disabled={pending}>
        {pending ? "Minting…" : "Mint invite code"}
      </button>
      {error && (
        <p role="alert" className="invites-panel__error">
          {error}
        </p>
      )}
      {code && (
        <div className="invites-panel__result">
          <pre className="invites-panel__code" data-testid="invite-code">
            <code>{code}</code>
          </pre>
          <button type="button" className="btn btn--quiet btn--sm" onClick={() => void copyCode()}>
            {copied ? "Copied!" : "Copy code"}
          </button>
          <p className="invites-panel__bob">
            Tell them: sign up, then paste this code under &quot;Have an invite code?&quot; on the
            join screen — <span className="mono">{code}</span>.
          </p>
        </div>
      )}
    </div>
  );
}
