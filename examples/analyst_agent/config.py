"""Configuration for the analyst_agent example.

`load_config` reads a plain `Mapping[str, str]` -- never `os.environ`
directly -- so every test here (and every caller that wants to layer a
`.env` file, `main.py`'s job, not this module's) controls its input
without touching the process environment. `Config` itself is a frozen
dataclass: once built, an agent's identity/tuning knobs for a run don't
change underneath it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

#: Local dev default: `smac-server --start`'s own default port
#: (`smac_cli/server.py::_DEFAULT_PORT`). These two must agree, or the
#: quickstart fails on a connection refused nobody can explain.
DEFAULT_SMAC_URL = "http://127.0.0.1:8000"
DEFAULT_AGENT_NAME = "Analyst"
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_REPLIES_PER_MIN = 6
DEFAULT_MAX_HOPS = 3


class ConfigError(Exception):
    """Raised by `load_config` for anything that can never work.

    Every message names the offending variable and points at
    `.env.example` -- the fix is always "copy the example, fill in one
    value", never a traceback to decipher.
    """


@dataclass(frozen=True)
class Config:
    smac_url: str
    agent_name: str
    agent_code: str | None
    anthropic_api_key: str
    model: str
    max_replies_per_min: int
    max_hops: int


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    """Parse `env[key]` as a positive int, or fall back to `default` if
    the variable is absent/empty. Anything present but not a positive
    integer is a `ConfigError` naming both the variable and the bad
    value, never a bare `ValueError`."""
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or value <= 0:
        raise ConfigError(
            f"{key} must be a positive integer, got {raw!r} -- check your .env "
            "(copy examples/analyst_agent/.env.example if you don't have one)"
        )
    return value


def load_config(env: Mapping[str, str]) -> Config:
    """Build a `Config` from `env` (an already-merged `.env` + process
    environment, or a plain test dict).

    Only `ANTHROPIC_API_KEY` is unconditionally required here.
    `SMAC_AGENT_CODE` (-> `agent_code`) is deliberately optional: whether
    it's *actually* needed depends on whether credentials are already
    saved on disk from a previous join, a decision that belongs to
    `SmacLink.join_or_load`, not to config loading -- a config that omits
    it is still valid, just not yet know-it-will-work.
    """
    anthropic_api_key = env.get("ANTHROPIC_API_KEY")
    if not anthropic_api_key:
        raise ConfigError(
            "ANTHROPIC_API_KEY is not set -- copy "
            "examples/analyst_agent/.env.example to examples/analyst_agent/.env "
            "and fill in your key"
        )

    smac_url = (env.get("SMAC_URL") or DEFAULT_SMAC_URL).rstrip("/")
    agent_name = env.get("AGENT_NAME") or DEFAULT_AGENT_NAME
    agent_code = env.get("SMAC_AGENT_CODE") or None
    model = env.get("MODEL") or DEFAULT_MODEL

    max_replies_per_min = _positive_int(
        env, "MAX_REPLIES_PER_MIN", DEFAULT_MAX_REPLIES_PER_MIN
    )
    max_hops = _positive_int(env, "MAX_HOPS", DEFAULT_MAX_HOPS)

    return Config(
        smac_url=smac_url,
        agent_name=agent_name,
        agent_code=agent_code,
        anthropic_api_key=anthropic_api_key,
        model=model,
        max_replies_per_min=max_replies_per_min,
        max_hops=max_hops,
    )
