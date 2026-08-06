"""The `/`-command registry the footer input contract dispatches into.

`COMMANDS` maps a bare command name (no leading slash) to a `(handler,
one_line_help)` pair. Every handler has the signature `def cmd_x(app:
SmacApp, args: str) -> None` -- `smac_cli.app` looks the name up, hides
the pull-up, and runs the handler on a worker thread (`SmacApp._run_command`)
so the blocking `SmacApi` calls and the blocking `app.ask()`/`app.choose()`
inline-form helpers never freeze the event loop.

Identity v2 (SMAC-79 Task 3, spec §6) reworks the account/workspace
commands around the server's two auth tiers: `/register` is now
account-only (email+password, no workspace); `/workspace create <name>`
founds a new one; `/join <code>` redeems a shareable invite code;
`/login` discovers real memberships (from `POST /accounts/login`'s
response) rather than simulating them; `/invite` (admin) mints a
shareable code for `/workspace create`d workspaces. `/channel`,
`/whoami`, `/channels`/`/unreads`, `/workspace delete`, `/help`, `/quit`
are unchanged (SMAC-72's task 6 already built and tested those against
the workspace tier, which this rework doesn't touch).

A handler that raises `smac_cli.app.FormCancelled` (via `ask()`/`choose()`
after Esc) is caught by the caller (`SmacApp._run_command`) -- handlers
don't need their own `try`/`except` around it. A handler that lets a
`smac_cli.errors.SmacError` propagate gets it turned into a message-only
system line by that same caller -- this is what makes `NoWorkspaceError`
(typing a workspace-tier command before ever entering one) and
`/workspace delete`'s not-an-admin case both "just work" with no
special-casing here. `/channel create`'s 409 is the one narrow
exception: spec §0.2's frame for it shows the server's full `code:
message` envelope verbatim, not just the message, so `cmd_channel`
catches exactly `NameTakenError` itself (never the broader `SmacError`)
rather than letting that one case fall through to the generic handling
every other command gets.
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


@_register("register", "create your account (email + password)")
def cmd_register(app: "SmacApp", args: str) -> None:
    """`/register`: account-only signup (spec §3/§6) -- `POST /accounts`,
    no workspace involved at all. Lands in the "signed in, no workspace
    yet" state (`SmacApp.show_no_workspace_state`): the account-created
    banner stays on screen, followed by the three next steps.
    """
    app.set_header("SMAC — creating your account")
    email = app.ask("email")
    password = app.ask("password", password=True)

    app.api.signup(email, password)
    app.system_line(f"account created: {email}")
    app.show_no_workspace_state()


def _ask_display_name(app: "SmacApp") -> tuple[str, str]:
    """The per-workspace display name every workspace-birth door needs
    (`first_name`/`last_name`), shared by `/workspace create` and `/join`."""
    first_name = app.ask("first name")
    last_name = app.ask("last name")
    return first_name, last_name


@_register("workspace", "create <name>, or delete this workspace (admin)")
def cmd_workspace(app: "SmacApp", args: str) -> None:
    """`/workspace create <name>`: found a brand-new workspace, asking the
    founder's per-workspace display name + visibility (spec §6). `/workspace
    delete`: the existing admin-only, two-step typed confirmation
    (SMAC-72 task 6, unchanged). Anything else is usage help -- the
    server enforces every real rule (admin-only delete, workspace-name
    uniqueness) regardless of what the client shows.
    """
    from smac_cli.app import cache_workspace_name

    text = args.strip()
    parts = text.split(None, 1)
    sub = parts[0].lower() if parts else ""

    if sub == "create":
        name = parts[1].strip() if len(parts) > 1 else ""
        if not name:
            app.system_line("usage: /workspace create <name>")
            return
        first_name, last_name = _ask_display_name(app)
        visibility = app.ask("visibility [private]", default="private")
        if visibility not in ("public", "private"):
            visibility = "private"
        session, workspace_name = app.api.create_workspace(
            name, visibility, first_name, last_name
        )
        assert session.workspace_id is not None  # just minted by create_workspace
        cache_workspace_name(session.workspace_id, workspace_name)
        app.enter_workspace(workspace_name, "general")
        app.system_line(f'workspace "{workspace_name}" founded — you\'re in #general')
        app.enter_general()
        return

    if sub == "delete":
        _confirm_and_delete_workspace(app)
        return

    app.system_line("usage: /workspace create <name>  or  /workspace delete")


@_register("join", "join a workspace via invite code")
def cmd_join(app: "SmacApp", args: str) -> None:
    """`/join <code>`: redeem a shareable invite code (`POST /workspaces/
    join`, spec §3/§6) -- asks the per-workspace display name (the
    account may already exist, but this is always a brand-new membership,
    so a name is always needed) then lands in `#general` of whichever
    workspace the code belongs to.

    Logged out, this used to prompt for both names and only THEN fail
    deep inside `api.join_code` with a bare "No active session." --
    final-review MINOR-4. The session check now runs BEFORE any
    prompting, so a logged-out `/join <code>` fails immediately with an
    actionable next step instead of wasting two answers first.
    """
    from smac_cli.app import cache_workspace_name

    code = args.strip()
    if not code:
        app.system_line("usage: /join <code>")
        return
    if app.api.session is None or app.api.session.account_access_token is None:
        app.system_line("create an account first: /register (then /join <code>)")
        return
    first_name, last_name = _ask_display_name(app)
    session, workspace_name = app.api.join_code(code, first_name, last_name)
    assert session.workspace_id is not None  # just minted by join_code
    cache_workspace_name(session.workspace_id, workspace_name)
    app.enter_workspace(workspace_name, "general")
    app.system_line(f'joined "{workspace_name}" — you\'re in #general')
    app.enter_general()


@_register("invite", "mint a shareable join code")
def cmd_invite(app: "SmacApp", args: str) -> None:
    """`/invite`: mint a shareable multi-use code (`POST /workspaces/
    {id}/invites`, gated server-side to human members of the workspace --
    `app/authorization.py:authorize_management_action`, unchanged by this
    task) and print both the code AND the exact line to hand a
    prospective member -- they need to `/register` an account first
    (codes are redeemed by `/join`, which is account-authed), then
    `/join` with it.
    """
    invite = app.api.mint_invite_code()
    code = invite["code"]
    app.system_line(f"invite code: {code}")
    app.system_line(f"tell them: smac → /register → /join {code}")


@_register("login", "log in (email + password)")
def cmd_login(app: "SmacApp", args: str) -> None:
    """`/login`: global login (`POST /accounts/login`, spec §2/§6) --
    branches on the REAL memberships the response carries:

    - **one match** -- enter that workspace directly (`api.enter_workspace`
      mints a fresh workspace token pair for it).
    - **several matches** -- the workspace picker (`app.choose`), same as
      before.
    - **zero matches** -- the public-directory join frame: a live-filtered
      `app.choose(..., filterable=True)` over `api.search_public`, then
      name prompts + `api.join_public` for whichever workspace is picked.

    Unlike the retired `/auth/discover`-based flow, a WRONG password now
    raises a real `AuthError` from `api.login` itself (propagates to
    `SmacApp._run_command`'s generic message-only handling) -- there's no
    more "zero matches" ambiguity between an unknown email and a typo'd
    password to soften with a hint; "zero matches" now only ever means
    "these are valid credentials, but this account has no workspace yet."
    """
    from smac_cli.app import cache_workspace_name

    email = app.ask("email")
    password = app.ask("password", password=True)
    _, memberships = app.api.login(email, password)

    if len(memberships) == 1:
        match = memberships[0]
        app.api.enter_workspace(match["workspace_id"])
        cache_workspace_name(match["workspace_id"], match["workspace_name"])
        app.enter_workspace(match["workspace_name"], "general")
        app.enter_general()
        return

    if len(memberships) > 1:
        app.set_header("SMAC — choose a workspace")
        app.write_line("your workspaces:")
        items = [(m["workspace_id"], m["workspace_name"]) for m in memberships]
        workspace_id, workspace_name = app.choose(items)
        app.api.enter_workspace(workspace_id)
        cache_workspace_name(workspace_id, workspace_name)
        app.enter_workspace(workspace_name, "general")
        app.enter_general()
        return

    # Zero memberships: the join frame -- live-filtered public directory.
    app.set_header("SMAC — no workspace yet: join one")
    app.system_line(
        "no workspaces yet for this account — pick a public one below, "
        "/join <code> if you have one, or /workspace create <name>"
    )
    app.write_line("public workspaces (type to search):")

    def _search(query: str) -> list[tuple[str, str]]:
        return [
            (w["workspace_id"], w["workspace_name"])
            for w in app.api.search_public(query)
        ]

    app.write_line("(or /workspace create <name>, or /join <code>, or Esc to go back)")
    workspace_id, workspace_name = app.choose(
        _search(""), filterable=True, on_filter=_search
    )
    first_name, last_name = _ask_display_name(app)
    session, workspace_name = app.api.join_public(workspace_id, first_name, last_name)
    assert session.workspace_id is not None  # just minted by join_public
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

    `GET /members/me` carries `role` and `workspace_visibility` (SMAC-72
    task 6 added the latter; SMAC-92 replaced the old boolean `is_admin`
    with `role`/`capabilities` -- see `app.schemas.MemberSelfOut`'s
    docstring) precisely so this command has somewhere to get both from.
    The role suffix is omitted for the baseline `member` role (the old
    `is_admin=False` case showed no suffix either) and rendered verbatim
    for anything else (`admin`, `agent_admin`, ...).
    """
    profile = app.api.whoami()
    first_name = profile.get("first_name") or ""
    last_name = profile.get("last_name") or ""
    full_name = f"{first_name} {last_name}".strip() or str(
        profile.get("member_name", "")
    )
    handle = profile.get("handle", "")
    role = profile.get("role") or "member"
    role_suffix = f" · {role}" if role != "member" else ""
    app.system_line(f"you: {full_name} (@{handle}){role_suffix}")
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


@_register("help", "command list")
def cmd_help(app: "SmacApp", args: str) -> None:
    """`/help`: the full command list (spec §0.2's `/help` frame, updated
    for the Identity v2 command set).

    Curated rather than generated straight off `COMMANDS`: `/channel` is
    one registry entry but earns two lines here (switch vs. create), and
    `/channels`/`/unreads` -- two registry entries, one handler -- collapse
    to the frame's single combined line. Every name here still comes
    straight from a real `COMMANDS` key, so this can never drift into
    describing a command that doesn't exist.
    """
    app.system_line("commands")
    app.system_line(f"/register          {COMMANDS['register'][1]}")
    app.system_line("/workspace create <name>  found a new workspace")
    app.system_line(f"/join <code>       {COMMANDS['join'][1]}")
    app.system_line(f"/login             {COMMANDS['login'][1]}")
    app.system_line(f"/invite            {COMMANDS['invite'][1]}")
    app.system_line(f"/whoami            {COMMANDS['whoami'][1]}")
    app.system_line("/channels /unreads your channels + unread badges")
    app.system_line("/channel <name>    switch channel")
    app.system_line("/channel create <name>  new channel")
    app.system_line("/workspace delete  delete this workspace (admin)")
    app.system_line(f"/quit              {COMMANDS['quit'][1]}")
    app.system_line(
        "anything without / is a message to the current channel (#general when you arrive)"
    )


@_register("quit", "exit")
def cmd_quit(app: "SmacApp", args: str) -> None:
    """`/quit`: clean shutdown -- session stays saved for next launch."""
    app.system_line("goodbye — session saved, see you next time")
    app.call_from_thread(app.exit)
