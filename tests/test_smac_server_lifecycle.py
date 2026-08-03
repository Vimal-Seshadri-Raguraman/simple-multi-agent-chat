"""`smac-server` lifecycle: real subprocess tests against a tmp $HOME.

Slow (each `--start` boots a real uvicorn + runs migrations), so server
starts are kept to a minimum: one start/stop cycle is reused across the
"happy path" assertions (already-running refusal, status, stop,
stop-again), and `--delete-db`'s "stops first" case gets its own cycle.
Everything else (stale pidfile, delete-db confirmation wording, the
repo-checkout preflight) is exercised without spawning a server at all.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from smac_cli import server as smac_server
from smac_cli.paths import db_path, pidfile_path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    """Grab an ephemeral port from the OS so parallel test runs don't collide."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(
    home: Path, *args: str, input_text: str | None = None
) -> subprocess.CompletedProcess:
    """Invoke `python -m smac_cli.server <args>` against a tmp $HOME.

    Module invocation (not the installed console script) per the task
    brief, so this test doesn't require `pip install -e .` to have run.
    """
    return subprocess.run(
        [sys.executable, "-m", "smac_cli.server", *args],
        cwd=str(_REPO_ROOT),
        env={**os.environ, "HOME": str(home)},
        input=input_text,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    """A tmp $HOME, isolating pidfile/db/log/config from the real machine."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    return home_dir


def test_full_start_stop_lifecycle(home: Path) -> None:
    port = _free_port()

    # --- --start ---
    result = _run(home, "--start", "--port", str(port))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "started" in result.stdout

    pidfile = home / ".config" / "smac" / "server.pid"
    assert pidfile.exists()
    info = json.loads(pidfile.read_text())
    assert info["port"] == port
    original_pid = info["pid"]
    assert isinstance(original_pid, int)

    db_file = home / ".local" / "share" / "smac" / "smac.db"
    assert db_file.exists()

    docs_response = httpx.get(f"http://127.0.0.1:{port}/docs", timeout=5.0)
    assert docs_response.status_code == 200

    try:
        # --- second --start refuses, pid unchanged ---
        result = _run(home, "--start", "--port", str(port))
        assert result.returncode == 0
        assert "already running" in result.stdout
        assert str(original_pid) in result.stdout
        assert json.loads(pidfile.read_text())["pid"] == original_pid

        # --- --status ---
        result = _run(home, "--status")
        assert result.returncode == 0
        assert str(original_pid) in result.stdout
        assert str(port) in result.stdout
        assert str(db_file) in result.stdout
    finally:
        # --- --stop ---
        result = _run(home, "--stop")
        assert result.returncode == 0
        assert "stopped" in result.stdout
        assert not pidfile.exists()

    with pytest.raises(OSError):
        os.kill(original_pid, 0)

    # --- --stop again: not running ---
    result = _run(home, "--stop")
    assert result.returncode == 0
    assert "not running" in result.stdout


def test_stale_pidfile_is_treated_as_not_running_and_cleaned(home: Path) -> None:
    config_dir = home / ".config" / "smac"
    config_dir.mkdir(parents=True)
    pidfile = config_dir / "server.pid"

    # A pid essentially guaranteed to be dead: spawn a trivial subprocess
    # and wait for it to exit, then reuse its now-free pid.
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_pid = dead.pid
    dead.wait()
    pidfile.write_text(
        json.dumps(
            {"pid": dead_pid, "port": 9999, "db_path": "/nonexistent", "started_at": 0}
        )
    )

    result = _run(home, "--status")
    assert result.returncode == 0
    assert "not running" in result.stdout
    assert not pidfile.exists()


def test_no_flags_prints_usage_and_status(home: Path) -> None:
    result = _run(home)
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
    assert "not running" in result.stdout


def test_delete_db_stops_running_server_then_deletes_on_confirmation(
    home: Path,
) -> None:
    port = _free_port()
    result = _run(home, "--start", "--port", str(port))
    assert result.returncode == 0, result.stdout + result.stderr

    db_file = home / ".local" / "share" / "smac" / "smac.db"
    assert db_file.exists()
    pidfile = home / ".config" / "smac" / "server.pid"
    assert pidfile.exists()

    result = _run(home, "--delete-db", input_text="delete\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "stopping" in result.stdout
    assert "deleted" in result.stdout
    assert not db_file.exists()
    assert not pidfile.exists()


def test_delete_db_aborts_on_wrong_confirmation(home: Path) -> None:
    data_dir = home / ".local" / "share" / "smac"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "smac.db"
    db_file.write_text("not actually a database, just needs to exist")

    result = _run(home, "--delete-db", input_text="nope\n")
    assert result.returncode == 0
    assert "aborted" in result.stdout
    assert db_file.exists()
    assert db_file.read_text() == "not actually a database, just needs to exist"


def test_delete_db_aborts_on_closed_stdin(home: Path) -> None:
    data_dir = home / ".local" / "share" / "smac"
    data_dir.mkdir(parents=True)
    db_file = data_dir / "smac.db"
    db_file.write_text("not actually a database, just needs to exist")

    result = subprocess.run(
        [sys.executable, "-m", "smac_cli.server", "--delete-db"],
        cwd=str(_REPO_ROOT),
        env={**os.environ, "HOME": str(home)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "aborted" in result.stdout
    assert db_file.exists()


def test_delete_db_with_no_database_file(home: Path) -> None:
    result = _run(home, "--delete-db", input_text="delete\n")
    assert result.returncode == 0
    assert "no database file" in result.stdout


class TestRepoRootResolution:
    """`_repo_root_for` (the packaging fallback preflight) as a pure function."""

    def test_resolves_when_alembic_ini_and_dir_present(self, tmp_path: Path) -> None:
        (tmp_path / "alembic.ini").touch()
        (tmp_path / "alembic").mkdir()
        pkg_dir = tmp_path / "app"
        pkg_dir.mkdir()
        fake_init = pkg_dir / "__init__.py"
        fake_init.touch()

        assert smac_server._repo_root_for(fake_init) == tmp_path

    def test_none_when_alembic_ini_missing(self, tmp_path: Path) -> None:
        (tmp_path / "alembic").mkdir()
        pkg_dir = tmp_path / "app"
        pkg_dir.mkdir()
        fake_init = pkg_dir / "__init__.py"
        fake_init.touch()

        assert smac_server._repo_root_for(fake_init) is None

    def test_none_when_alembic_dir_missing(self, tmp_path: Path) -> None:
        (tmp_path / "alembic.ini").touch()
        pkg_dir = tmp_path / "app"
        pkg_dir.mkdir()
        fake_init = pkg_dir / "__init__.py"
        fake_init.touch()

        assert smac_server._repo_root_for(fake_init) is None


def test_start_reports_documented_error_when_repo_root_unresolvable(
    monkeypatch: pytest.MonkeyPatch, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The packaging fallback: `--start` must fail fast with the exact
    spec-pinned message when the Alembic config isn't resolvable, instead
    of spawning uvicorn and letting it crash on `init_db()`."""
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(smac_server, "_find_repo_root", lambda: None)

    with pytest.raises(SystemExit) as exc_info:
        smac_server.main(["--start"])

    assert exc_info.value.code == 1
    assert smac_server.REPO_CHECKOUT_ERROR in capsys.readouterr().out
