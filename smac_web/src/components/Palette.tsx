import { type KeyboardEvent, useEffect, useMemo, useState } from "react";
import {
  COMMANDS,
  type Command,
  type CommandContext,
  requiredCapHint,
  requiredCapsFor,
} from "../lib/commands";

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
  /** The caller's current capability check (`state/workspace.tsx`'s
   * `hasCap`) -- decides which entries render dimmed per `lib/commands.ts`'s
   * `REQUIRED_CAP` map (task-4 brief). */
  hasCap: (cap: string) => boolean;
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

/**
 * Finding 1 (final review, IMPORTANT): `matches()` alone lets a query that
 * exactly types one command's name multi-match a REGISTRY-ADJACENT command
 * whose name is a strict prefix of it -- `/channel` and `/channel create`
 * both satisfy `needle.startsWith(name)` for the query "channel create
 * standup", and `/channel`/`/channels` both satisfy it for "channels".
 * Registry order (not intent) then decided which one Enter ran, so typing
 * a canonical command exactly and hitting Enter could silently run a
 * DIFFERENT command with the rest of the text as garbage `args`.
 *
 * This score breaks that tie the way the fix direction specifies: among
 * everything `matches()` already let through, whichever command's name is
 * the LONGEST prefix the (trimmed) query actually starts with wins --
 * "/channel create" (15 chars) outranks "/channel" (7 chars) for "channel
 * create standup"; "/channels" (8 chars) outranks "/channel" (7 chars) for
 * "channels"; an exact-name match is just the query-length-equals-name-
 * length case of the same rule, so it needs no separate tier. Anything
 * that only matched via fuzzy substring/help-text browsing (not a name
 * prefix at all) scores -1 and sorts after every real prefix match, in
 * its original registry order (stable sort) -- unchanged from before.
 */
function rankScore(command: Command, needle: string): number {
  const name = command.name.slice(1).toLowerCase();
  return needle.startsWith(name) ? name.length : -1;
}

function splitArgs(command: Command, query: string): string {
  const nameNoSlash = command.name.slice(1).toLowerCase();
  if (query.toLowerCase().startsWith(nameNoSlash)) {
    return query.slice(nameNoSlash.length).trim();
  }
  return "";
}

export default function Palette({ open, initialQuery, onClose, buildContext, hasCap }: PaletteProps) {
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

  const filtered = useMemo(() => {
    const needle = query.toLowerCase();
    // `.sort()` on the array `.filter()` just produced is stable (ES2019+)
    // and doesn't touch `COMMANDS` itself -- ties (including the all-fuzzy,
    // score -1 case) keep the registry's own order, exactly as before this
    // fix. See `rankScore`'s docstring for what the score means.
    return COMMANDS.filter((c) => matches(c, query)).sort(
      (a, b) => rankScore(b, needle) - rankScore(a, needle)
    );
  }, [query]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  if (!open) {
    return null;
  }

  /** `true` if `command` is gated and the caller holds NONE of its
   * required capabilities -- Enter/click must both refuse to run it
   * (task-4 brief), matching the dimmed rendering below. */
  function isGated(command: Command): boolean {
    const caps = requiredCapsFor(command);
    return caps.length > 0 && !caps.some((cap) => hasCap(cap));
  }

  function runCommand(command: Command) {
    if (isGated(command)) {
      return;
    }
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
          {filtered.map((command, index) => {
            const requiredCaps = requiredCapsFor(command);
            const gated = requiredCaps.length > 0 && !requiredCaps.some((cap) => hasCap(cap));
            const classNames = ["palette__item"];
            if (index === activeIndex) classNames.push("palette__item--active");
            if (gated) classNames.push("palette__item--gated");
            return (
              <li
                key={command.name}
                role="option"
                aria-selected={index === activeIndex}
                aria-disabled={gated ? "true" : undefined}
                className={classNames.join(" ")}
                onMouseEnter={() => setActiveIndex(index)}
                onMouseDown={(event) => {
                  event.preventDefault();
                  runCommand(command);
                }}
              >
                <span className="palette__item-name">
                  {command.name} <span className="palette__item-args">{command.args}</span>
                </span>
                <span className="palette__item-help">
                  {command.help}
                  {gated && (
                    <span className="palette__item-hint"> — {requiredCapHint(requiredCaps)}</span>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
