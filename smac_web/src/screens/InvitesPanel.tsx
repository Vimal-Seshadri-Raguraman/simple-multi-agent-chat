import { useCallback, useEffect, useState } from "react";
import * as api from "../lib/api";
import type { InviteOut } from "../lib/api";
import { errorMessage } from "../lib/errors";

/**
 * Settings' Invites panel (web spec §2: "mint shareable code -> copy +
 * the Bob instructions line"; SMAC-92 Task 4 added the agent-invite twin;
 * Task 5 completes it: a kind selector for a caller who holds BOTH mint
 * caps, the agent bootstrap line, and the pending-invites list + Revoke).
 *
 * **Kind selector:** `Settings.tsx` already gates WHETHER this whole panel
 * is reachable on "holds at least one of the two mint caps" -- this
 * component still re-checks per-cap, since an `agent_admin` has `Cap.
 * MINT_AGENT_INVITES` but NOT `Cap.MINT_HUMAN_INVITES`. A caller with only
 * ONE mint cap goes straight to that kind's section, no selector shown at
 * all (task-5 brief: "agent-only minting for agent_admin ... no
 * selector"). A caller with BOTH (an admin) gets a small Human/Agent
 * toggle above the mint section, defaulting to Human -- exactly one
 * section renders at a time either way, so there's never a gated mint
 * button visible for a kind the caller can't actually mint.
 *
 * **The agent bootstrap line** mirrors the task-5 brief's exact required
 * copy (`Put this code in your agent's config; its first call is POST
 * /agents/join`) with the endpoint in mono, distinct from the human
 * section's "Bob instructions" line (`smac_cli/commands.py`'s own invite
 * copy, adapted to the web surface's equivalent path -- there's no `/join`
 * CLI verb in a browser, the Join screen's code field is it).
 *
 * **Pending invites + Revoke:** `api.listInvites()`/`api.revokeInvite()`
 * hit the SAME `Cap.MINT_HUMAN_INVITES`-or-`Cap.MINT_AGENT_INVITES` wall
 * server-side (`app/routers/invites.py`'s `_require_any_mint_cap`) as
 * minting itself -- shown whenever either mint section is, listing every
 * type (human code, agent code, email) so an admin can revoke an
 * agent_admin's minted code and vice versa, same "either mint cap manages
 * either kind's list" contract the server already enforces. A fresh mint
 * refreshes this list too, so a just-minted code shows up in both places
 * without a second round trip from the caller.
 */

export type InvitesPanelProps = {
  canMintHuman: boolean;
  canMintAgent: boolean;
};

type InviteKind = "human" | "agent";

type MintState = {
  pending: boolean;
  error: string | null;
  invite: InviteOut | null;
  copied: boolean;
};

const IDLE_STATE: MintState = { pending: false, error: null, invite: null, copied: false };

const INVITE_TYPE_LABELS: Record<string, string> = {
  code: "Human code",
  agent_code: "Agent code",
  email: "Email invite",
};

function formatExpiry(expiresAt: string | null): string | null {
  if (!expiresAt) return null;
  const date = new Date(expiresAt);
  if (Number.isNaN(date.getTime())) return expiresAt;
  return date.toLocaleString();
}

function MintSection({
  kind,
  title,
  hint,
  onMinted,
}: {
  kind: InviteKind;
  title: string;
  hint: string;
  /** Called after a successful mint -- `InvitesPanel` uses this to refresh
   * the pending-invites list so the fresh code shows up there too. */
  onMinted: () => void;
}) {
  const [state, setState] = useState<MintState>(IDLE_STATE);

  async function handleMint() {
    setState({ ...IDLE_STATE, pending: true });
    try {
      const invite = await api.mintInvite(kind);
      setState({ pending: false, error: null, invite, copied: false });
      onMinted();
    } catch (err) {
      setState({ pending: false, error: errorMessage(err), invite: null, copied: false });
    }
  }

  async function copyCode() {
    if (!state.invite?.code) return;
    try {
      await navigator.clipboard.writeText(state.invite.code);
      setState((s) => ({ ...s, copied: true }));
    } catch {
      // Best-effort -- the mono block still lets the reader select-and-copy.
    }
  }

  const expiry = state.invite ? formatExpiry(state.invite.expires_at) : null;

  return (
    <section className="invites-panel__section">
      <h2>{title}</h2>
      <p className="invites-panel__hint">{hint}</p>
      <button
        type="button"
        className="btn btn--primary"
        onClick={() => void handleMint()}
        disabled={state.pending}
      >
        {state.pending ? "Minting…" : `Mint ${kind === "agent" ? "agent " : ""}invite code`}
      </button>
      {state.error && (
        <p role="alert" className="invites-panel__error">
          {state.error}
        </p>
      )}
      {state.invite?.code && (
        <div className="invites-panel__result">
          <pre className="invites-panel__code" data-testid={`invite-code-${kind}`}>
            <code>{state.invite.code}</code>
          </pre>
          {expiry && <p className="invites-panel__expiry">Expires {expiry}</p>}
          <button type="button" className="btn btn--quiet btn--sm" onClick={() => void copyCode()}>
            {state.copied ? "Copied!" : "Copy code"}
          </button>
          {kind === "agent" ? (
            <p className="invites-panel__bob">
              Put this code in your agent&apos;s config; its first call is{" "}
              <span className="mono">POST /agents/join</span>.
            </p>
          ) : (
            <p className="invites-panel__bob">
              Tell them: sign up, then paste this code under &quot;Have an invite code?&quot; on the
              join screen — {state.invite.code}.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

export default function InvitesPanel({ canMintHuman, canMintAgent }: InvitesPanelProps) {
  const bothCaps = canMintHuman && canMintAgent;
  const [kind, setKind] = useState<InviteKind>("human");
  const activeKind: InviteKind = bothCaps ? kind : canMintAgent ? "agent" : "human";
  const canManageInvites = canMintHuman || canMintAgent;

  const [invites, setInvites] = useState<InviteOut[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [revokingId, setRevokingId] = useState<string | null>(null);

  const refreshInvites = useCallback(async () => {
    if (!canManageInvites) return;
    try {
      const list = await api.listInvites();
      // Defensive against a test/mocked `api.listInvites` with no
      // implementation (resolves `undefined`, not `[]`) -- a real server
      // response is always an array.
      setInvites(Array.isArray(list) ? list : []);
      setListError(null);
    } catch (err) {
      setListError(errorMessage(err));
    }
  }, [canManageInvites]);

  useEffect(() => {
    void refreshInvites();
  }, [refreshInvites]);

  async function handleRevoke(inviteId: string) {
    setRevokingId(inviteId);
    setListError(null);
    try {
      await api.revokeInvite(inviteId);
      await refreshInvites();
    } catch (err) {
      setListError(errorMessage(err));
    } finally {
      setRevokingId(null);
    }
  }

  return (
    <div className="invites-panel">
      <h2>Invites</h2>

      {bothCaps && (
        <div className="invites-panel__kind-selector" role="group" aria-label="Invite kind">
          <button
            type="button"
            aria-pressed={activeKind === "human"}
            className={
              activeKind === "human"
                ? "invites-panel__kind-btn invites-panel__kind-btn--active"
                : "invites-panel__kind-btn"
            }
            onClick={() => setKind("human")}
          >
            Human
          </button>
          <button
            type="button"
            aria-pressed={activeKind === "agent"}
            className={
              activeKind === "agent"
                ? "invites-panel__kind-btn invites-panel__kind-btn--active"
                : "invites-panel__kind-btn"
            }
            onClick={() => setKind("agent")}
          >
            Agent
          </button>
        </div>
      )}

      {activeKind === "human" && canMintHuman && (
        <MintSection
          kind="human"
          title="Invite a person"
          hint="Mint a shareable code so a teammate can join this workspace. Anyone with the code can join without an admin approving each request."
          onMinted={() => void refreshInvites()}
        />
      )}
      {activeKind === "agent" && canMintAgent && (
        <MintSection
          kind="agent"
          title="Invite an agent"
          hint="Mint a shareable code so a bot can join this workspace with its own handle and API key. Redeemable without an account, at /agents/join."
          onMinted={() => void refreshInvites()}
        />
      )}
      {!canManageInvites && (
        <p className="invites-panel__empty">You don&apos;t have permission to mint invites.</p>
      )}

      {canManageInvites && (
        <section className="invites-panel__pending">
          <h3>Pending invites</h3>
          {listError && (
            <p role="alert" className="invites-panel__error">
              {listError}
            </p>
          )}
          {invites.length === 0 ? (
            <p className="invites-panel__empty">No pending invites.</p>
          ) : (
            <ul className="invites-panel__pending-list">
              {invites.map((invite) => (
                <li key={invite.invite_id} className="invites-panel__pending-row">
                  <span className="invites-panel__pending-type">
                    {INVITE_TYPE_LABELS[invite.invite_type] ?? invite.invite_type}
                  </span>
                  <span className="invites-panel__pending-detail mono">
                    {invite.code ?? invite.email ?? "—"}
                  </span>
                  <button
                    type="button"
                    className="btn btn--quiet btn--sm"
                    disabled={revokingId === invite.invite_id}
                    onClick={() => void handleRevoke(invite.invite_id)}
                  >
                    {revokingId === invite.invite_id ? "Revoking…" : "Revoke"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
