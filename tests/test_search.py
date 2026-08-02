"""Unauthenticated public-workspace directory."""

from tests.conftest import founder_auth


def test_search_is_unauthenticated_and_public_only(client):
    founder_auth(client, "pub")  # public test workspace
    founder_auth(client, "sec", visibility="private")
    r = client.get("/workspaces/search")  # no auth header at all
    assert r.status_code == 200
    names = [w["workspace_name"] for w in r.json()]
    assert "pub-workspace" in names
    assert "sec-workspace" not in names


def test_search_substring_case_insensitive(client):
    founder_auth(client, "pub")
    r = client.get("/workspaces/search", params={"name": "PUB-work"})
    assert [w["workspace_name"] for w in r.json()] == ["pub-workspace"]
    assert client.get("/workspaces/search", params={"name": "zzz"}).json() == []


def test_search_result_shape(client):
    founder_auth(client, "pub")
    row = client.get("/workspaces/search").json()[0]
    assert set(row.keys()) == {"workspace_id", "workspace_name", "visibility"}
