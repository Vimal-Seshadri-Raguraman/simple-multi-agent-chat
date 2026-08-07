"""Shared fixtures for analyst_agent's test suite."""

import pytest

from analyst_agent.config import Config


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
