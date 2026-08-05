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
        <button type="button" onClick={() => navigate("login")}>
          Log in
        </button>
        <button type="button" onClick={() => navigate("register")}>
          Create an account
        </button>
      </div>
    </div>
  );
}
