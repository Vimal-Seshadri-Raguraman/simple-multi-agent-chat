import { useAuth } from "../state/auth";

/**
 * The logged-out landing screen (web spec §2): SMAC wordmark + a choice
 * between logging in and creating a new account. Pure navigation --
 * no api.ts calls live here.
 */
export default function Welcome() {
  const { navigate } = useAuth();

  return (
    <div className="auth-screen auth-screen--welcome">
      <p className="auth-screen__tagline">Simple Multi-Agent Chat</p>
      <div className="auth-screen__actions">
        <button type="button" className="btn btn--primary btn--block" onClick={() => navigate("login")}>
          Log in
        </button>
        <button type="button" className="btn btn--quiet btn--block" onClick={() => navigate("register")}>
          Create an account
        </button>
      </div>
    </div>
  );
}
