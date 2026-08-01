from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import generate_api_key, get_current_member, hash_api_key
from app.authorization import authorize_management_action
from app.database import get_db
from app.errors import NotFoundError
from app.models import Member
from app.schemas import (
    MemberOut,
    MemberProfileUpdate,
    MemberRegisterIn,
    MemberRegisterOut,
    MemberSelfOut,
)

router = APIRouter()


def _register(db: Session, member_name: str, member_type: str) -> MemberRegisterOut:
    raw_key = generate_api_key()
    member = Member(
        member_name=member_name,
        member_type=member_type,
        api_key_hash=hash_api_key(raw_key),
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return MemberRegisterOut(
        member_id=member.member_id,
        member_name=member.member_name,
        member_type=member.member_type,
        api_key=raw_key,
    )


@router.post("/members/agents", response_model=MemberRegisterOut)
def register_agent(
    body: MemberRegisterIn,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MemberRegisterOut:
    authorize_management_action(member)
    return _register(db, body.member_name, "agent")


@router.post("/members/bots", response_model=MemberRegisterOut)
def register_bot(
    body: MemberRegisterIn,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MemberRegisterOut:
    authorize_management_action(member)
    return _register(db, body.member_name, "bot_app")


@router.get("/members", response_model=list[MemberOut])
def search_members(
    search_name: str | None = Query(default=None),
    search_id: str | None = Query(default=None),
    search_type: str | None = Query(default=None),
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[Member]:
    query = db.query(Member)
    if search_name:
        query = query.filter(Member.member_name.contains(search_name))
    if search_id:
        query = query.filter(Member.member_id == search_id)
    if search_type:
        query = query.filter(Member.member_type == search_type)
    return query.all()


@router.get("/member", response_model=MemberSelfOut)
def get_member(
    member_id: str = Query(alias="id"),
    current_member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MemberSelfOut:
    """A member's profile. Email is included only when fetching your own."""
    member = db.get(Member, member_id)
    if member is None:
        raise NotFoundError(f"Member '{member_id}' not found")
    profile = MemberSelfOut.model_validate(member)
    if member.member_id != current_member.member_id:
        profile.email = None
    return profile


@router.patch("/members/me", response_model=MemberSelfOut)
def update_my_profile(
    body: MemberProfileUpdate,
    current_member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MemberSelfOut:
    """Update the caller's own profile fields (humans only)."""
    authorize_management_action(current_member)
    updates = body.model_dump(exclude_unset=True)
    if "display_name" in updates:
        current_member.member_name = updates.pop("display_name")
    for field, value in updates.items():
        setattr(current_member, field, value)
    db.add(current_member)
    db.commit()
    db.refresh(current_member)
    return MemberSelfOut.model_validate(current_member)
