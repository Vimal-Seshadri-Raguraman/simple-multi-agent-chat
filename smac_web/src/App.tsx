import { useState } from "react";
import "./styles/tokens.css";
import "./App.css";

type Theme = "light" | "dark";

/**
 * Placeholder shell for Task 1: proves the constitution's tokens actually
 * drive the visible surface (the theme toggle flips `data-theme`, and every
 * color on screen comes from a `var(--color-*)` custom property generated
 * from design/tokens.json). Rail/Room/Drawer land in Task 3.
 */
export default function App() {
  const [theme, setTheme] = useState<Theme>("dark");

  return (
    <div className="app-shell" data-theme={theme}>
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
        <p>Web scaffold is up. The daily driver ships in the tasks that follow.</p>
      </main>
    </div>
  );
}
