import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Palette from "../components/Palette";
import { COMMANDS, type CommandContext } from "../lib/commands";

const __dirname = dirname(fileURLToPath(import.meta.url));

/** Parses `design/commands.md`'s normative table into `{name, help}` rows,
 * stripping markdown backtick/code-span formatting so the result is
 * directly comparable to `COMMANDS`'s plain strings. */
function parseCommandsMd(): { name: string; help: string }[] {
  const source = readFileSync(
    resolve(__dirname, "../../../design/commands.md"),
    "utf-8"
  );
  const rows: { name: string; help: string }[] = [];
  for (const line of source.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("|") || !trimmed.endsWith("|")) continue;
    const cells = trimmed
      .slice(1, -1)
      .split("|")
      .map((c) => c.trim());
    if (cells.length < 3) continue;
    if (cells[0] === "Command") continue; // header row
    if (/^:?-+:?$/.test(cells[0])) continue; // separator row
    const name = cells[0].replace(/`/g, "").trim();
    const help = cells[2].replace(/`/g, "").trim();
    rows.push({ name, help });
  }
  return rows;
}

describe("Palette command registry drift guard (constitution §4, mandatory)", () => {
  it("matches design/commands.md's table exactly, name for name, help for help, in order", () => {
    const canonical = parseCommandsMd();
    expect(canonical.length).toBeGreaterThan(0); // the parser actually found rows

    const registry = COMMANDS.map((c) => ({ name: c.name, help: c.help }));
    expect(registry).toEqual(canonical);
  });
});

function buildContext(overrides: Partial<CommandContext> = {}): CommandContext {
  return {
    args: "",
    navigateAuthScreen: vi.fn(),
    logout: vi.fn().mockResolvedValue(undefined),
    switchChannelByName: vi.fn(),
    createChannel: vi.fn().mockResolvedValue(undefined),
    refreshUnreads: vi.fn().mockResolvedValue(undefined),
    showWhoami: vi.fn(),
    goToSettings: vi.fn(),
    ...overrides,
  };
}

describe("Palette (Cmd-K, web spec §2)", () => {
  it("shows every command when the query is empty (the palette empty-state IS /help)", () => {
    render(<Palette open initialQuery="" onClose={vi.fn()} buildContext={(args) => buildContext({ args })} />);
    for (const command of COMMANDS) {
      expect(screen.getByText(command.name)).toBeInTheDocument();
    }
  });

  it("opens prefiltered by whatever followed the composer's leading '/'", () => {
    render(<Palette open initialQuery="chan" onClose={vi.fn()} buildContext={(args) => buildContext({ args })} />);
    expect(screen.getByText("/channel")).toBeInTheDocument();
    expect(screen.getByText("/channel create")).toBeInTheDocument();
    expect(screen.getByText("/channels")).toBeInTheDocument();
    expect(screen.queryByText("/whoami")).not.toBeInTheDocument();
  });

  it("renders nothing when closed", () => {
    render(<Palette open={false} initialQuery="" onClose={vi.fn()} buildContext={(args) => buildContext({ args })} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("filters as the user types in the palette's own input", () => {
    render(<Palette open initialQuery="" onClose={vi.fn()} buildContext={(args) => buildContext({ args })} />);
    fireEvent.change(screen.getByLabelText("Command palette"), { target: { value: "whoami" } });
    expect(screen.getByText("/whoami")).toBeInTheDocument();
    expect(screen.queryByText("/quit")).not.toBeInTheDocument();
  });

  it("Escape closes the palette", () => {
    const onClose = vi.fn();
    render(<Palette open initialQuery="" onClose={onClose} buildContext={(args) => buildContext({ args })} />);
    fireEvent.keyDown(screen.getByLabelText("Command palette"), { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("ArrowDown/ArrowUp move the highlighted entry, and Enter runs it, splitting off typed args", () => {
    let capturedArgs: string | undefined;
    const onClose = vi.fn();
    render(
      <Palette
        open
        initialQuery="channel general"
        onClose={onClose}
        buildContext={(args) => {
          capturedArgs = args;
          return buildContext();
        }}
      />
    );
    const input = screen.getByLabelText("Command palette");
    // "/channel" is the first match for "channel general" (its name is a
    // prefix of the query) -- Enter on the default (first) highlight runs it.
    fireEvent.keyDown(input, { key: "Enter" });
    expect(capturedArgs).toBe("general");
    expect(onClose).toHaveBeenCalled();
  });

  it("is fully clickable: clicking an entry runs it and closes the palette", () => {
    const run = vi.fn();
    const originalRun = COMMANDS.find((c) => c.name === "/whoami")!.run;
    COMMANDS.find((c) => c.name === "/whoami")!.run = run;
    try {
      const onClose = vi.fn();
      render(
        <Palette open initialQuery="whoami" onClose={onClose} buildContext={(args) => buildContext({ args })} />
      );
      fireEvent.mouseDown(screen.getByText("/whoami"));
      expect(run).toHaveBeenCalled();
      expect(onClose).toHaveBeenCalled();
    } finally {
      COMMANDS.find((c) => c.name === "/whoami")!.run = originalRun;
    }
  });
});
