"""The `/`-command registry the footer input contract dispatches into.

`COMMANDS` maps a bare command name (no leading slash) to a `(handler,
one_line_help)` pair. Every handler has the signature `def cmd_x(app:
SmacApp, args: str) -> None` -- `smac_cli.app` looks the name up, hides
the pull-up, and runs the handler on a worker thread (`SmacApp._run_command`)
so the blocking `SmacApi` calls and the blocking `app.ask()`/`app.choose()`
inline-form helpers never freeze the event loop.

This task (SMAC-72 task 4) registers exactly the four commands the shell
itself depends on to get a caller logged in: `/register`, `/login`,
`/help`, `/quit`. Tasks 5-6 add `/whoami`, `/channels`, `/channel`,
`/unreads`, `/workspace delete` etc. by adding entries to this dict --
`smac_cli.app` never needs to change for that.

A handler that raises `smac_cli.app.FormCancelled` (via `ask()`/`choose()`
after Esc) is caught by the caller (`SmacApp._run_command`) -- handlers
don't need their own `try`/`except` around it. A handler that lets a
`smac_cli.errors.SmacError` propagate gets it turned into a system line
by the same caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

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
        return

    if len(matches) > 1:
        app.set_header("SMAC — choose a workspace")
        app.system_line("your accounts:")
        items = [(m["workspace_id"], m["workspace_name"]) for m in matches]
        chosen = app.choose(items)
        if chosen is None:
            return
        workspace_id, workspace_name = chosen
        app.api.login(workspace_id, email, password)
        cache_workspace_name(workspace_id, workspace_name)
        app.enter_workspace(workspace_name, "general")
        return

    # Zero matches: the join frame -- live-filtered public directory.
    app.set_header("SMAC — no workspace yet: join one")
    app.system_line("public workspaces (type to search):")

    def _search(query: str) -> list[tuple[str, str]]:
        return [
            (w["workspace_id"], w["workspace_name"])
            for w in app.api.search_public(query)
        ]

    chosen = app.choose(_search(""), filterable=True, on_filter=_search)
    if chosen is None:
        return
    workspace_id, workspace_name = chosen
    first_name = app.ask("first name")
    last_name = app.ask("last name")
    session = app.api.register_into(
        workspace_id, email, password, first_name, last_name
    )
    cache_workspace_name(session.workspace_id, workspace_name)
    app.enter_workspace(workspace_name, "general")


@_register("help", "command list")
def cmd_help(app: "SmacApp", args: str) -> None:
    """`/help`: the same content the pull-up shows, one line per command."""
    app.system_line("commands")
    for name, (_, help_text) in COMMANDS.items():
        app.system_line(f"/{name}  {help_text}")
    app.system_line("anything without / is a message to the current channel")


@_register("quit", "exit")
def cmd_quit(app: "SmacApp", args: str) -> None:
    """`/quit`: clean shutdown -- session stays saved for next launch."""
    app.system_line("goodbye — session saved, see you next time")
    app.call_from_thread(app.exit)
