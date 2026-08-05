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
 * A few entries name a flow this branch hasn't built a dedicated screen
 * for yet (workspace create/delete, invite, join-by-code -- all need a
 * multi-field form, not a single args string, and Settings is where
 * workspace/invite administration lives per web spec §2). Task-3 brief:
 * these are allowed to `run` by navigating to the Settings stub screen
 * (Settings itself, and the real flows, are Task 5's job) -- noted in the
 * task-3 report rather than silently left half-built.
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
  /** Navigate to the (Task-5) Settings screen -- the stub target for
   * flows this branch doesn't build a dedicated UI for yet. */
  goToSettings: () => void;
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
    run: (ctx) => ctx.goToSettings(),
  },
  {
    name: "/workspace delete",
    args: "(none)",
    help: "Delete the current workspace (typed confirmation)",
    run: (ctx) => ctx.goToSettings(),
  },
  {
    name: "/join",
    args: "<code>",
    help: "Redeem an invite code to join a workspace",
    run: (ctx) => ctx.goToSettings(),
  },
  {
    name: "/invite",
    args: "(none)",
    help: "Mint and copy an invite code",
    run: (ctx) => ctx.goToSettings(),
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
