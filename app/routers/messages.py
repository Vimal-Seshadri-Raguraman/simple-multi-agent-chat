from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.authorization import (
    authorize_channel_read,
    authorize_post_message,
    require_same_workspace,
)
from app.database import get_db
from app.errors import NotFoundError
from app.mentions import build_mention_event, canonicalize
from app.models import Channel, Member, Mention, Message, Workspace
from app.schemas import MessageCreate, build_message_payload
from app.ws_manager import event_manager, manager

router = APIRouter()

DEFAULT_LIMIT = 5
MAX_LIMIT = 15


def _get_workspace_and_channel(
    db: Session, workspace_id: str, channel_id: str
) -> tuple[Workspace, Channel]:
    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        raise NotFoundError(f"Workspace '{workspace_id}' not found")
    channel = (
        db.query(Channel)
        .filter(Channel.channel_id == channel_id, Channel.workspace_id == workspace_id)
        .first()
    )
    if channel is None:
        raise NotFoundError(
            f"Channel '{channel_id}' not found in workspace '{workspace_id}'"
        )
    return workspace, channel


def _next_seq(db: Session, channel_id: str) -> int:
    current_max = (
        db.query(Message.seq)
        .filter(Message.channel_id == channel_id)
        .order_by(Message.seq.desc())
        .first()
    )
    return (current_max[0] + 1) if current_max else 1


@router.post("/workspaces/{workspace_id}/channels/{channel_id}/messages")
async def post_message(
    workspace_id: str,
    channel_id: str,
    body: MessageCreate,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> dict:
    require_same_workspace(member, workspace_id)
    workspace, channel = _get_workspace_and_channel(db, workspace_id, channel_id)
    authorize_post_message(db, member, channel_id)

    canonical_text, mentioned = canonicalize(
        db, workspace_id, member.member_id, body.message_text
    )
    message = Message(
        channel_id=channel_id,
        sender_member_id=member.member_id,
        message_text=canonical_text,
        seq=_next_seq(db, channel_id),
    )
    db.add(message)
    db.flush()  # assigns message.message_id for the Mention rows below
    mention_rows = [
        Mention(
            message_id=message.message_id,
            mentioned_member_id=mentioned_member.member_id,
        )
        for mentioned_member in mentioned
    ]
    for mention_row in mention_rows:
        db.add(mention_row)
    db.commit()
    db.refresh(message)
    for mention_row in mention_rows:
        db.refresh(mention_row)  # populates mention_id/created_at post-commit

    payload = build_message_payload(message, workspace, channel, member, db)
    # Broadcast to the channel first, then fan out mention events -- both
    # happen strictly after the commit above, never before, so a dead
    # socket or a slow event push can never roll back a persisted message.
    await manager.broadcast(channel_id, payload)
    for mention_row in mention_rows:
        await event_manager.send_to_member(
            mention_row.mentioned_member_id, build_mention_event(db, mention_row)
        )
    return payload


@router.get("/workspaces/{workspace_id}/channels/{channel_id}/messages")
def get_messages(
    workspace_id: str,
    channel_id: str,
    after: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1),
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[dict]:
    require_same_workspace(member, workspace_id)
    workspace, channel = _get_workspace_and_channel(db, workspace_id, channel_id)
    authorize_channel_read(db, member, channel_id)
    limit = min(limit, MAX_LIMIT)

    query = db.query(Message).filter(Message.channel_id == channel_id)
    if after:
        anchor = db.get(Message, after)
        if anchor is None or anchor.channel_id != channel_id:
            raise NotFoundError(
                f"Message '{after}' not found in channel '{channel_id}'"
            )
        query = query.filter(Message.seq > anchor.seq)

    messages = query.order_by(Message.seq.asc()).limit(limit).all()
    sender_ids = {m.sender_member_id for m in messages}
    senders = {
        s.member_id: s
        for s in db.query(Member).filter(Member.member_id.in_(sender_ids)).all()
    }
    return [
        build_message_payload(m, workspace, channel, senders[m.sender_member_id], db)
        for m in messages
    ]
