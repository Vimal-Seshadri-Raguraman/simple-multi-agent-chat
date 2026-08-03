"""The `/`-command registry the footer input contract dispatches into.

`COMMANDS` maps a bare command name (no leading slash) to a `(handler,
one_line_help)` pair. Every handler has the signature `def cmd_x(app:
SmacApp, args: str) -> None` -- `smac_cli.app` looks the name up, hides
the pull-up, and runs the handler on a worker thread (`SmacApp._run_command`)
so the blocking `SmacApi` calls and the blocking `app.ask()`/`app.choose()`
inline-form helpers never freeze the event loop.

Task 4 (SMAC-72) registered the four commands the shell itself depends on
to get a caller logged in: `/register`, `/login`, `/help`, `/quit`. Task 5
wired the live room (no new commands). This task (6, final) adds
`/whoami`, `/channels` (+ `/unreads`, the same handler under a second
name), `/channel create`, and `/workspace delete`, and completes `/help`
to describe all of them (spec §0.2).

A handler that raises `smac_cli.app.FormCancelled` (via `ask()`/`choose()`
after Esc) is caught by the caller (`SmacApp._run_command`) -- handlers
don't need their own `try`/`except` around it. A handler that lets a
`smac_cli.errors.SmacError` propagate gets it turned into a message-only
system line by that same caller -- this is what makes `/workspace
delete`'s not-an-admin case "just work" with no special-casing here.
`/channel create`'s 409 is the one narrow exception: spec §0.2's frame
for it shows the server's `code: message` envelope verbatim
(`channel_name_taken: A channel named '...' already exists...`), not just
the message, so `cmd_channel` catches exactly `NameTakenError` itself
(never the broader `SmacError` -- an unreachable server, a rate limit,
etc. during `/channel create` still falls through to the same generic
message-only handling every other command gets) rather than letting that
one case fall through to it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from smac_cli.errors import NameTakenError

if TYPE_CHECKING:
    from smac_cli.app import SmacApp

#: name (no leading "/") -> (handler, one-line help shown in the pull-up + /help).
COMMANDS: dict[str, tuple[Callable[["SmacApp", str], None], str]] = {}


def _register(
    name: str, help_text: str
) -> Callable[[Callable[["SmacApp", str], None]], Callable[["SmacApp", str], None]]:
    """Decorator: add a handler to `COMMANDS` under `name`."""

    def wrap(
        func: Callable[["SmacApp", str], None],
    ) -> Callable[["SmacApp", str], None]:
        COMMANDS[name] = (func, help_text)
        return func

    return wrap


@_register("register", "create your account + workspace")
def cmd_register(app: "SmacApp", args: str) -> None:
    """`/register`: the two-step account-then-workspace form (spec Frame 3).

    Underneath it is ONE atomic `POST /workspaces` (`api.register_found`)
    -- the two "steps" are presentation only, gathered via sequential
    `app.ask()` calls before the single API call fires. On success: an
    `── account created ──` / `── workspace founded ──` pair of system
    lines, and the header switches to `<workspace> — #general`.
    """
    from smac_cli.app import cache_workspace_name

    app.set_header("SMAC — creating your account")
    app.system_line("step 1 of 2: create your account")
    email = app.ask("email")
    password = app.ask("password", password=True)
    first_name = app.ask("first name")
    last_name = app.ask("last name")

    app.system_line("step 2 of 2: your workspace")
    workspace_name = app.ask("workspace name")
    visibility = app.ask("visibility [private]", default="private")
    if visibility not in ("public", "private"):
        visibility = "private"

    session = app.api.register_found(
        email, password, first_name, last_name, workspace_name, visibility
    )
    cache_workspace_name(session.workspace_id, workspace_name)
    member = app.api.whoami()
    handle = member.get("handle", "")

    app.enter_workspace(workspace_name, "general")
    app.system_line(f"account created: @{handle} (admin)")
    app.system_line(f'workspace "{workspace_name}" founded — you\'re in #general')
    app.enter_general()


@_register("login", "log in (email + password)")
def cmd_login(app: "SmacApp", args: str) -> None:
    """`/login`: email + password only, then discovery decides the branch
    (spec §2.5 + Frames 3b/3c):

    - **one match** -- log straight into that workspace.
    - **several matches** -- the workspace picker (`app.choose`).
    - **zero matches** -- the public-directory join frame: a live-filtered
      `app.choose(..., filterable=True)` over `api.search_public`, then
      name prompts + `api.register_into` for whichever workspace is picked.
    """
    from smac_cli.app import cache_workspace_name

    email = app.ask("email")
    password = app.ask("password", password=True)
    matches = app.api.discover(email, password)

    if len(matches) == 1:
        match = matches[0]
        app.api.login(match["workspace_id"], email, password)
        cache_workspace_name(match["workspace_id"], match["workspace_name"])
        app.enter_workspace(match["workspace_name"], "general")
        app.enter_general()
        return

    if len(matches) > 1:
        app.set_header("SMAC — choose a workspace")
        app.write_line("your accounts:")
        items = [(m["workspace_id"], m["workspace_name"]) for m in matches]
        workspace_id, workspace_name = app.choose(items)
        app.api.login(workspace_id, email, password)
        cache_workspace_name(workspace_id, workspace_name)
        app.enter_workspace(workspace_name, "general")
        app.enter_general()
        return

    # Zero matches: the join frame -- live-filtered public directory.
    app.set_header("SMAC — no workspace yet: join one")
    app.write_line("public workspaces (type to search):")

    def _search(query: str) -> list[tuple[str, str]]:
        return [
            (w["workspace_id"], w["workspace_name"])
            for w in app.api.search_public(query)
        ]

    app.write_line("(or /register to create your own)")
    workspace_id, workspace_name = app.choose(
        _search(""), filterable=True, on_filter=_search
    )
    first_name = app.ask("first name")
    last_name = app.ask("last name")
    session = app.api.register_into(
        workspace_id, email, password, first_name, last_name
    )
    cache_workspace_name(session.workspace_id, workspace_name)
    app.enter_workspace(workspace_name, "general")
    app.enter_general()


@_register("channel", "switch channel, or create <name>")
def cmd_channel(app: "SmacApp", args: str) -> None:
    """`/channel <name>`: switch the live room to another channel.
    `/channel create <name>`: create one and switch to it (spec §0.2).

    Case-insensitive (SMAC-68 guarantees workspace-wide channel-name
    uniqueness regardless of case). An unknown name to switch to shows a
    system line and leaves the current channel untouched. A duplicate
    name to create raises `NameTakenError`, caught here specifically (and
    ONLY that class -- any other `SmacError`, e.g. `Unreachable` or
    `RateLimitedError`, is deliberately left to propagate to
    `SmacApp._run_command`'s generic message-only handling, same as every
    other command) so the server's full `code: message` envelope renders
    verbatim, matching spec §0.2's frame -- `enter_channel` is never
    reached in the 409 case.
    """
    text = args.strip()
    parts = text.split(None, 1)
    if parts and parts[0].lower() == "create":
        name = parts[1].strip() if len(parts) > 1 else ""
        if not name:
            app.system_line("usage: /channel create <name>")
            return
        try:
            created = app.api.create_channel(name)
        except NameTakenError as exc:
            app.system_line(f"{exc.code}: {exc.message}")
            return
        app.enter_channel(created["channel_id"], created["channel_name"])
        app.system_line(f"channel #{created['channel_name']} created — you're in it")
        return

    name = text
    if not name:
        app.system_line("usage: /channel <name>")
        return
    target = name.lower()
    channels = app.api.channels()
    match = next((c for c in channels if c["channel_name"].lower() == target), None)
    if match is None:
        app.system_line(f"no such channel: #{name}")
        return
    app.enter_channel(match["channel_id"], match["channel_name"])


@_register("whoami", "who am I")
def cmd_whoami(app: "SmacApp", args: str) -> None:
    """`/whoami`: your identity + this workspace, as system lines (spec §0.2).

    `GET /members/me` carries `is_admin` and `workspace_visibility`
    (SMAC-72 task 6 addition -- see `app.schemas.MemberSelfOut`'s
    docstring) precisely so this command has somewhere to get both from;
    neither was on any response before this task.
    """
    profile = app.api.whoami()
    first_name = profile.get("first_name") or ""
    last_name = profile.get("last_name") or ""
    full_name = f"{first_name} {last_name}".strip() or str(
        profile.get("member_name", "")
    )
    handle = profile.get("handle", "")
    admin_suffix = " · admin" if profile.get("is_admin") else ""
    app.system_line(f"you: {full_name} (@{handle}){admin_suffix}")
    workspace_name = app.workspace_name or ""
    visibility = profile.get("workspace_visibility", "")
    app.system_line(f"workspace: {workspace_name} ({visibility})")


def _channel_row_line(row: dict[str, Any], current_channel_id: str | None) -> str:
    """One `/channels` table row: `#name  ·  caught up|N unread  [🔔 N
    mention(s)]  [(here)]` (spec §0.2's `/channels` frame)."""
    unread_count = int(row.get("unread_count") or 0)
    status = "caught up" if unread_count == 0 else f"{unread_count} unread"
    mention_count = int(row.get("mention_count") or 0)
    if mention_count:
        noun = "mention" if mention_count == 1 else "mentions"
        status += f"  🔔 {mention_count} {noun}"
    line = f"#{row['channel_name']}    ·  {status}"
    if row.get("channel_id") == current_channel_id:
        line += "  (here)"
    return line


@_register("channels", "your channels + unread badges")
@_register("unreads", "your channels + unread badges (same as /channels)")
def cmd_channels(app: "SmacApp", args: str) -> None:
    """`/channels` and `/unreads` (spec §0.2: "same table, both names kept
    for discoverability"): one `GET /unreads` call, `(here)` on the
    current channel, 🔔 on anything with a pending mention.
    """
    data = app.api.unreads()
    app.system_line("your channels")
    for row in data.get("unreads", []):
        app.system_line(_channel_row_line(row, app.current_channel_id))
    app.system_line("switch: /channel <name>")


def _confirm_and_delete_workspace(app: "SmacApp") -> None:
    """`/workspace delete`'s inline two-step typed confirmation (spec
    §0.2, "house style"): the workspace NAME, then the literal word
    `delete` -- a mismatch on either step aborts with a system line and
    nothing is called. On success, clears the session and returns to the
    Frame-1 welcome screen (`SmacApp.reset_to_logged_out`).
    """
    workspace_name = app.workspace_name or ""
    app.system_line(
        f'⚠ this permanently deletes "{workspace_name}": all channels, '
        "messages, accounts, and agent keys"
    )
    app.system_line("type the workspace name to continue")
    typed_name = app.ask("name")
    if typed_name != workspace_name:
        app.system_line("workspace name did not match — cancelled")
        return

    app.system_line("type delete to confirm")
    confirmation = app.ask("confirm")
    if confirmation != "delete":
        app.system_line("cancelled")
        return

    app.api.delete_workspace()
    app.reset_to_logged_out(f'workspace "{workspace_name}" deleted')


@_register("workspace", "delete this workspace (admin)")
def cmd_workspace(app: "SmacApp", args: str) -> None:
    """`/workspace delete`: the only subcommand today. Anything else is
    usage help -- the server enforces admin-only regardless of what the
    client shows, so a non-admin still gets a clear (verbatim) rejection
    after typing through the confirmation."""
    if args.strip().lower() != "delete":
        app.system_line("usage: /workspace delete")
        return
    _confirm_and_delete_workspace(app)


@_register("help", "command list")
def cmd_help(app: "SmacApp", args: str) -> None:
    """`/help`: the full command list (spec §0.2's `/help` frame).

    Curated rather than generated straight off `COMMANDS`: `/channel` is
    one registry entry but earns two lines here (switch vs. create), and
    `/channels`/`/unreads` -- two registry entries, one handler -- collapse
    to the frame's single combined line. Every name here still comes
    straight from a real `COMMANDS` key, so this can never drift into
    describing a command that doesn't exist.
    """
    app.system_line("commands")
    app.system_line(f"/register          {COMMANDS['register'][1]}")
    app.system_line(f"/login             {COMMANDS['login'][1]}")
    app.system_line(f"/whoami            {COMMANDS['whoami'][1]}")
    app.system_line("/channels /unreads your channels + unread badges")
    app.system_line("/channel <name>    switch channel")
    app.system_line("/channel create <name>  new channel")
    app.system_line(f"/workspace delete  {COMMANDS['workspace'][1]}")
    app.system_line(f"/quit              {COMMANDS['quit'][1]}")
    app.system_line(
        "anything without / is a message to the current channel (#general when you arrive)"
    )


@_register("quit", "exit")
def cmd_quit(app: "SmacApp", args: str) -> None:
    """`/quit`: clean shutdown -- session stays saved for next launch."""
    app.system_line("goodbye — session saved, see you next time")
    app.call_from_thread(app.exit)
