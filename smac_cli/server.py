"""`smac-server`: lifecycle manager for the SMAC backend.

Spawns `uvicorn app.main:app` as a detached subprocess with a pinned
`DATABASE_URL` (so `--start`/`--stop`/`--status`/`--delete-db` always agree
on the same database regardless of the caller's cwd -- spec Decision 4),
tracks it via a JSON pidfile, and never pattern-matches process names:
start/stop/status all key off the recorded pid only.

Packaging note (spec S3, documented fallback): `app.database.init_db()`
resolves its Alembic config relative to `app`'s own installed location
(`Path(__file__).resolve().parent.parent`), not the caller's cwd. That
works unmodified for the dev checkout and for `pip install -e .` (an
editable install still points `app.__file__` at the checkout), but NOT for
a non-editable install that copies `app/` into `site-packages/` without
`alembic.ini`/`alembic/` alongside it -- bundling those as package data for
arbitrary install layouts was judged disproportionate for v1. `_find_repo_root`
below performs the same resolution as a preflight check so `--start` fails
fast with a clear message instead of a silent uvicorn crash.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from smac_cli.paths import db_path, log_path, pidfile_path

#: How long `--start` waits for the spawned server's `/meta` to respond.
_READY_TIMEOUT_S = 15.0
_READY_POLL_INTERVAL_S = 0.2

#: How long `--stop` waits after SIGTERM before escalating to SIGKILL.
_STOP_TIMEOUT_S = 5.0
_STOP_POLL_INTERVAL_S = 0.1

_DEFAULT_PORT = 8000

#: Exact wording pinned by the packaging fallback test -- spec S3.
REPO_CHECKOUT_ERROR = (
    "smac-server currently requires running from the SMAC repo checkout"
)


def _repo_root_for(app_init_file: Path) -> Path | None:
    """Resolve the repo checkout root from `app`'s `__file__`, or None.

    The checkout root is `app`'s package directory's parent. It only
    counts as usable if `alembic.ini` and the `alembic/` migration scripts
    directory are found alongside it -- exactly what `app.database.init_db()`
    needs to run `alembic upgrade head` in the spawned server.
    """
    candidate = Path(app_init_file).resolve().parent.parent
    if (candidate / "alembic.ini").is_file() and (candidate / "alembic").is_dir():
        return candidate
    return None


def _find_repo_root() -> Path | None:
    """Locate the repo checkout the spawned server will need, or None.

    None means `app` is installed somewhere (e.g. a non-editable install
    into `site-packages/`) where the Alembic migration scripts aren't
    resolvable -- the documented packaging fallback (spec S3): callers
    must run from a repo checkout instead.
    """
    import app  # local import: only needed for this preflight check

    return _repo_root_for(Path(app.__file__))


def _is_alive(pid: int) -> bool:
    """Return whether `pid` refers to a live process we can signal."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_pidfile() -> dict[str, Any] | None:
    """Return the pidfile's contents if it names a live process, else None.

    A pidfile is stale if its process died without cleaning up after
    itself (a crash, `kill -9` from outside `--stop`, etc.). Stale and
    missing pidfiles mean the same thing to every caller -- "not
    running" -- so this reads, validates liveness, and deletes stale
    files right here, once, instead of pushing that logic onto every
    caller.
    """
    path = pidfile_path()
    if not path.exists():
        return None
    try:
        info = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        path.unlink(missing_ok=True)
        return None
    if not isinstance(info, dict):
        # Valid JSON (e.g. `[1]` or `"oops"`) but not the `{...}` shape
        # this file is always written as -- treat it exactly like a
        # stale/corrupt pidfile rather than letting `.get` below raise
        # `AttributeError` and crash every command that reads it.
        path.unlink(missing_ok=True)
        return None
    pid = info.get("pid")
    if not isinstance(pid, int) or not _is_alive(pid):
        path.unlink(missing_ok=True)
        return None
    return info


def _write_pidfile(info: dict[str, Any]) -> None:
    """Persist `info` (pid/port/db_path/started_at) as pidfile JSON."""
    pidfile_path().write_text(json.dumps(info))


def _terminate_child(process: "subprocess.Popen[bytes]") -> None:
    """TERM the still-running child, escalate to KILL if it doesn't exit.

    Used by `_start`'s readiness-timeout path (finding D): before this,
    a slow start (>15s) left the spawned uvicorn running unmanaged --
    no pidfile was written for it, so it held the port forever and every
    later `--start` failed mysteriously with nothing `--stop` could
    reach (no recorded pid). Mirrors `_stop`'s own TERM-then-KILL
    escalation, just against the `Popen` handle `_start` already has in
    hand rather than a pid read back from a file.
    """
    if process.poll() is not None:
        return  # already exited on its own
    process.terminate()
    try:
        process.wait(timeout=_STOP_TIMEOUT_S)
        return
    except subprocess.TimeoutExpired:
        pass
    process.kill()
    try:
        process.wait(timeout=_STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        pass


def _tail_log(n: int = 20) -> list[str]:
    """Return the last `n` lines of the server log, or `[]` if absent."""
    path = log_path()
    if not path.exists():
        return []
    return path.read_text(errors="replace").splitlines()[-n:]


def _start(port: int) -> None:
    """Spawn a detached `uvicorn app.main:app`, wait for it to be ready.

    Refuses (no-op) if a pidfile names a live process already. On
    success, writes the pidfile and prints the URL. On a readiness
    timeout, terminates the still-running child (finding D -- it used to
    be left running unmanaged, holding the port forever with no pidfile
    to reach it by), prints the last 20 log lines, and exits 1 -- no
    pidfile is written, so a subsequent `--start` retries cleanly.
    """
    running = _read_pidfile()
    if running is not None:
        print(
            f"smac-server already running (pid {running['pid']}) at "
            f"http://127.0.0.1:{running['port']}"
        )
        return

    repo_root = _find_repo_root()
    if repo_root is None:
        print(REPO_CHECKOUT_ERROR)
        raise SystemExit(1)

    db = db_path()
    log = log_path()
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}"}

    with open(log, "a") as log_file:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=str(repo_root),
            env=env,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )

    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + _READY_TIMEOUT_S
    ready = False
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break  # the child already exited -- no point continuing to poll
        try:
            response = httpx.get(f"{url}/meta", timeout=1.0)
            if response.status_code == 200:
                ready = True
                break
        except httpx.HTTPError:
            pass
        time.sleep(_READY_POLL_INTERVAL_S)

    if not ready:
        _terminate_child(process)
        print("smac-server did not become ready in time -- last log lines:")
        for line in _tail_log(20):
            print(line)
        raise SystemExit(1)

    _write_pidfile(
        {
            "pid": process.pid,
            "port": port,
            "db_path": str(db),
            "started_at": time.time(),
        }
    )
    print(f"smac-server started (pid {process.pid}) at {url}")


def _stop() -> None:
    """SIGTERM the running server, escalate to SIGKILL, clean up the pidfile.

    "Not running" (missing or stale pidfile) is a no-op, reported as such.
    """
    info = _read_pidfile()
    if info is None:
        print("smac-server not running")
        return

    pid = int(info["pid"])
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass  # already gone between the liveness check and here

    deadline = time.monotonic() + _STOP_TIMEOUT_S
    while time.monotonic() < deadline and _is_alive(pid):
        time.sleep(_STOP_POLL_INTERVAL_S)

    if _is_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        deadline = time.monotonic() + _STOP_TIMEOUT_S
        while time.monotonic() < deadline and _is_alive(pid):
            time.sleep(_STOP_POLL_INTERVAL_S)

    pidfile_path().unlink(missing_ok=True)
    print(f"smac-server stopped (pid {pid})")


def _status() -> None:
    """Print pid/port/db/log for a running server, or "not running"."""
    info = _read_pidfile()
    if info is None:
        print("smac-server not running")
        return
    print(f"pid: {info['pid']}")
    print(f"port: {info['port']}")
    print(f"db: {info['db_path']}")
    print(f"log: {log_path()}")


def _delete_db() -> None:
    """Stop the server if running, then delete the DB after typed confirmation.

    Requires the literal text "delete" (whitespace-stripped) on stdin;
    anything else, or a closed/exhausted stdin (`EOFError`), aborts with
    the file left intact. The next `--start` builds a fresh, fully
    migrated database.
    """
    if _read_pidfile() is not None:
        print("stopping running smac-server before deleting its database")
        _stop()

    path = db_path()
    if not path.exists():
        print("no database file to delete")
        return

    print(f"this will permanently delete {path}")
    try:
        confirmation = input("type 'delete' to confirm: ")
    except EOFError:
        print("aborted -- database left intact")
        return

    if confirmation.strip() != "delete":
        print("aborted -- database left intact")
        return

    path.unlink()
    print(f"deleted {path}")


def _build_parser() -> argparse.ArgumentParser:
    """Build the `smac-server` argument parser."""
    parser = argparse.ArgumentParser(
        prog="smac-server", description="Lifecycle manager for the SMAC backend."
    )
    parser.add_argument("--start", action="store_true", help="start the server")
    parser.add_argument("--stop", action="store_true", help="stop the server")
    parser.add_argument(
        "--status", action="store_true", help="show whether the server is running"
    )
    parser.add_argument(
        "--delete-db",
        action="store_true",
        dest="delete_db",
        help="stop the server (if running) and delete its database",
    )
    parser.add_argument(
        "--port", type=int, default=_DEFAULT_PORT, help="port for --start"
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """CLI entry point. No flags prints usage, then falls through to status."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.start:
        _start(args.port)
    elif args.stop:
        _stop()
    elif args.delete_db:
        _delete_db()
    elif args.status:
        _status()
    else:
        parser.print_usage()
        _status()


if __name__ == "__main__":
    main()
