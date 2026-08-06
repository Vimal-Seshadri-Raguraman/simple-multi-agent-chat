import { type FormEvent, useState } from "react";
import * as api from "../lib/api";
import BackToWorkspace from "../components/BackToWorkspace";
import { errorMessage } from "../lib/errors";
import { useAuth } from "../state/auth";

type Visibility = "public" | "private";

/**
 * The "no workspace yet" landing state (web spec §2): reached after
 * register step 1 (`Register.tsx`) or a login with 0 memberships. Offers
 * "create your own" (inline form, this file) or "join a workspace"
 * (hands off to `JoinScreen.tsx`, its own top-level screen).
 *
 * Also reachable with a LIVE workspace session still underneath (the Rail
 * switcher's "Create or join a workspace…" entry, `/workspace create` from
 * the palette) -- `<BackToWorkspace />` renders the escape hatch back to
 * it in that case, and nothing at all otherwise (fix round 1, see that
 * component's docstring).
 */
export default function CreateOrJoin() {
  const { navigate, workspaceEntered, setPending, setError, pending, error, logout } = useAuth();
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [name, setName] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("private");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    setPending();
    try {
      const { session } = await api.createWorkspace(name, visibility, firstName, lastName);
      workspaceEntered(session);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  if (showCreateForm) {
    return (
      <div className="auth-screen auth-screen--create-workspace">
        <BackToWorkspace />
        <h1>Create a workspace</h1>
        <form onSubmit={handleCreate}>
          <label htmlFor="create-workspace-name">Workspace name</label>
          <input
            id="create-workspace-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
          <label htmlFor="create-workspace-visibility">Visibility</label>
          <select
            id="create-workspace-visibility"
            value={visibility}
            onChange={(event) => setVisibility(event.target.value as Visibility)}
          >
            <option value="private">Private</option>
            <option value="public">Public</option>
          </select>
          <label htmlFor="create-workspace-first-name">Your first name</label>
          <input
            id="create-workspace-first-name"
            value={firstName}
            onChange={(event) => setFirstName(event.target.value)}
            required
          />
          <label htmlFor="create-workspace-last-name">Your last name</label>
          <input
            id="create-workspace-last-name"
            value={lastName}
            onChange={(event) => setLastName(event.target.value)}
            required
          />
          {error && (
            <p role="alert" className="auth-screen__error">
              {error}
            </p>
          )}
          <button type="submit" className="btn btn--primary btn--block" disabled={pending}>
            {pending ? "Creating…" : "Create workspace"}
          </button>
        </form>
        <button type="button" className="btn btn--quiet btn--block" onClick={() => setShowCreateForm(false)}>
          Back
        </button>
      </div>
    );
  }

  return (
    <div className="auth-screen auth-screen--create-or-join">
      <BackToWorkspace />
      <h1>Create or join a workspace</h1>
      <div className="auth-screen__actions">
        <button type="button" className="btn btn--primary btn--block" onClick={() => setShowCreateForm(true)}>
          Create your own
        </button>
        <button type="button" className="btn btn--quiet btn--block" onClick={() => navigate("join")}>
          Join a workspace
        </button>
      </div>
      <button type="button" className="btn btn--quiet btn--block" onClick={() => void logout()}>
        Log out
      </button>
    </div>
  );
}
