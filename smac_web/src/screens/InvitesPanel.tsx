import { useState } from "react";
import * as api from "../lib/api";
import type { InviteOut } from "../lib/api";
import { errorMessage } from "../lib/errors";

/**
 * Settings' Invites panel (web spec §2: "mint shareable code -> copy +
 * the Bob instructions line"; SMAC-92 Task 4 adds the agent-invite twin).
 * Mints a fresh multi-use code (`api.mintInvite(kind)`) on demand -- no
 * list of past codes here (email invites/history are API-only per spec).
 *
 * Two independent sections, each shown only when the caller actually
 * holds the capability that mints it (`Settings.tsx` already gates
 * WHETHER this whole panel is reachable on "holds at least one of the two
 * mint caps" -- this component still re-checks per-section, since an
 * `agent_admin` has `mint_agent_invites` but NOT `mint_human_invites`,
 * and showing a button that would just 403 is worse than not showing it):
 * "Invite a person" (`Cap.MINT_HUMAN_INVITES`) mints `invite_type: "code"`
 * exactly as before this task; "Invite an agent" (`Cap.
 * MINT_AGENT_INVITES`) mints `invite_type: "agent_code"`, redeemable only
 * via the unauthenticated `POST /agents/join` (spec's agent-invite flow).
 *
 * The "Bob instructions line" mirrors `smac_cli/commands.py`'s own invite
 * command copy (`"tell them: smac → /register → /join {code}"`), adapted
 * to the web surface's equivalent path (there's no `/join` CLI verb in a
 * browser -- the Join screen's "have an invite code?" field is it).
 */

export type InvitesPanelProps = {
  canMintHuman: boolean;
  canMintAgent: boolean;
};

type MintState = {
  pending: boolean;
  error: string | null;
  invite: InviteOut | null;
  copied: boolean;
};

const IDLE_STATE: MintState = { pending: false, error: null, invite: null, copied: false };

function MintSection({
  kind,
  title,
  hint,
  bobLine,
}: {
  kind: "human" | "agent";
  title: string;
  hint: string;
  bobLine: (code: string) => string;
}) {
  const [state, setState] = useState<MintState>(IDLE_STATE);

  async function handleMint() {
    setState({ ...IDLE_STATE, pending: true });
    try {
      const invite = await api.mintInvite(kind);
      setState({ pending: false, error: null, invite, copied: false });
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
          <button type="button" className="btn btn--quiet btn--sm" onClick={() => void copyCode()}>
            {state.copied ? "Copied!" : "Copy code"}
          </button>
          <p className="invites-panel__bob">{bobLine(state.invite.code)}</p>
        </div>
      )}
    </section>
  );
}

export default function InvitesPanel({ canMintHuman, canMintAgent }: InvitesPanelProps) {
  return (
    <div className="invites-panel">
      <h2>Invites</h2>
      {canMintHuman && (
        <MintSection
          kind="human"
          title="Invite a person"
          hint="Mint a shareable code so a teammate can join this workspace. Anyone with the code can join without an admin approving each request."
          bobLine={(code) =>
            `Tell them: sign up, then paste this code under "Have an invite code?" on the join screen — ${code}.`
          }
        />
      )}
      {canMintAgent && (
        <MintSection
          kind="agent"
          title="Invite an agent"
          hint="Mint a shareable code so a bot can join this workspace with its own handle and API key. Redeemable without an account, at /agents/join."
          bobLine={(code) => `Give the agent this code to redeem at /agents/join — ${code}.`}
        />
      )}
      {!canMintHuman && !canMintAgent && (
        <p className="invites-panel__empty">You don&apos;t have permission to mint invites.</p>
      )}
    </div>
  );
}
