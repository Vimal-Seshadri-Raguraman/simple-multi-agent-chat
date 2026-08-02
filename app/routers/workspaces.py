from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.accounts import create_member_account
from app.auth import get_current_member
from app.authorization import require_same_workspace, require_workspace_admin
from app.database import get_db
from app.errors import (
    ConfirmationRequiredError,
    ForbiddenMemberTypeError,
    LastAdminError,
    NotFoundError,
)
from app.models import (
    Channel,
    ChannelMember,
    Member,
    Mention,
    Message,
    RefreshToken,
    Workspace,
    WorkspaceInvite,
    WorkspaceRecord,
    new_id,
    utcnow,
)
from app.routers.auth import _issue_token_pair
from app.schemas import (
    FoundWorkspaceIn,
    InviteOut,
    MemberAdminIn,
    MemberOut,
    MemberSelfOut,
    WorkspaceAuthOut,
    WorkspaceOut,
    WorkspaceSearchOut,
    WorkspaceVisibilityIn,
    build_message_payload,
)

_DELETE_CONFIRMATION = "delete"

router = APIRouter()


@router.post("/workspaces", response_model=WorkspaceAuthOut)
def found_workspace(
    body: FoundWorkspaceIn, db: Session = Depends(get_db)
) -> WorkspaceAuthOut:
    """Found a workspace: workspace + 'general' + admin account + audit record, atomically."""
    workspace = Workspace(
        workspace_id=new_id(),
        workspace_name=body.workspace_name,
        visibility=body.visibility,
    )
    db.add(workspace)
    db.flush()
    general = Channel(
        channel_id=new_id(),
        workspace_id=workspace.workspace_id,
        channel_name="general",
    )
    db.add(general)
    db.flush()
    workspace.default_channel_id = general.channel_id
    founder = create_member_account(
        db,
        workspace,
        email=body.email,
        password=body.password,
        first_name=body.first_name,
        last_name=body.last_name,
        display_name=body.display_name,
        company=body.company,
        occupation=body.occupation,
        job_role=body.job_role,
        is_admin=True,
    )
    db.add(
        WorkspaceRecord(
            workspace_id=workspace.workspace_id,
            workspace_name=workspace.workspace_name,
            created_by=founder.member_id,
        )
    )
    db.commit()
    db.refresh(founder)
    tokens = _issue_token_pair(db, founder)
    return WorkspaceAuthOut(
        member=MemberSelfOut.model_validate(founder),
        workspace=WorkspaceOut.model_validate(workspace),
        **tokens.model_dump(),
    )


# GET /workspaces/search MUST be defined before any /workspaces/{workspace_id} GET route
# so FastAPI's router does not interpret "search" as a workspace_id path parameter.
@router.get("/workspaces/search", response_model=list[WorkspaceSearchOut])
def search_public_workspaces(
    name: str | None = None, db: Session = Depends(get_db)
) -> list[Workspace]:
    """Search public workspaces by name (case-insensitive substring match).

    Unauthenticated endpoint: no auth dependency, public visibility only.
    If name is absent or empty, returns all public workspaces.
    """
    query = db.query(Workspace).filter(Workspace.visibility == "public")
    if name:
        query = query.filter(Workspace.workspace_name.ilike(f"%{name}%"))
    return query.all()


@router.get("/workspaces/{workspace_id}/members", response_model=list[MemberOut])
def list_workspace_members(
    workspace_id: str,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[Member]:
    """Everyone in the caller's own workspace (the wall blocks all others)."""
    require_same_workspace(member, workspace_id)
    return db.query(Member).filter(Member.workspace_id == workspace_id).all()


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def update_workspace_visibility(
    workspace_id: str,
    body: WorkspaceVisibilityIn,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Workspace:
    """Flip a workspace's visibility. Admin-only, wall-gated."""
    require_workspace_admin(member, workspace_id)
    workspace = db.query(Workspace).filter(Workspace.workspace_id == workspace_id).one()
    workspace.visibility = body.visibility
    db.commit()
    db.refresh(workspace)
    return workspace


@router.patch(
    "/workspaces/{workspace_id}/members/{member_id}", response_model=MemberOut
)
def update_member_admin(
    workspace_id: str,
    member_id: str,
    body: MemberAdminIn,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Member:
    """Promote/demote a workspace member's admin flag. Admin-only, wall-gated.

    Guards: the target must exist in the same workspace, must be human (not
    an agent/bot_app), and demoting the last remaining admin is rejected so
    a workspace can never end up with zero admins.
    """
    require_workspace_admin(member, workspace_id)
    target = db.query(Member).filter(Member.member_id == member_id).first()
    if target is None or target.workspace_id != workspace_id:
        raise NotFoundError(f"Member '{member_id}' not found")
    if target.member_type != "human":
        raise ForbiddenMemberTypeError(
            f"Member '{target.member_id}' has type '{target.member_type}'; "
            "only 'human' members may be granted admin"
        )
    if not body.is_admin and target.is_admin:
        admin_count = (
            db.query(Member)
            .filter(Member.workspace_id == workspace_id, Member.is_admin.is_(True))
            .count()
        )
        if admin_count == 1:
            raise LastAdminError(
                f"Cannot demote member '{target.member_id}': the workspace "
                "must retain at least one admin"
            )
    target.is_admin = body.is_admin
    db.commit()
    db.refresh(target)
    return target


@router.get("/workspaces/{workspace_id}/export")
def export_workspace(
    workspace_id: str,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> dict:
    """Admin-only full JSON dump of a workspace: meta, channels, members, messages, invites.

    Member profiles are MemberOut-shaped (no emails). Messages use the same
    wire-schema payload as REST/WebSocket, grouped by channel_id.
    """
    require_workspace_admin(member, workspace_id)
    workspace = db.query(Workspace).filter(Workspace.workspace_id == workspace_id).one()
    channels = db.query(Channel).filter(Channel.workspace_id == workspace_id).all()
    members = db.query(Member).filter(Member.workspace_id == workspace_id).all()
    invites = (
        db.query(WorkspaceInvite)
        .filter(WorkspaceInvite.workspace_id == workspace_id)
        .all()
    )
    members_by_id = {m.member_id: m for m in members}

    messages_by_channel: dict[str, list[dict]] = {}
    for channel in channels:
        channel_messages = (
            db.query(Message)
            .filter(Message.channel_id == channel.channel_id)
            .order_by(Message.seq.asc())
            .all()
        )
        messages_by_channel[channel.channel_id] = [
            build_message_payload(
                msg, workspace, channel, members_by_id[msg.sender_member_id]
            )
            for msg in channel_messages
        ]

    return {
        "workspace": {
            "workspace_id": workspace.workspace_id,
            "workspace_name": workspace.workspace_name,
            "visibility": workspace.visibility,
            "created_at": workspace.created_at,
        },
        "channels": [
            {
                "channel_id": c.channel_id,
                "channel_name": c.channel_name,
                "created_at": c.created_at,
            }
            for c in channels
        ],
        "members": [MemberOut.model_validate(m).model_dump() for m in members],
        "messages": messages_by_channel,
        "pending_invites": [InviteOut.model_validate(i).model_dump() for i in invites],
    }


@router.delete("/workspaces/{workspace_id}")
def delete_workspace(
    workspace_id: str,
    confirm: str | None = None,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> dict:
    """Admin-only, confirmed deletion of a workspace: full cascade + audit tombstone.

    Requires ?confirm=delete exactly (case-sensitive); anything else raises
    ConfirmationRequiredError before any row is touched. On success, the
    entire cascade runs as one transaction, strictly children before parents:
    the workspace's own default_channel_id self-reference is cleared and
    flushed first (it points at a channel that's about to be deleted); then
    mentions (FK messages + members) -> messages -> channel_members ->
    channels; then, before members can be deleted, everything that still
    references a member_id is cleared first -- refresh_tokens AND
    workspace_invites (invite.created_by is a NOT NULL FK to members, so it
    must go before the members delete, not after); then members; then the
    workspace row itself; finally the permanent WorkspaceRecord is updated
    to a tombstone ("deleted", who, when) in the same commit.
    """
    require_workspace_admin(member, workspace_id)
    if confirm != _DELETE_CONFIRMATION:
        raise ConfirmationRequiredError(
            f"Deleting a workspace requires ?confirm={_DELETE_CONFIRMATION}"
        )

    workspace = db.query(Workspace).filter(Workspace.workspace_id == workspace_id).one()

    workspace.default_channel_id = None
    db.flush()

    channel_ids = [
        c.channel_id
        for c in db.query(Channel).filter(Channel.workspace_id == workspace_id).all()
    ]
    if channel_ids:
        message_ids = [
            m.message_id
            for m in db.query(Message).filter(Message.channel_id.in_(channel_ids)).all()
        ]
        if message_ids:
            db.query(Mention).filter(Mention.message_id.in_(message_ids)).delete(
                synchronize_session=False
            )
        db.query(Message).filter(Message.channel_id.in_(channel_ids)).delete(
            synchronize_session=False
        )
        db.query(ChannelMember).filter(
            ChannelMember.channel_id.in_(channel_ids)
        ).delete(synchronize_session=False)
        db.query(Channel).filter(Channel.workspace_id == workspace_id).delete(
            synchronize_session=False
        )

    member_ids = [
        m.member_id
        for m in db.query(Member).filter(Member.workspace_id == workspace_id).all()
    ]
    if member_ids:
        db.query(RefreshToken).filter(RefreshToken.member_id.in_(member_ids)).delete(
            synchronize_session=False
        )
    # workspace_invites.created_by is a NOT NULL FK to members, so pending
    # invites must be cleared before the members delete below, not after.
    db.query(WorkspaceInvite).filter(
        WorkspaceInvite.workspace_id == workspace_id
    ).delete(synchronize_session=False)
    if member_ids:
        db.query(Member).filter(Member.workspace_id == workspace_id).delete(
            synchronize_session=False
        )

    db.delete(workspace)

    record = (
        db.query(WorkspaceRecord)
        .filter(WorkspaceRecord.workspace_id == workspace_id)
        .one()
    )
    record.status = "deleted"
    record.deleted_by = member.member_id
    record.deleted_at = utcnow()

    db.commit()
    return {"status": "deleted"}
