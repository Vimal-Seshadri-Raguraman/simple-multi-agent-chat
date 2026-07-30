from datetime import datetime, timezone

from app.models import Channel, Member, Message, Workspace
from app.schemas import build_message_payload


def test_build_message_payload_matches_wire_schema():
    workspace = Workspace(workspace_id="w_1", workspace_name="Acme")
    channel = Channel(channel_id="c_1", workspace_id="w_1", channel_name="general")
    sender = Member(member_id="m_1", member_name="Alice", member_type="human")
    message = Message(
        message_id="msg_1",
        seq=1,
        channel_id="c_1",
        sender_member_id="m_1",
        message_text="hello",
        created_at=datetime(2026, 7, 30, 18, 0, 0, tzinfo=timezone.utc),
    )

    payload = build_message_payload(message, workspace, channel, sender)

    assert payload == {
        "timestamp": "2026-07-30T18:00:00+00:00",
        "workspace": {"workspace_id": "w_1", "workspace_name": "Acme"},
        "Channel": {"channel_id": "c_1", "channel_name": "general"},
        "Sender": {"member_id": "m_1", "member_name": "Alice"},
        "Message": {"message_id": "msg_1", "message_text": "hello"},
    }
