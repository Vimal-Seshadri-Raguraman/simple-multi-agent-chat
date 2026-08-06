import { type FormEvent, useState } from "react";
import * as api from "../lib/api";
import { errorMessage } from "../lib/errors";
import { useAuth } from "../state/auth";

const MIN_PASSWORD_LENGTH = 8;

/**
 * Register, step 1 of 2 (web spec §2: "account-first two-step, mirrors
 * TUI frames"): ONLY account fields (email/password) live here. There is
 * no workspace field on this screen at all -- `accountReady()` is the
 * only way forward, and it always lands on "create-or-join" (step 2,
 * `CreateOrJoin.tsx`), never the reverse order.
 */
export default function Register() {
  const { navigate, accountReady, setPending, setError, pending, error } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }
    setPending();
    try {
      const session = await api.signup(email, password);
      accountReady(session);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="auth-screen auth-screen--register">
      <h1>Create your account</h1>
      <form onSubmit={handleSubmit}>
        <label htmlFor="register-email">Email</label>
        <input
          id="register-email"
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
        />
        <label htmlFor="register-password">Password</label>
        <input
          id="register-password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
        <label htmlFor="register-confirm-password">Confirm password</label>
        <input
          id="register-confirm-password"
          type="password"
          value={confirmPassword}
          onChange={(event) => setConfirmPassword(event.target.value)}
          required
        />
        {error && (
          <p role="alert" className="auth-screen__error">
            {error}
          </p>
        )}
        <button type="submit" className="btn btn--primary btn--block" disabled={pending}>
          {pending ? "Creating account…" : "Continue"}
        </button>
      </form>
      <button type="button" className="btn btn--quiet btn--block" onClick={() => navigate("welcome")}>
        Back
      </button>
    </div>
  );
}
