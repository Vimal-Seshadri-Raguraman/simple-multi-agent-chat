"""SmacApi: auth header, error mapping, lazy workspace cache."""

import asyncio

import httpx

from smac_mcp.api import SmacApi, SmacApiError
from tests.conftest import founder_auth, founder_headers


def _api_for(client, key="w1", api_key=None):
    """A SmacApi wired to the in-process app via ASGITransport.

    When api_key is None, create an agent in workspace `key` and use its key.
    """
    from app.main import app

    if api_key is None:
        created = client.post(
            "/members/agents",
            json={"member_name": "Bridge Bot"},
            headers=founder_headers(client, key),
        ).json()
        api_key = created["api_key"]
    return SmacApi(
        base_url="http://testserver",
        api_key=api_key,
        transport=httpx.ASGITransport(app=app),
    )


def test_request_success_and_auth_header(client):
    founder_auth(client, "w1")
    api = _api_for(client)
    me = asyncio.run(api.me())
    assert me["member_name"] == "Bridge Bot"


def test_workspace_id_lazy_and_cached(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    api = _api_for(client)
    assert asyncio.run(api.workspace_id()) == ws
    assert api._workspace_id == ws  # cached; second call must not re-fetch


def test_bad_key_maps_to_friendly_401(client):
    founder_auth(client, "w1")
    api = _api_for(client, api_key="not-a-real-key")
    try:
        asyncio.run(api.me())
        raise AssertionError("expected SmacApiError")
    except SmacApiError as e:
        assert "API key" in str(e)


def test_error_envelope_message_passthrough(client):
    founder_auth(client, "w1")
    api = _api_for(client)
    try:
        asyncio.run(api.request("GET", "/workspaces/nonexistent-id/unreads"))
        raise AssertionError("expected SmacApiError")
    except SmacApiError as e:
        assert "not found" in str(e).lower()


def test_unreachable_server_message():
    api = SmacApi(base_url="http://127.0.0.1:59999", api_key="k")
    try:
        asyncio.run(api.me())
        raise AssertionError("expected SmacApiError")
    except SmacApiError as e:
        assert "not reachable" in str(e)
        assert "127.0.0.1:59999" in str(e)
