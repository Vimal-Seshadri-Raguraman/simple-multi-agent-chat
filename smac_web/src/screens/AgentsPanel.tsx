import { type FormEvent, useState } from "react";
import * as api from "../lib/api";
import type { MemberOut } from "../lib/api";
import { errorMessage } from "../lib/errors";

/**
 * Settings' Agents panel (web spec §2, constitution §6's "agents panel
 * with one-time key reveal + copy" surface signature): the member
 * directory's agents, filtered client-side from `workspace.members`
 * (already loaded by `state/workspace.tsx` -- no separate fetch), plus
 * "create a new agent" and "attach an existing agent account" forms.
 *
 * **One-time key handling (constitution §7.5, task-5 brief's mandatory
 * test detail -- security-critical, read before touching this file):**
 * `createAgent`/`attachAgent` both return a freshly-minted `api_key` the
 * server never shows again. It lives ONLY in this component's `revealed`
 * state, rendered in a single mono block. Dismissing that block
 * (`dismissReveal`) sets `revealed` back to `null`, which unmounts the
 * block entirely -- React removes those DOM nodes, not just hides them --
 * so the key string is provably gone from the DOM afterward. Nothing in
 * this file ever passes the key (or the `MemberRegisterOut` it came from)
 * to `console.*` -- error paths log nothing, they only set `error` state
 * from `errorMessage()`, which never includes request/response bodies.
 * Do not add a `console.log`/`console.debug` anywhere in this file,
 * including "temporary" debugging -- `__tests__/settings.test.tsx` spies
 * on every console method for exactly this reason.
 *
 * **Read-only mode (SMAC-92 Task 4):** a caller with only `Cap.
 * VIEW_AGENTS` (the baseline `member` role) reaches this same panel --
 * the agent list stays visible (`Cap.VIEW_AGENTS`, everyone), but every
 * mutation control (create/attach forms, and by extension the one-time
 * key reveal they lead to) is entirely absent when `readOnly` is true,
 * not merely disabled -- same "omit, don't disable" posture `Settings.
 * tsx` already uses for whole tabs (constitution §7.5). The server's own
 * `Cap.MANAGE_AGENTS` wall on `POST /members/agents` is still the real
 * gate; this is belt-and-suspenders UI hygiene.
 */

export type AgentsPanelProps = {
  members: MemberOut[];
  /** Re-fetch the member directory (`state/workspace.tsx`'s
   * `refreshMembers`) -- called after a successful create/attach so the
   * new agent shows up in the list immediately. */
  onRefresh: () => Promise<void>;
  /** `true` for a caller who only holds `Cap.VIEW_AGENTS` (not `Cap.
   * MANAGE_AGENTS`) -- hides create/attach/key controls, list stays. */
  readOnly: boolean;
};

type Mode = "create" | "attach" | null;

export default function AgentsPanel({ members, onRefresh, readOnly }: AgentsPanelProps) {
  const agents = members.filter((m) => m.member_type === "agent");
  const [mode, setMode] = useState<Mode>(null);
  const [name, setName] = useState("");
  const [accountId, setAccountId] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<{ handle: string; apiKey: string } | null>(null);
  const [copied, setCopied] = useState(false);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const result = await api.createAgent(name.trim());
      setName("");
      setMode(null);
      await onRefresh();
      setRevealed({ handle: result.handle, apiKey: result.api_key });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPending(false);
    }
  }

  async function handleAttach(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const result = await api.attachAgent(accountId.trim());
      setAccountId("");
      setMode(null);
      await onRefresh();
      setRevealed({ handle: result.handle, apiKey: result.api_key });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPending(false);
    }
  }

  async function copyKey() {
    if (!revealed) return;
    try {
      await navigator.clipboard.writeText(revealed.apiKey);
      setCopied(true);
    } catch {
      // Best-effort -- the mono block still lets the reader select-and-
      // copy by hand; never log the failure (it would echo the key into
      // console/error-reporting alongside the reason).
    }
  }

  function dismissReveal() {
    setRevealed(null);
    setCopied(false);
  }

  if (revealed) {
    return (
      <div className="agents-panel__reveal" role="alertdialog" aria-label="New agent key">
        <h2>Agent key created</h2>
        <p>
          For <span className="mono">@{revealed.handle}</span>
        </p>
        <p className="agents-panel__reveal-warning">
          This key is shown exactly once. Copy it now — SMAC never stores or logs it anywhere,
          and it cannot be shown again.
        </p>
        <pre className="agents-panel__key" data-testid="agent-key">
          <code>{revealed.apiKey}</code>
        </pre>
        <div className="agents-panel__reveal-actions">
          <button type="button" className="btn btn--quiet" onClick={() => void copyKey()}>
            {copied ? "Copied!" : "Copy key"}
          </button>
          <button type="button" className="btn btn--primary" onClick={dismissReveal}>
            Done — I&apos;ve saved it
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="agents-panel">
      <h2>Agents</h2>
      {agents.length === 0 && (
        <p className="agents-panel__empty">
          No agents yet. Agents are teammates with their own handle and API key — create one to
          give a bot a seat in this workspace.
        </p>
      )}
      {agents.length > 0 && (
        <ul className="agents-panel__list">
          {agents.map((agent) => (
            <li key={agent.member_id} className="agents-panel__row">
              <span className="agents-panel__name">{agent.member_name}</span>
              <span className="agents-panel__handle mono">@{agent.handle}</span>
              <span className="agents-panel__account-id mono">{agent.account_id}</span>
            </li>
          ))}
        </ul>
      )}

      {!readOnly && (
        <div className="agents-panel__actions">
          <button
            type="button"
            className="btn btn--quiet"
            onClick={() => setMode(mode === "create" ? null : "create")}
          >
            + Create agent
          </button>
          <button
            type="button"
            className="btn btn--quiet"
            onClick={() => setMode(mode === "attach" ? null : "attach")}
          >
            Attach existing
          </button>
        </div>
      )}

      {!readOnly && mode === "create" && (
        <form onSubmit={handleCreate} className="agents-panel__form">
          <label htmlFor="agent-create-name">Agent name</label>
          <input
            id="agent-create-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            autoFocus
          />
          {error && (
            <p role="alert" className="agents-panel__error">
              {error}
            </p>
          )}
          <button type="submit" className="btn btn--primary" disabled={pending}>
            {pending ? "Creating…" : "Create"}
          </button>
        </form>
      )}

      {!readOnly && mode === "attach" && (
        <form onSubmit={handleAttach} className="agents-panel__form">
          <label htmlFor="agent-attach-account-id">Account ID</label>
          <input
            id="agent-attach-account-id"
            className="mono"
            value={accountId}
            onChange={(event) => setAccountId(event.target.value)}
            required
            autoFocus
          />
          {error && (
            <p role="alert" className="agents-panel__error">
              {error}
            </p>
          )}
          <button type="submit" className="btn btn--primary" disabled={pending}>
            {pending ? "Attaching…" : "Attach"}
          </button>
        </form>
      )}
    </div>
  );
}
