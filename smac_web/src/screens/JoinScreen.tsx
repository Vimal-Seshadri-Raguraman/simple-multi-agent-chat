import { type FormEvent, useEffect, useRef, useState } from "react";
import * as api from "../lib/api";
import BackToWorkspace from "../components/BackToWorkspace";
import type { WorkspaceSearchOut } from "../lib/api";
import { errorMessage } from "../lib/errors";
import { useAuth } from "../state/auth";

const SEARCH_DEBOUNCE_MS = 300;

/**
 * The join path (web spec §2): live-searchable public workspace
 * directory, plus a shareable invite-code entry. Both actions need the
 * caller's per-workspace display name first, so first/last name fields
 * live at the top rather than being duplicated per-action.
 *
 * Also reachable with a LIVE workspace session still underneath (the
 * palette's `/join`) -- `<BackToWorkspace />` renders the escape hatch
 * back to it in that case, and nothing at all otherwise (fix round 1,
 * see that component's docstring).
 */
export default function JoinScreen() {
  const { navigate, workspaceEntered, setPending, setError, pending, error } = useAuth();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<WorkspaceSearchOut[]>([]);
  const [code, setCode] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (debounceRef.current !== null) {
      clearTimeout(debounceRef.current);
    }
    debounceRef.current = setTimeout(() => {
      api
        .searchPublic(query)
        .then(setResults)
        .catch(() => setResults([]));
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current !== null) {
        clearTimeout(debounceRef.current);
      }
    };
  }, [query]);

  const canJoin = firstName.trim().length > 0 && lastName.trim().length > 0;

  async function handleJoinPublic(workspaceId: string) {
    setPending();
    try {
      const { session } = await api.joinPublic(workspaceId, firstName, lastName);
      workspaceEntered(session);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function handleJoinCode(event: FormEvent) {
    event.preventDefault();
    setPending();
    try {
      const { session } = await api.joinCode(code, firstName, lastName);
      workspaceEntered(session);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="auth-screen auth-screen--join">
      <BackToWorkspace />
      <h1>Join a workspace</h1>
      <div className="join-screen__display-name">
        <label htmlFor="join-first-name">Your first name</label>
        <input
          id="join-first-name"
          value={firstName}
          onChange={(event) => setFirstName(event.target.value)}
          required
        />
        <label htmlFor="join-last-name">Your last name</label>
        <input
          id="join-last-name"
          value={lastName}
          onChange={(event) => setLastName(event.target.value)}
          required
        />
      </div>

      <section className="join-screen__directory">
        <h2>Public workspaces</h2>
        <input
          type="search"
          aria-label="Search public workspaces"
          placeholder="Search public workspaces…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <ul>
          {results.map((workspace) => (
            <li key={workspace.workspace_id}>
              {workspace.workspace_name}
              <button
                type="button"
                className="btn btn--primary btn--sm"
                disabled={pending || !canJoin}
                onClick={() => void handleJoinPublic(workspace.workspace_id)}
              >
                Join
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="join-screen__code">
        <h2>Have an invite code?</h2>
        <form onSubmit={handleJoinCode}>
          <label htmlFor="join-code">Invite code</label>
          <input
            id="join-code"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            required
          />
          <button type="submit" className="btn btn--primary btn--block" disabled={pending || !canJoin}>
            {pending ? "Joining…" : "Join"}
          </button>
        </form>
      </section>

      {error && (
        <p role="alert" className="auth-screen__error">
          {error}
        </p>
      )}

      <button type="button" className="btn btn--quiet btn--block" onClick={() => navigate("create-or-join")}>
        Back
      </button>
    </div>
  );
}
