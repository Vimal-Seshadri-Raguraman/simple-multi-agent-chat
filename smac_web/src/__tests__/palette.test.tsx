import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Palette from "../components/Palette";
import { COMMANDS, type Command, type CommandContext } from "../lib/commands";

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
    hasCap: () => true,
    ...overrides,
  };
}

/** Everyone can run everything -- the default for tests not exercising
 * the gating behavior itself. */
const ALLOW_ALL = () => true;
/** Nobody can run anything gated -- a plain `member`. */
const DENY_ALL = () => false;

describe("Palette (Cmd-K, web spec §2)", () => {
  it("shows every command when the query is empty (the palette empty-state IS /help)", () => {
    render(
      <Palette
        open
        initialQuery=""
        onClose={vi.fn()}
        buildContext={(args) => buildContext({ args })}
        hasCap={ALLOW_ALL}
      />
    );
    for (const command of COMMANDS) {
      expect(screen.getByText(command.name)).toBeInTheDocument();
    }
  });

  it("opens prefiltered by whatever followed the composer's leading '/'", () => {
    render(
      <Palette
        open
        initialQuery="chan"
        onClose={vi.fn()}
        buildContext={(args) => buildContext({ args })}
        hasCap={ALLOW_ALL}
      />
    );
    expect(screen.getByText("/channel")).toBeInTheDocument();
    expect(screen.getByText("/channel create")).toBeInTheDocument();
    expect(screen.getByText("/channels")).toBeInTheDocument();
    expect(screen.queryByText("/whoami")).not.toBeInTheDocument();
  });

  it("renders nothing when closed", () => {
    render(
      <Palette
        open={false}
        initialQuery=""
        onClose={vi.fn()}
        buildContext={(args) => buildContext({ args })}
        hasCap={ALLOW_ALL}
      />
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("filters as the user types in the palette's own input", () => {
    render(
      <Palette
        open
        initialQuery=""
        onClose={vi.fn()}
        buildContext={(args) => buildContext({ args })}
        hasCap={ALLOW_ALL}
      />
    );
    fireEvent.change(screen.getByLabelText("Command palette"), { target: { value: "whoami" } });
    expect(screen.getByText("/whoami")).toBeInTheDocument();
    expect(screen.queryByText("/quit")).not.toBeInTheDocument();
  });

  it("Escape closes the palette", () => {
    const onClose = vi.fn();
    render(
      <Palette
        open
        initialQuery=""
        onClose={onClose}
        buildContext={(args) => buildContext({ args })}
        hasCap={ALLOW_ALL}
      />
    );
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
        hasCap={ALLOW_ALL}
      />
    );
    const input = screen.getByLabelText("Command palette");
    // "/channel" is the first match for "channel general" (its name is a
    // prefix of the query) -- Enter on the default (first) highlight runs it.
    fireEvent.keyDown(input, { key: "Enter" });
    expect(capturedArgs).toBe("general");
    expect(onClose).toHaveBeenCalled();
  });

  // Finding 1 (final review, IMPORTANT): exact-typed "/channels" and
  // "/channel create <name>" used to run "/channel" instead, because
  // registry order (not the longest/exact name match) decided which of the
  // multiple `matches()` hits Enter selected. These three regression tests
  // swap in spies for the affected commands' `run` so each can assert
  // exactly one of them fired.
  function withSpies(names: string[], fn: (spies: Record<string, ReturnType<typeof vi.fn>>) => void) {
    const originals: Record<string, Command["run"]> = {};
    const spies: Record<string, ReturnType<typeof vi.fn>> = {};
    for (const name of names) {
      const command = COMMANDS.find((c) => c.name === name)!;
      originals[name] = command.run;
      spies[name] = vi.fn();
      command.run = spies[name];
    }
    try {
      fn(spies);
    } finally {
      for (const name of names) {
        COMMANDS.find((c) => c.name === name)!.run = originals[name];
      }
    }
  }

  it("Enter on exact-typed '/channels' runs /channels, not /channel (Finding 1 regression)", () => {
    withSpies(["/channel", "/channels", "/channel create"], (spies) => {
      render(
        <Palette
          open
          initialQuery="channels"
          onClose={vi.fn()}
          buildContext={(args) => buildContext({ args })}
          hasCap={ALLOW_ALL}
        />
      );
      fireEvent.keyDown(screen.getByLabelText("Command palette"), { key: "Enter" });
      expect(spies["/channels"]).toHaveBeenCalled();
      expect(spies["/channel"]).not.toHaveBeenCalled();
      expect(spies["/channel create"]).not.toHaveBeenCalled();
    });
  });

  it("Enter on '/channel create foo' runs /channel create with args 'foo', not /channel (Finding 1 regression)", () => {
    withSpies(["/channel", "/channels", "/channel create"], (spies) => {
      let capturedArgs: string | undefined;
      render(
        <Palette
          open
          initialQuery="channel create foo"
          onClose={vi.fn()}
          buildContext={(args) => {
            capturedArgs = args;
            return buildContext({ args });
          }}
          hasCap={ALLOW_ALL}
        />
      );
      fireEvent.keyDown(screen.getByLabelText("Command palette"), { key: "Enter" });
      expect(spies["/channel create"]).toHaveBeenCalled();
      expect(spies["/channel"]).not.toHaveBeenCalled();
      expect(capturedArgs).toBe("foo");
    });
  });

  it("Enter on plain '/channel' (bare name, no args) runs /channel, not /channel create (Finding 1 regression)", () => {
    withSpies(["/channel", "/channels", "/channel create"], (spies) => {
      render(
        <Palette
          open
          initialQuery="channel"
          onClose={vi.fn()}
          buildContext={(args) => buildContext({ args })}
          hasCap={ALLOW_ALL}
        />
      );
      fireEvent.keyDown(screen.getByLabelText("Command palette"), { key: "Enter" });
      expect(spies["/channel"]).toHaveBeenCalled();
      expect(spies["/channel create"]).not.toHaveBeenCalled();
      expect(spies["/channels"]).not.toHaveBeenCalled();
    });
  });

  it("is fully clickable: clicking an entry runs it and closes the palette", () => {
    const run = vi.fn();
    const originalRun = COMMANDS.find((c) => c.name === "/whoami")!.run;
    COMMANDS.find((c) => c.name === "/whoami")!.run = run;
    try {
      const onClose = vi.fn();
      render(
        <Palette
          open
          initialQuery="whoami"
          onClose={onClose}
          buildContext={(args) => buildContext({ args })}
          hasCap={ALLOW_ALL}
        />
      );
      fireEvent.mouseDown(screen.getByText("/whoami"));
      expect(run).toHaveBeenCalled();
      expect(onClose).toHaveBeenCalled();
    } finally {
      COMMANDS.find((c) => c.name === "/whoami")!.run = originalRun;
    }
  });
});

// SMAC-92 Task 4: `/invite` (requires `mint_human_invites`) and
// `/workspace delete` (requires `manage_workspace`) are gated via `lib/
// commands.ts`'s `REQUIRED_CAP` map, kept BESIDE `COMMANDS` rather than on
// each entry -- the drift-guard test at the top of this file (which diffs
// `COMMANDS`' `name`/`help` against `design/commands.md`) stays green
// because `REQUIRED_CAP` never touches those fields.
describe("Palette capability gating (task-4 brief, SMAC-92)", () => {
  it("as a member (no mint_human_invites), '/invite' renders dimmed with the Workspace Admin hint, and does not run on Enter", () => {
    const run = vi.fn();
    const originalRun = COMMANDS.find((c) => c.name === "/invite")!.run;
    COMMANDS.find((c) => c.name === "/invite")!.run = run;
    try {
      render(
        <Palette
          open
          initialQuery="invite"
          onClose={vi.fn()}
          buildContext={(args) => buildContext({ args })}
          hasCap={DENY_ALL}
        />
      );
      const entry = screen.getByRole("option", { name: /\/invite/ });
      expect(entry).toHaveAttribute("aria-disabled", "true");
      expect(entry).toHaveTextContent(/requires Workspace Admin/i);

      fireEvent.keyDown(screen.getByLabelText("Command palette"), { key: "Enter" });
      expect(run).not.toHaveBeenCalled();
    } finally {
      COMMANDS.find((c) => c.name === "/invite")!.run = originalRun;
    }
  });

  it("as a member, clicking a gated '/invite' entry also does not run it", () => {
    const run = vi.fn();
    const originalRun = COMMANDS.find((c) => c.name === "/invite")!.run;
    COMMANDS.find((c) => c.name === "/invite")!.run = run;
    try {
      render(
        <Palette
          open
          initialQuery="invite"
          onClose={vi.fn()}
          buildContext={(args) => buildContext({ args })}
          hasCap={DENY_ALL}
        />
      );
      fireEvent.mouseDown(screen.getByRole("option", { name: /\/invite/ }));
      expect(run).not.toHaveBeenCalled();
    } finally {
      COMMANDS.find((c) => c.name === "/invite")!.run = originalRun;
    }
  });

  it("as an admin (holds mint_human_invites), '/invite' is NOT dimmed and Enter runs it", () => {
    const run = vi.fn();
    const originalRun = COMMANDS.find((c) => c.name === "/invite")!.run;
    COMMANDS.find((c) => c.name === "/invite")!.run = run;
    try {
      render(
        <Palette
          open
          initialQuery="invite"
          onClose={vi.fn()}
          buildContext={(args) => buildContext({ args })}
          hasCap={ALLOW_ALL}
        />
      );
      const entry = screen.getByRole("option", { name: /\/invite/ });
      expect(entry).not.toHaveAttribute("aria-disabled");
      expect(entry).not.toHaveTextContent(/requires/i);

      fireEvent.keyDown(screen.getByLabelText("Command palette"), { key: "Enter" });
      expect(run).toHaveBeenCalled();
    } finally {
      COMMANDS.find((c) => c.name === "/invite")!.run = originalRun;
    }
  });

  it("'/workspace delete' is gated on manage_workspace with the Workspace Admin hint", () => {
    render(
      <Palette
        open
        initialQuery="workspace delete"
        onClose={vi.fn()}
        buildContext={(args) => buildContext({ args })}
        hasCap={DENY_ALL}
      />
    );
    const entry = screen.getByRole("option", { name: /\/workspace delete/ });
    expect(entry).toHaveAttribute("aria-disabled", "true");
    expect(entry).toHaveTextContent(/requires Workspace Admin/i);
  });

  it("ungated commands (e.g. '/whoami') are never dimmed regardless of hasCap", () => {
    render(
      <Palette
        open
        initialQuery="whoami"
        onClose={vi.fn()}
        buildContext={(args) => buildContext({ args })}
        hasCap={DENY_ALL}
      />
    );
    const entry = screen.getByRole("option", { name: /\/whoami/ });
    expect(entry).not.toHaveAttribute("aria-disabled");
  });
});
