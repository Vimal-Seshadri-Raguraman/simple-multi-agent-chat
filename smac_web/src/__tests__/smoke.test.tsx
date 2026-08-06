import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import App from "../App";

describe("App shell smoke test (Task 1 scaffold)", () => {
  it("renders the SMAC wordmark", () => {
    render(<App />);
    expect(screen.getByText("SMAC")).toBeInTheDocument();
  });

  it("toggles the theme, proving tokens.css drives the visible surface", () => {
    render(<App />);
    const toggle = screen.getByRole("button", { name: /dark mode|light mode/i });
    const shell = toggle.closest(".app-shell") as HTMLElement;

    expect(shell).toHaveAttribute("data-theme", "dark");
    fireEvent.click(toggle);
    expect(shell).toHaveAttribute("data-theme", "light");
  });
});
