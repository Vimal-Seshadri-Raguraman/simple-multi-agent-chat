import { useState } from "react";
import "./styles/tokens.css";
import "./App.css";
import AuthedShell from "./components/AuthedShell";
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
 * Hand-rolled screen switch over the auth store's `screen` state (no
 * react-router -- see `state/auth.tsx`'s module docstring), for every
 * screen EXCEPT the terminal `"authed"` one -- `AppBody` below intercepts
 * `"authed"` before this ever renders, since the daily-driver shell (Task
 * 3's `AuthedShell`, full-bleed, no wordmark header) needs a completely
 * different wrapper than every pre-workspace screen here.
 */
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
      return null; // unreachable -- AppBody renders AuthedShell for this case
    default:
      return null;
  }
}

/** Chooses between the wordmark-header layout (every pre-authed screen)
 * and the authed shell's full-bleed layout (web spec §5's layout grammar
 * -- the Rail/Room/Drawer shell owns the whole viewport, with its own
 * theme toggle in the Rail's YOU menu; both read/write the SAME lifted
 * `theme` state from `App` below, so switching it anywhere stays
 * consistent). Needs `useAuth()`, so it has to live below
 * `<AuthProvider>`, unlike `App` itself. */
function AppBody({ theme, onToggleTheme }: { theme: Theme; onToggleTheme: () => void }) {
  const { screen } = useAuth();
  if (screen === "authed") {
    return <AuthedShell theme={theme} onToggleTheme={onToggleTheme} />;
  }
  return (
    <>
      <header className="app-shell__header">
        <span className="app-shell__wordmark">SMAC</span>
        <button type="button" className="app-shell__theme-toggle" onClick={onToggleTheme}>
          {theme === "dark" ? "Light mode" : "Dark mode"}
        </button>
      </header>
      <main className="app-shell__body">
        <AuthScreens />
      </main>
    </>
  );
}

/**
 * App root (Task 1 scaffold, wired to the real auth flow (Task 2) and the
 * real authed shell (Task 3, `AuthedShell`) -- the constitution's tokens
 * drive both).
 */
export default function App() {
  const [theme, setTheme] = useState<Theme>("dark");
  const toggleTheme = () => setTheme((current) => (current === "dark" ? "light" : "dark"));

  return (
    <AuthProvider>
      <div className="app-shell" data-theme={theme}>
        <VersionBanner />
        <AppBody theme={theme} onToggleTheme={toggleTheme} />
      </div>
    </AuthProvider>
  );
}
