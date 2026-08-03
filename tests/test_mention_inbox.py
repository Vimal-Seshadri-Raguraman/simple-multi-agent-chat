"""GET /mentions inbox: cursor pagination + idempotent ack."""

import app.database as database_module
from app.models import Mention
from tests.conftest import founder_auth, founder_headers, general_channel_id


def _agent(client, name: str) -> dict:
    """Register an agent (founder does the registering); response has handle + api_key."""
    response = client.post(
        "/members/agents",
        json={"member_name": name},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _mention(
    client, ws: str, general: str, handle: str, text: str = "please look"
) -> dict:
    """Founder posts a message mentioning `handle` in the general channel."""
    response = client.post(
        f"/workspaces/{ws}/channels/{general}/messages",
        json={"message_text": f"@{handle} {text}"},
        headers=founder_headers(client, "w1"),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_mention_lifecycle(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    general = general_channel_id(client, "w1")
    agent = _agent(client, "Bot1")
    posted = _mention(client, ws, general, agent["handle"])

    inbox = client.get("/mentions", headers={"X-API-Key": agent["api_key"]})
    assert inbox.status_code == 200
    events = inbox.json()
    assert len(events) == 1
    event = events[0]
    assert event["event"] == "mention"
    assert event["mentioned_member_id"] == agent["member_id"]
    assert "mention_id" in event
    assert "created_at" in event
    assert event["message"] == posted

    ack = client.post(
        f"/mentions/{event['mention_id']}/ack",
        headers={"X-API-Key": agent["api_key"]},
    )
    assert ack.status_code == 200
    assert ack.json() == {"status": "acknowledged"}

    empty = client.get("/mentions", headers={"X-API-Key": agent["api_key"]})
    assert empty.status_code == 200
    assert empty.json() == []

    reack = client.post(
        f"/mentions/{event['mention_id']}/ack",
        headers={"X-API-Key": agent["api_key"]},
    )
    assert reack.status_code == 200
    assert reack.json() == {"status": "acknowledged"}


def test_reack_does_not_overwrite_original_acknowledged_at(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    general = general_channel_id(client, "w1")
    agent = _agent(client, "Bot2")
    _mention(client, ws, general, agent["handle"])
    mention_id = client.get(
        "/mentions", headers={"X-API-Key": agent["api_key"]}
    ).json()[0]["mention_id"]

    client.post(f"/mentions/{mention_id}/ack", headers={"X-API-Key": agent["api_key"]})
    with database_module.SessionLocal() as db:
        first_ack = db.get(Mention, mention_id).acknowledged_at

    client.post(f"/mentions/{mention_id}/ack", headers={"X-API-Key": agent["api_key"]})
    with database_module.SessionLocal() as db:
        second_ack = db.get(Mention, mention_id).acknowledged_at

    assert first_ack is not None
    assert first_ack == second_ack


def test_ack_foreign_mention_is_uniform_404(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    general = general_channel_id(client, "w1")
    agent1 = _agent(client, "Bot3")
    agent2 = _agent(client, "Bot4")
    _mention(client, ws, general, agent1["handle"])
    mention_id = client.get(
        "/mentions", headers={"X-API-Key": agent1["api_key"]}
    ).json()[0]["mention_id"]

    response = client.post(
        f"/mentions/{mention_id}/ack", headers={"X-API-Key": agent2["api_key"]}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_ack_unknown_id_is_404(client):
    agent = _agent(client, "Bot5")
    response = client.post(
        "/mentions/does-not-exist/ack", headers={"X-API-Key": agent["api_key"]}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_cursor_pagination(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    general = general_channel_id(client, "w1")
    agent = _agent(client, "Bot6")
    for i in range(3):
        _mention(client, ws, general, agent["handle"], text=f"msg {i}")

    first_page = client.get(
        "/mentions", params={"limit": 2}, headers={"X-API-Key": agent["api_key"]}
    ).json()
    assert len(first_page) == 2

    second_page = client.get(
        "/mentions",
        params={"after": first_page[1]["mention_id"]},
        headers={"X-API-Key": agent["api_key"]},
    ).json()
    assert len(second_page) == 1
    seen_ids = {e["mention_id"] for e in first_page}
    assert second_page[0]["mention_id"] not in seen_ids


def test_cursor_with_foreign_after_is_404(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    general = general_channel_id(client, "w1")
    agent1 = _agent(client, "Bot7")
    agent2 = _agent(client, "Bot8")
    _mention(client, ws, general, agent1["handle"])
    foreign_mention_id = client.get(
        "/mentions", headers={"X-API-Key": agent1["api_key"]}
    ).json()[0]["mention_id"]

    response = client.get(
        "/mentions",
        params={"after": foreign_mention_id},
        headers={"X-API-Key": agent2["api_key"]},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_cursor_with_unknown_after_is_404(client):
    agent = _agent(client, "Bot9")
    response = client.get(
        "/mentions",
        params={"after": "does-not-exist"},
        headers={"X-API-Key": agent["api_key"]},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_acknowledged_mentions_never_reappear(client):
    ws = founder_auth(client, "w1")["workspace_id"]
    general = general_channel_id(client, "w1")
    agent = _agent(client, "Bot10")
    _mention(client, ws, general, agent["handle"], text="first")
    _mention(client, ws, general, agent["handle"], text="second")

    events = client.get("/mentions", headers={"X-API-Key": agent["api_key"]}).json()
    assert len(events) == 2

    client.post(
        f"/mentions/{events[0]['mention_id']}/ack",
        headers={"X-API-Key": agent["api_key"]},
    )
    remaining = client.get("/mentions", headers={"X-API-Key": agent["api_key"]}).json()
    assert len(remaining) == 1
    assert remaining[0]["mention_id"] == events[1]["mention_id"]
