import { type FormEvent, useState } from "react";
import * as api from "../lib/api";
import { errorMessage } from "../lib/errors";
import { useAuth } from "../state/auth";

/**
 * Global email+password login (web spec §2). Three branches on the
 * returned membership list, all driven from here (the store only records
 * state; it doesn't own the follow-up `enterWorkspace` call):
 *  - 0 memberships -> `loginSuccess` routes straight to "create-or-join".
 *  - 1 membership -> auto `enterWorkspace` into it, then "authed".
 *  - >1 memberships -> `loginSuccess` routes to "workspace-picker".
 */
export default function Login() {
  const { navigate, loginSuccess, workspaceEntered, setPending, setError, pending, error } =
    useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setPending();
    try {
      const { session, workspaces } = await api.login(email, password);
      loginSuccess(session, workspaces);
      if (workspaces.length === 1) {
        await api.enterWorkspace(workspaces[0].workspace_id);
        const entered = api.getSession();
        if (entered) {
          workspaceEntered(entered);
        }
      }
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="auth-screen auth-screen--login">
      <h1>Log in</h1>
      <form onSubmit={handleSubmit}>
        <label htmlFor="login-email">Email</label>
        <input
          id="login-email"
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
        />
        <label htmlFor="login-password">Password</label>
        <input
          id="login-password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
        {error && (
          <p role="alert" className="auth-screen__error">
            {error}
          </p>
        )}
        <button type="submit" className="btn btn--primary btn--block" disabled={pending}>
          {pending ? "Logging in…" : "Log in"}
        </button>
      </form>
      <button type="button" className="btn btn--quiet btn--block" onClick={() => navigate("welcome")}>
        Back
      </button>
    </div>
  );
}
