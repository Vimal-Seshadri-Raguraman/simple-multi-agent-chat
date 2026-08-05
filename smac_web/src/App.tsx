import { useState } from "react";
import "./styles/tokens.css";
import "./App.css";
import VersionBanner from "./components/VersionBanner";
import CreateOrJoin from "./screens/CreateOrJoin";
import JoinScreen from "./screens/JoinScreen";
import Login from "./screens/Login";
import Register from "./screens/Register";
import Welcome from "./screens/Welcome";
import WorkspacePicker from "./screens/WorkspacePicker";
import { AuthProvider, useAuth } from "./state/auth";

type Theme = "light" | "dark";

/**
 * The authed placeholder: Task 3 owns the real Rail/Room/Drawer daily-
 * driver shell (web spec §2). This just proves the auth state machine
 * actually reaches its terminal "authed" screen and offers a way back
 * out (logout) for manual testing until the real shell lands.
 */
function AuthedPlaceholder() {
  const { logout, session } = useAuth();
  return (
    <div className="app-shell__authed-placeholder">
      <p>Signed in as {session?.email}. The daily-driver shell lands in Task 3.</p>
      <button type="button" onClick={() => void logout()}>
        Log out
      </button>
    </div>
  );
}

/** Hand-rolled screen switch over the auth store's `screen` state (no
 * react-router -- see `state/auth.tsx`'s module docstring). */
function AuthScreens() {
  const { screen } = useAuth();
  switch (screen) {
    case "welcome":
      return <Welcome />;
    case "login":
      return <Login />;
    case "register":
      return <Register />;
    case "workspace-picker":
      return <WorkspacePicker />;
    case "create-or-join":
      return <CreateOrJoin />;
    case "join":
      return <JoinScreen />;
    case "authed":
      return <AuthedPlaceholder />;
    default:
      return null;
  }
}

/**
 * App shell (Task 1 scaffold, now wired to the real auth flow). Rail/
 * Room/Drawer daily-driver layout lands in Task 3; this proves the
 * constitution's tokens drive the visible surface and hosts the auth
 * screens + version banner in the meantime.
 */
export default function App() {
  const [theme, setTheme] = useState<Theme>("dark");

  return (
    <AuthProvider>
      <div className="app-shell" data-theme={theme}>
        <VersionBanner />
        <header className="app-shell__header">
          <span className="app-shell__wordmark">SMAC</span>
          <button
            type="button"
            className="app-shell__theme-toggle"
            onClick={() =>
              setTheme((current) => (current === "dark" ? "light" : "dark"))
            }
          >
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </button>
        </header>
        <main className="app-shell__body">
          <AuthScreens />
        </main>
      </div>
    </AuthProvider>
  );
}
