"""The mention inbox: GET /mentions (cursor-paginated) + POST /mentions/{id}/ack."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.auth import get_current_member
from app.database import get_db
from app.errors import NotFoundError
from app.mentions import build_mention_event
from app.models import Member, Mention, utcnow

router = APIRouter()

DEFAULT_LIMIT = 20
MAX_LIMIT = 50


@router.get("/mentions")
def list_mentions(
    after: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[dict]:
    """The caller's own pending (unacknowledged) mentions, oldest-first.

    `after` is a cursor: the mention_id of the last event the caller has
    already seen. It must be one of the caller's own mentions (acknowledged
    or not) -- any other id, including a real mention belonging to someone
    else, is indistinguishable from unknown and raises the uniform 404.
    Pagination is strictly-greater on the (created_at, mention_id) tuple,
    matching the listing order, so same-timestamp ties never repeat or
    skip a row across pages.
    """
    query = db.query(Mention).filter(
        Mention.mentioned_member_id == member.member_id,
        Mention.acknowledged_at.is_(None),
    )
    if after is not None:
        anchor = db.get(Mention, after)
        if anchor is None or anchor.mentioned_member_id != member.member_id:
            raise NotFoundError(f"Mention '{after}' not found")
        query = query.filter(
            or_(
                Mention.created_at > anchor.created_at,
                and_(
                    Mention.created_at == anchor.created_at,
                    Mention.mention_id > anchor.mention_id,
                ),
            )
        )

    mentions = (
        query.order_by(Mention.created_at.asc(), Mention.mention_id.asc())
        .limit(limit)
        .all()
    )
    return [build_mention_event(db, mention) for mention in mentions]


@router.post("/mentions/{mention_id}/ack")
def ack_mention(
    mention_id: str,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Acknowledge a mention: sets acknowledged_at once, idempotently.

    Foreign or unknown mention_id -> the uniform 404. Re-acking an already
    acknowledged mention still returns 200 without touching the original
    acknowledged_at.
    """
    mention = db.get(Mention, mention_id)
    if mention is None or mention.mentioned_member_id != member.member_id:
        raise NotFoundError(f"Mention '{mention_id}' not found")
    if mention.acknowledged_at is None:
        mention.acknowledged_at = utcnow()
        db.add(mention)
        db.commit()
    return {"status": "acknowledged"}
