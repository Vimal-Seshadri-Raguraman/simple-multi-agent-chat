"""The product demo, in test form: mention-to-response, end to end.

Founder founds a workspace, registers an agent ("Analyst" -> handle
"analyst"), @mentions it in `general` *without ever adding it to the
channel* -- proving mention delivery is independent of channel membership
(the inbox/event fan-out is per-member, not per-channel) -- the agent reads
its inbox over X-API-Key, gets added to the channel (posting, unlike being
mentioned, does require channel membership), replies mentioning the founder
back, the founder's inbox picks up the reply-mention, both sides ack down to
an empty inbox, and a tight rate limiter proves the abuse guard trips on the
11th rapid post. This is the README's demo story
(`@analyst summarize today's numbers` -> the analyst answers) as a single
assertion chain.
"""

from app import rate_limit
from tests.conftest import founder_auth, founder_headers, general_channel_id


def test_mention_to_response_journey(client, monkeypatch):
    # --- Found the workspace ------------------------------------------------
    founder = founder_auth(client, "w1")
    ws = founder["workspace_id"]
    founder_id = founder["member_id"]
    general = general_channel_id(client, "w1")

    founder_profile = client.get(
        f"/member?id={founder_id}", headers=founder_headers(client, "w1")
    ).json()
    founder_handle = founder_profile["handle"]
    founder_name = founder_profile["member_name"]

    # --- Register the agent --------------------------------------------------
    agent = client.post(
        "/members/agents",
        json={"member_name": "Analyst"},
        headers=founder_headers(client, "w1"),
    ).json()
    agent_id = agent["member_id"]
    assert agent["handle"] == "analyst"  # slugify("Analyst")

    # --- The agent is provably NOT a member of general yet --------------------
    general_members = client.get(
        f"/workspaces/{ws}/channels/{general}/members",
        headers=founder_headers(client, "w1"),
    ).json()
    assert agent_id not in {m["member_id"] for m in general_members}

    # --- Founder posts "@analyst ..." in general -- the trigger ---------------
    posted = client.post(
        f"/workspaces/{ws}/channels/{general}/messages",
        json={"message_text": "@analyst summarize today's numbers"},
        headers=founder_headers(client, "w1"),
    )
    assert posted.status_code == 200, posted.text
    posted = posted.json()
    assert (
        posted["Message"]["message_text"] == f"<@{agent_id}> summarize today's numbers"
    )
    assert posted["mentions"] == [
        {"member_id": agent_id, "handle": "analyst", "member_name": "Analyst"}
    ]
    assert posted["Sender"]["member_id"] == founder_id

    # --- Agent reads its inbox via X-API-Key: the trigger event, undelivered
    #     to any channel the agent hasn't joined -----------------------------
    inbox = client.get("/mentions", headers={"X-API-Key": agent["api_key"]})
    assert inbox.status_code == 200
    inbox = inbox.json()
    assert len(inbox) == 1
    trigger_event = inbox[0]
    assert trigger_event["event"] == "mention"
    assert trigger_event["mentioned_member_id"] == agent_id
    assert trigger_event["message"]["Sender"]["member_id"] == founder_id
    assert f"<@{agent_id}>" in trigger_event["message"]["Message"]["message_text"]
    assert trigger_event["message"] == posted

    # --- Founder adds the agent to general so it CAN post a reply -------------
    #     (being *mentioned* needed no membership; *posting* does.)
    add_response = client.post(
        f"/workspaces/{ws}/channels/{general}/members",
        json={"member_id": agent_id},
        headers=founder_headers(client, "w1"),
    )
    assert add_response.status_code == 200, add_response.text

    # --- Agent replies, mentioning the founder back ----------------------------
    reply = client.post(
        f"/workspaces/{ws}/channels/{general}/messages",
        json={"message_text": f"@{founder_handle} done!"},
        headers={"X-API-Key": agent["api_key"]},
    )
    assert reply.status_code == 200, reply.text
    reply = reply.json()
    assert reply["Message"]["message_text"] == f"<@{founder_id}> done!"
    assert reply["mentions"] == [
        {"member_id": founder_id, "handle": founder_handle, "member_name": founder_name}
    ]
    assert reply["Sender"]["member_id"] == agent_id

    # --- Founder's inbox now has exactly the reply-mention ---------------------
    founder_inbox = client.get("/mentions", headers=founder_headers(client, "w1"))
    assert founder_inbox.status_code == 200
    founder_inbox = founder_inbox.json()
    assert len(founder_inbox) == 1
    reply_event = founder_inbox[0]
    assert reply_event["event"] == "mention"
    assert reply_event["mentioned_member_id"] == founder_id
    assert reply_event["message"]["Sender"]["member_id"] == agent_id
    assert f"<@{founder_id}>" in reply_event["message"]["Message"]["message_text"]
    assert reply_event["message"] == reply

    # --- Both sides ack --------------------------------------------------------
    agent_ack = client.post(
        f"/mentions/{trigger_event['mention_id']}/ack",
        headers={"X-API-Key": agent["api_key"]},
    )
    assert agent_ack.status_code == 200
    assert agent_ack.json() == {"status": "acknowledged"}

    founder_ack = client.post(
        f"/mentions/{reply_event['mention_id']}/ack",
        headers=founder_headers(client, "w1"),
    )
    assert founder_ack.status_code == 200
    assert founder_ack.json() == {"status": "acknowledged"}

    # --- Both inboxes are now empty ---------------------------------------------
    assert client.get("/mentions", headers={"X-API-Key": agent["api_key"]}).json() == []
    assert client.get("/mentions", headers=founder_headers(client, "w1")).json() == []

    # --- Rate-limit smoke: a tight limiter trips on the 11th rapid post ---------
    small_limiter = rate_limit.SlidingWindowRateLimiter(
        max_events=10, window_seconds=60
    )
    monkeypatch.setattr(rate_limit, "post_limiter", small_limiter)

    for i in range(10):
        response = client.post(
            f"/workspaces/{ws}/channels/{general}/messages",
            json={"message_text": f"rapid post {i}"},
            headers=founder_headers(client, "w1"),
        )
        assert response.status_code == 200, response.text

    eleventh = client.post(
        f"/workspaces/{ws}/channels/{general}/messages",
        json={"message_text": "rapid post 10"},
        headers=founder_headers(client, "w1"),
    )
    assert eleventh.status_code == 429
    assert eleventh.json() == {
        "error": {
            "code": "rate_limited",
            "message": "Posting too fast — wait a moment",
        }
    }
