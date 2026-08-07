/**
 * THE command registry (constitution §4 -- design/commands.md is the
 * normative table; `__tests__/palette.test.tsx`'s drift-guard test parses
 * that file and asserts this list's `name`+`help` match it exactly, name
 * for name, row for row). Both the Cmd-K palette (`components/Palette.tsx`)
 * and the composer's leading-`/` grammar (`components/Composer.tsx`)
 * render/filter this SAME array -- there is exactly one place a SMAC
 * command's name and one-line help text are spelled out for the web
 * surface, per the constitution's "same names, same help strings" rule.
 *
 * `run(ctx)` is deliberately synchronous-looking (it may kick off a
 * promise internally, but the palette doesn't await it) -- commands are
 * fire-and-forget UI actions, not something the caller blocks on.
 *
 * `/invite` and `/workspace delete` land on Settings (web spec §2: that's
 * where invite/workspace administration for the CURRENT workspace lives)
 * via `goToSettings(section)`, landing directly on the right panel rather
 * than Settings' default. `/workspace create` and `/join` do NOT go to
 * Settings -- founding or joining a workspace isn't "administering the
 * one you're in", it's the same account-scoped flow the Rail's "Create or
 * join a workspace…" switcher entry already uses (`navigateAuthScreen`),
 * so both commands reuse that existing screen instead of duplicating it
 * inside Settings (task-5 brief: "wire them to land on the right panel" --
 * for these two, the right panel is the one that already exists).
 */

export type CommandContext = {
  /** Whatever followed the matched command's name in the raw input that
   * selected it (e.g. typing "/channel general" and hitting Enter on the
   * "/channel" entry gives `args === "general"`). Trimmed; may be empty
   * if the command was invoked with no further text. */
  args: string;
  /** Switch the unauthenticated/pre-workspace screen machine
   * (`state/auth.tsx`) to a specific screen. */
  navigateAuthScreen: (screen: "welcome" | "login" | "register" | "create-or-join" | "join") => void;
  /** Log out of the current session (account + workspace tiers, best-effort
   * server-side revoke + unconditional local clear -- `api.logout()`). */
  logout: () => Promise<void>;
  /** Switch the current room to the channel with this name (case-
   * insensitive); no-op if no channel matches. */
  switchChannelByName: (name: string) => void;
  /** Create a new channel with this name and switch to it. */
  createChannel: (name: string) => Promise<void>;
  /** Re-fetch the unread/mention badge overview -- the sidebar already
   * shows this live; running the command just guarantees freshness. */
  refreshUnreads: () => Promise<void>;
  /** Show the caller's identity card (web spec §2: reached from the Rail
   * avatar menu; the palette's `/whoami` triggers the same card). */
  showWhoami: () => void;
  /** Navigate to the Settings screen, optionally landing directly on one
   * of its panels (default: Agents, Settings' own first tab) -- the
   * administration home for the CURRENT workspace (web spec §2). */
  goToSettings: (section?: "agents" | "invites" | "workspace") => void;
  /** `true` if the caller's CURRENT capabilities (`state/workspace.tsx`'s
   * `hasCap`) include `cap` -- what `REQUIRED_CAP` below is checked
   * against to decide whether a gated command is runnable. */
  hasCap: (cap: string) => boolean;
};

export type Command = {
  name: string;
  args: string;
  help: string;
  run: (ctx: CommandContext) => void;
};

/** Fire-and-forget helper: log out, then land on `screen` -- used by
 * `/register`/`/login` when invoked from inside an already-authed
 * session (the constitution's command table lists both for every
 * surface with no "authed-only" carve-out; logging out first is the only
 * sensible interpretation of "log into your account" while already
 * logged into one). */
function logoutThenNavigate(
  ctx: CommandContext,
  screen: "welcome" | "login" | "register"
): void {
  void ctx.logout().then(() => ctx.navigateAuthScreen(screen));
}

export const COMMANDS: Command[] = [
  {
    name: "/register",
    args: "(none)",
    help: "Create a new account (account-first, two-step)",
    run: (ctx) => logoutThenNavigate(ctx, "register"),
  },
  {
    name: "/login",
    args: "(none)",
    help: "Log into your account and pick a workspace",
    run: (ctx) => logoutThenNavigate(ctx, "login"),
  },
  {
    name: "/workspace create",
    args: "<name>",
    help: "Found a new workspace",
    // Same screen the Rail switcher's "Create or join a workspace…" entry
    // opens -- founding a workspace isn't a Settings action.
    run: (ctx) => ctx.navigateAuthScreen("create-or-join"),
  },
  {
    name: "/workspace delete",
    args: "(none)",
    help: "Delete the current workspace (typed confirmation)",
    run: (ctx) => ctx.goToSettings("workspace"),
  },
  {
    name: "/join",
    args: "<code>",
    help: "Redeem an invite code to join a workspace",
    // `JoinScreen` already exists as the code-entry + public-directory
    // screen -- reuse it rather than a second entry point inside Settings.
    run: (ctx) => ctx.navigateAuthScreen("join"),
  },
  {
    name: "/invite",
    args: "(none)",
    help: "Mint and copy an invite code",
    run: (ctx) => ctx.goToSettings("invites"),
  },
  {
    name: "/channel",
    args: "<name>",
    help: "Switch to a channel",
    run: (ctx) => {
      if (ctx.args) {
        ctx.switchChannelByName(ctx.args);
      }
    },
  },
  {
    name: "/channel create",
    args: "<name>",
    help: "Create a new channel",
    run: (ctx) => {
      if (ctx.args) {
        void ctx.createChannel(ctx.args);
      }
    },
  },
  {
    name: "/channels",
    args: "(none)",
    help: "Show channels with unread/mention badges (alias: /unreads)",
    run: (ctx) => void ctx.refreshUnreads(),
  },
  {
    name: "/whoami",
    args: "(none)",
    help: "Show your identity card",
    run: (ctx) => ctx.showWhoami(),
  },
  {
    name: "/help",
    args: "(none)",
    help: "List available commands",
    // The palette's own empty-filter state IS the help list (web spec
    // §2: "palette empty-state") -- selecting this entry has nothing
    // further to do.
    run: () => undefined,
  },
  {
    name: "/quit",
    args: "(none)",
    help: "Leave the app",
    // Web's "/quit" is "close tab" per the constitution's command table
    // -- the only in-app equivalent is logging out.
    run: (ctx) => void ctx.logout(),
  },
];

/**
 * Display-only capability gate, keyed by `Command.name` -- lives BESIDE
 * `COMMANDS` rather than as a field ON each entry (task-4 brief: "registry
 * entries themselves untouched", keeping `palette.test.tsx`'s drift-guard
 * diffing `COMMANDS`' `name`/`help` against `design/commands.md` blind to
 * this addition). A command with no entry here is ungated -- runnable by
 * anyone who can open the palette at all (every role: `Cap.POST` etc. are
 * baseline caps every member has). `components/Palette.tsx` looks a
 * selected/rendered command up here and, if the caller holds NONE of the
 * listed capabilities, renders it dimmed with a "requires ..." hint and
 * refuses to run it -- the server's own `require_cap` wall is the REAL
 * gate (constitution §7.5); this is belt-and-suspenders UI hygiene, same
 * posture as `Settings.tsx`'s tab omission.
 *
 * A value may be a single capability OR an array of alternatives ("gated
 * unless the caller holds AT LEAST ONE of these"). `/invite` is the array
 * case (task-5 brief, fix round): `InvitesPanel` mints a human code OR an
 * agent code depending on which mint cap the caller holds, so gating the
 * PALETTE entry on `mint_human_invites` alone locked an `agent_admin` --
 * who genuinely can mint an agent invite once landed on the Invites tab --
 * out of running `/invite` at all. Single-value entries (`/workspace
 * delete`) are unaffected.
 */
export const REQUIRED_CAP: Partial<Record<string, string | string[]>> = {
  "/invite": ["mint_human_invites", "mint_agent_invites"],
  "/workspace delete": "manage_workspace",
};

/** `REQUIRED_CAP[command.name]`, normalized to an array (`[]` if the
 * command is ungated) -- the one place that normalization happens, so
 * `Palette.tsx`'s gating check and hint rendering can't drift apart on
 * how they read a single-vs-array entry. */
export function requiredCapsFor(command: Command): string[] {
  const cap = REQUIRED_CAP[command.name];
  if (cap === undefined) return [];
  return Array.isArray(cap) ? cap : [cap];
}

function capLabel(cap: string): string {
  if (cap === "manage_workspace" || cap === "mint_human_invites") {
    return "Workspace Admin";
  }
  if (cap === "manage_agents" || cap === "mint_agent_invites") {
    return "Agent Admin";
  }
  return cap;
}

/** Human-readable "requires ..." hint for a gated command's required
 * capability/capabilities, per the task-4 brief's two named examples
 * ("requires Workspace Admin" for a workspace-wide cap, "requires Agent
 * Admin" for an agent-only one). An array joins with "or" (`/invite`:
 * "requires Workspace Admin or Agent Admin" -- holding EITHER is enough
 * to un-gate it, `requiredCapsFor`'s `.some()` check). Falls back to the
 * raw capability name for any future entry this map doesn't special-case. */
export function requiredCapHint(cap: string | string[]): string {
  const caps = Array.isArray(cap) ? cap : [cap];
  return `requires ${caps.map(capLabel).join(" or ")}`;
}
