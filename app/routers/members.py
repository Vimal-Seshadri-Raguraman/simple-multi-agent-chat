from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.accounts import build_member_self_out, create_agent_account
from app.auth import generate_api_key, get_current_member, hash_api_key
from app.authorization import authorize_management_action
from app.database import get_db
from app.errors import HandleTakenError, NotFoundError
from app.handles import generate_unique_handle
from app.models import Member
from app.schemas import (
    MemberOut,
    MemberProfileUpdate,
    MemberRegisterIn,
    MemberRegisterOut,
    MemberSelfOut,
)

router = APIRouter()


def _register(
    db: Session, member_name: str, member_type: str, workspace_id: str
) -> MemberRegisterOut:
    raw_key = generate_api_key()
    # Identity v2 (SMAC-79 Task 1) dual-write: every agent/bot member gets
    # its own brand-new, identity-only global Account (no email/password;
    # the API key stays right here on Member, per-workspace, unchanged).
    account = create_agent_account(db, member_type)
    member = Member(
        member_name=member_name,
        member_type=member_type,
        handle=generate_unique_handle(db, workspace_id, member_name),
        api_key_hash=hash_api_key(raw_key),
        workspace_id=workspace_id,
        account_id=account.account_id,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return MemberRegisterOut(
        member_id=member.member_id,
        member_name=member.member_name,
        member_type=member.member_type,
        handle=member.handle,
        api_key=raw_key,
    )


@router.post("/members/agents", response_model=MemberRegisterOut)
def register_agent(
    body: MemberRegisterIn,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MemberRegisterOut:
    authorize_management_action(member)
    return _register(db, body.member_name, "agent", member.workspace_id)


@router.post("/members/bots", response_model=MemberRegisterOut)
def register_bot(
    body: MemberRegisterIn,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MemberRegisterOut:
    authorize_management_action(member)
    return _register(db, body.member_name, "bot_app", member.workspace_id)


@router.get("/members", response_model=list[MemberOut])
def search_members(
    search_name: str | None = Query(default=None),
    search_id: str | None = Query(default=None),
    search_type: str | None = Query(default=None),
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[Member]:
    """Search members within the caller's own workspace (the wall applies implicitly)."""
    query = db.query(Member).filter(Member.workspace_id == member.workspace_id)
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
    """A member's profile within the caller's own workspace.

    A member in a foreign workspace simply doesn't exist for you -> 404,
    via the same not-found path as an unknown member_id. `email`,
    `is_admin`, and `workspace_visibility` are all included only when
    fetching your own profile -- nulled out for anyone else's (SMAC-72
    task 6's `is_admin`/`workspace_visibility` addition is scoped to the
    caller's own `/whoami` view, same as `email` already was).
    """
    member = (
        db.query(Member)
        .filter(
            Member.member_id == member_id,
            Member.workspace_id == current_member.workspace_id,
        )
        .first()
    )
    if member is None:
        raise NotFoundError(f"Member '{member_id}' not found")
    profile = build_member_self_out(db, member)
    if member.member_id != current_member.member_id:
        profile.email = None
        profile.is_admin = None
        profile.workspace_visibility = None
    return profile


@router.get("/members/me", response_model=MemberSelfOut)
def get_my_profile(
    current_member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MemberSelfOut:
    """The caller's own full profile, including workspace_id and email.

    Works under either credential (Bearer JWT for humans, X-API-Key for
    agents/bot_apps) — this is how an API-key-only client discovers its own
    identity and workspace without already knowing its member_id.
    """
    return build_member_self_out(db, current_member)


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
    if "handle" in updates:
        new_handle = updates.pop("handle")
        taken = (
            db.query(Member)
            .filter(
                Member.workspace_id == current_member.workspace_id,
                Member.handle == new_handle,
                Member.member_id != current_member.member_id,
            )
            .first()
        )
        if taken is not None:
            raise HandleTakenError(
                f"Handle '{new_handle}' is already in use in this workspace"
            )
        current_member.handle = new_handle
    for field, value in updates.items():
        setattr(current_member, field, value)
    db.add(current_member)
    db.commit()
    db.refresh(current_member)
    return build_member_self_out(db, current_member)
