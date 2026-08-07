"""Shared fixtures for analyst_agent's test suite."""

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from analyst_agent.config import Config

#: This checkout's repo root -- three parents up from this file
#: (examples/analyst_agent/tests/conftest.py -> examples/analyst_agent ->
#: examples -> repo root). `smac_cli.server` needs to run with this as its
#: cwd (see `_find_repo_root` in `smac_cli/server.py`).
_REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def cfg() -> Config:
    """A `Config` matching `test_config.py`'s `BASE` fixture dict: a
    fresh agent with an unredeemed invite code and no saved credentials
    yet."""
    return Config(
        smac_url="http://smac.test",
        agent_name="Analyst",
        agent_code="abc123",
        anthropic_api_key="sk-ant-test",
        model="claude-sonnet-5",
        max_replies_per_min=6,
        max_hops=3,
    )


def _free_port() -> int:
    """Grab an ephemeral port from the OS so parallel test runs don't collide."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def real_smac_server(tmp_path_factory: pytest.TempPathFactory):
    """A real `smac-server` (real uvicorn, real migrations) for Task 6's
    integration journey, spawned once per test module.

    Follows `tests/conftest.py`'s own `real_smac_server` fixture exactly
    (same `smac_cli.server` lifecycle manager, same free-port/readiness/
    exact-PID-teardown mechanics) rather than inventing a second way to
    spawn a server -- reimplemented locally here, not imported from
    `tests/conftest.py`, so this example's test suite stays self-contained
    and importable on its own (`examples/` has no `__init__.py`, so
    `tests.conftest` at the repo root and `analyst_agent.tests.conftest`
    here are two unrelated packages).

    `smac_cli.server --start` pins `DATABASE_URL` to a fresh sqlite file
    under the tmp `$HOME` this fixture hands it (see `smac_cli/paths.py`),
    waits for `/meta` to answer before returning, and `--stop` signals the
    exact pid recorded in its pidfile (SIGTERM, escalating to SIGKILL) --
    never a name-pattern kill. Yields the server's base URL.
    """
    home_dir = tmp_path_factory.mktemp("analyst-agent-smac-home")
    port = _free_port()
    env = {**os.environ, "HOME": str(home_dir)}

    start = subprocess.run(
        [sys.executable, "-m", "smac_cli.server", "--start", "--port", str(port)],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert start.returncode == 0, start.stdout + start.stderr

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        subprocess.run(
            [sys.executable, "-m", "smac_cli.server", "--stop"],
            cwd=str(_REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )


def pytest_configure(config: pytest.Config) -> None:
    """Register the `live` marker (Task 6's real-Anthropic integration
    test) without touching the server's pyproject.toml -- see
    `test_integration.py`'s module docstring. A conftest.py under
    examples/ is picked up regardless of which subtree pytest is
    invoked against (whole-repo `pytest -q`, or the scoped
    `pytest examples/ -q` gate), unlike a pytest.ini in this directory,
    which pytest's config-file search never descends into from a
    repo-root invocation."""
    config.addinivalue_line(
        "markers",
        "live: exercises the real Anthropic API (requires ANTHROPIC_API_KEY); "
        "skipped by default -- see README.md's quickstart.",
    )
