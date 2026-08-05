import { type KeyboardEvent, useEffect, useMemo, useState } from "react";
import { COMMANDS, type Command, type CommandContext } from "../lib/commands";

/**
 * The Cmd-K command palette (web spec §2 / constitution §4): THE
 * registry (`lib/commands.ts`) rendered as a fuzzy-filterable, keyboard-
 * first-but-fully-clickable list. Opened two ways, both landing here with
 * the SAME behavior (task-3 brief): the shell's global Cmd-K/Ctrl-K
 * listener (`initialQuery=""`), or the composer's leading `/` (`initialQuery`
 * = whatever followed the `/`, so the palette opens already prefiltered).
 *
 * An empty query shows every command -- the constitution's "palette
 * empty-state" doubling as `/help`'s command list.
 *
 * Matching a command's own full name in the query (e.g. typing "channel
 * general" against the "/channel" entry) splits the query into the
 * matched command's `args` -- what's left over is handed to that
 * command's `run(ctx)` as `ctx.args` (e.g. "/channel general" runs
 * `/channel` with `args: "general"`). Selecting a command WITHOUT typing
 * its full name first (a fuzzy match, or an arrow-key selection) simply
 * runs it with empty `args`.
 */

export type PaletteProps = {
  open: boolean;
  /** Prefilter text the palette opens with (composer's `/`-prefix hand-off,
   * or "" for a bare Cmd-K/Ctrl-K open). */
  initialQuery: string;
  onClose: () => void;
  /** Builds the `CommandContext` a selected command's `run` receives,
   * given the `args` split out of the typed query. */
  buildContext: (args: string) => CommandContext;
};

function matches(command: Command, query: string): boolean {
  if (query.length === 0) return true;
  const needle = query.toLowerCase();
  const name = command.name.slice(1).toLowerCase();
  return (
    // Browsing toward a name ("chan" while aiming for "/channel").
    name.includes(needle) ||
    // The full name was typed, with args trailing ("channel general").
    needle.startsWith(name) ||
    // A fuzzy search over what the command actually does.
    command.help.toLowerCase().includes(needle)
  );
}

function splitArgs(command: Command, query: string): string {
  const nameNoSlash = command.name.slice(1).toLowerCase();
  if (query.toLowerCase().startsWith(nameNoSlash)) {
    return query.slice(nameNoSlash.length).trim();
  }
  return "";
}

export default function Palette({ open, initialQuery, onClose, buildContext }: PaletteProps) {
  const [query, setQuery] = useState(initialQuery);
  const [activeIndex, setActiveIndex] = useState(0);

  // Re-seed the input every time the palette (re)opens -- a fresh Cmd-K
  // open always starts blank; a composer "/"-prefix hand-off starts with
  // whatever the user had already typed after the slash.
  useEffect(() => {
    if (open) {
      setQuery(initialQuery);
      setActiveIndex(0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialQuery]);

  const filtered = useMemo(() => COMMANDS.filter((c) => matches(c, query)), [query]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  if (!open) {
    return null;
  }

  function runCommand(command: Command) {
    const args = splitArgs(command, query);
    command.run(buildContext(args));
    onClose();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((i) => (filtered.length === 0 ? 0 : (i + 1) % filtered.length));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((i) => (filtered.length === 0 ? 0 : (i - 1 + filtered.length) % filtered.length));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const command = filtered[activeIndex];
      if (command) {
        runCommand(command);
      }
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  }

  return (
    <div className="palette__backdrop" onMouseDown={onClose}>
      <div
        className="palette"
        role="dialog"
        aria-label="Command palette dialog"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <input
          type="text"
          className="palette__input"
          aria-label="Command palette"
          autoFocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type a command…"
        />
        <ul className="palette__list" role="listbox">
          {filtered.length === 0 && <li className="palette__empty">No matching commands</li>}
          {filtered.map((command, index) => (
            <li
              key={command.name}
              role="option"
              aria-selected={index === activeIndex}
              className={
                index === activeIndex ? "palette__item palette__item--active" : "palette__item"
              }
              onMouseEnter={() => setActiveIndex(index)}
              onMouseDown={(event) => {
                event.preventDefault();
                runCommand(command);
              }}
            >
              <span className="palette__item-name">
                {command.name} <span className="palette__item-args">{command.args}</span>
              </span>
              <span className="palette__item-help">{command.help}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
