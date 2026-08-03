"""smac_cli: the `smac-server` lifecycle manager and (a later task on this
branch) the `smac` TUI client.

A pure client package -- it never imports from `app/` directly. The one
exception is `smac_cli.server`, which spawns the backend as a subprocess
by module string (`app.main:app`, handed to `uvicorn`), never by importing
`app` at the Python level for anything other than locating the repo
checkout (see `smac_cli.server._find_repo_root`).

`CLIENT_VERSION` mirrors `app.__version__` (SMAC-72 task 1) so the TUI's
`/meta` handshake has a client-side version to compare against. Keep it in
sync by hand until packaging wires up a single shared source.
"""

CLIENT_VERSION = "0.6.0"
