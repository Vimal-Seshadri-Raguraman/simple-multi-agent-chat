"""create_agent / run_interactive: the CLI setup helper for new agent members."""

import asyncio

import httpx
import pytest

from smac_mcp.api import SmacApiError
from smac_mcp.create_agent import create_agent, run_interactive
from tests.conftest import founder_auth


def _transport() -> httpx.ASGITransport:
    from app.main import app

    return httpx.ASGITransport(app=app)


def test_create_agent_success(client):
    founder = founder_auth(client, "w1")
    result = asyncio.run(
        create_agent(
            base_url="http://testserver",
            workspace_id=founder["workspace_id"],
            email="w1@test.example",
            password="test-password-123",
            agent_name="Fin Analyst",
            transport=_transport(),
        )
    )
    assert result["handle"] == "fin-analyst"
    assert result["member_name"] == "Fin Analyst"
    assert result["member_type"] == "agent"
    assert "api_key" in result and result["api_key"]


def test_create_agent_wrong_password_surfaces_login_message(client):
    founder = founder_auth(client, "w1")
    with pytest.raises(SmacApiError) as excinfo:
        asyncio.run(
            create_agent(
                base_url="http://testserver",
                workspace_id=founder["workspace_id"],
                email="w1@test.example",
                password="totally-wrong-password",
                agent_name="Fin Analyst",
                transport=_transport(),
            )
        )
    assert str(excinfo.value) == "Invalid email or password"


def test_run_interactive_prints_one_time_key_warning(monkeypatch, capsys):
    inputs = iter(["ws-123", "founder@test.example", "Fin Analyst"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    monkeypatch.setattr("getpass.getpass", lambda _prompt: "s3cret")

    captured_kwargs = {}

    async def fake_create_agent(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "member_id": "m1",
            "member_name": "Fin Analyst",
            "member_type": "agent",
            "handle": "fin-analyst",
            "api_key": "sk-onetime-abc123",
        }

    monkeypatch.setattr("smac_mcp.create_agent.create_agent", fake_create_agent)

    run_interactive()

    out = capsys.readouterr().out
    assert "sk-onetime-abc123" in out
    assert "shown exactly once" in out
    assert "fin-analyst" in out
    assert captured_kwargs["workspace_id"] == "ws-123"
    assert captured_kwargs["email"] == "founder@test.example"
    assert captured_kwargs["password"] == "s3cret"
    assert captured_kwargs["agent_name"] == "Fin Analyst"


def test_run_interactive_prints_login_error_and_exits(monkeypatch, capsys):
    inputs = iter(["ws-123", "founder@test.example", "Fin Analyst"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
    monkeypatch.setattr("getpass.getpass", lambda _prompt: "wrong")

    async def fake_create_agent(**kwargs):
        raise SmacApiError("Invalid email or password")

    monkeypatch.setattr("smac_mcp.create_agent.create_agent", fake_create_agent)

    with pytest.raises(SystemExit) as excinfo:
        run_interactive()

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "Invalid email or password" in err
