"""XDG-style filesystem paths for the `smac-server` lifecycle manager and
the `smac` TUI client.

Every function is derived from `Path.home()` rather than the process's
current working directory, so the lifecycle commands agree on the same
pidfile/DB/log locations regardless of where they're invoked from --
the "phantom old data from a different cwd" failure class the spec calls
out (Decision 4).

Tests exercise this in two ways: in-process tests monkeypatch
`Path.home`; subprocess-based lifecycle tests instead run `smac-server`
with the `HOME` environment variable overridden, which `Path.home()`
honors on Linux/macOS (via `os.path.expanduser("~")`).
"""

from pathlib import Path


def config_dir() -> Path:
    """`~/.config/smac` -- pidfile and session live here. Created on demand."""
    path = Path.home() / ".config" / "smac"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_dir() -> Path:
    """`~/.local/state/smac` -- the server log lives here. Created on demand."""
    path = Path.home() / ".local" / "state" / "smac"
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    """`~/.local/share/smac` -- the managed database lives here. Created on demand."""
    path = Path.home() / ".local" / "share" / "smac"
    path.mkdir(parents=True, exist_ok=True)
    return path


def pidfile_path() -> Path:
    """`~/.config/smac/server.pid` -- JSON `{pid, port, db_path, started_at}`."""
    return config_dir() / "server.pid"


def session_path() -> Path:
    """`~/.config/smac/session.json` -- the TUI's saved login session (chmod 600)."""
    return config_dir() / "session.json"


def db_path() -> Path:
    """`~/.local/share/smac/smac.db` -- the managed server's pinned database."""
    return data_dir() / "smac.db"


def log_path() -> Path:
    """`~/.local/state/smac/server.log` -- the managed server's stdout/stderr."""
    return state_dir() / "server.log"
