from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.accounts import build_member_self_out, create_member_account
from app.auth import get_current_account, get_current_member
from app.authorization import require_same_workspace
from app.capabilities import Cap, require_cap
from app.database import get_db
from app.errors import (
    ConfirmationRequiredError,
    ForbiddenMemberTypeError,
    LastAdminError,
    NotFoundError,
    SelfRemovalError,
    WorkspaceNameTakenError,
)
from app.models import (
    Account,
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
from app.routers.auth import _issue_workspace_token_pair
from app.schemas import (
    FoundWorkspaceIn,
    InviteOut,
    MemberOut,
    MemberRoleIn,
    TokenPairOut,
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
    body: FoundWorkspaceIn,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> WorkspaceAuthOut:
    """Found a workspace: workspace + 'general' + admin profile + audit record, atomically.

    Account-authed (spec §3, SMAC-79 Task 2 cutover): the caller already
    has a global account (via their account token); founding only ever
    LINKS that account into a brand-new admin profile here, it never
    creates a password. The response includes a convenience WORKSPACE
    token pair (minted below) so the caller needs no second call to start
    acting inside the workspace they just founded.
    """
    duplicate = (
        db.query(Workspace)
        .filter(Workspace.workspace_name_key == body.workspace_name.lower())
        .first()
    )
    if duplicate is not None:
        raise WorkspaceNameTakenError(
            f"A workspace named '{body.workspace_name}' already exists"
        )
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
        account=account,
        first_name=body.display_first_name,
        last_name=body.display_last_name,
        role="admin",
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
    tokens = _issue_workspace_token_pair(db, founder)
    return WorkspaceAuthOut(
        member=build_member_self_out(db, founder),
        workspace=WorkspaceOut.model_validate(workspace),
        **tokens.model_dump(),
    )


@router.post("/workspaces/{workspace_id}/token", response_model=TokenPairOut)
def mint_workspace_token(
    workspace_id: str,
    account: Account = Depends(get_current_account),
    db: Session = Depends(get_db),
) -> TokenPairOut:
    """Exchange an account token for a workspace token pair (spec §2).

    The caller must hold a membership (a `Member` row linked to this
    account) in `workspace_id`; a non-membership is the uniform 404 --
    "the wall" -- indistinguishable from a nonexistent workspace, exactly
    like `require_same_workspace` already does for workspace-tier
    callers.
    """
    member = (
        db.query(Member)
        .filter(
            Member.workspace_id == workspace_id, Member.account_id == account.account_id
        )
        .first()
    )
    if member is None:
        raise NotFoundError(f"Workspace '{workspace_id}' not found")
    return _issue_workspace_token_pair(db, member)


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
    """Everyone in the caller's own workspace (the wall blocks all others).

    `Cap.VIEW_MEMBERS`-gated (SMAC-92) -- held by every human role, so
    this only bites non-human callers, which every human role has held
    since Task 1. Confirmed the MCP bridge (`smac_mcp/`) never calls this
    route with an agent key (it only ever uses `/members/me`, `/workspaces/
    {id}/{unreads,channels}`, channel messages/read -- all under `Cap.READ`
    or self-profile, unaffected by this gate) -- see the task report for
    the full trace.
    """
    require_same_workspace(member, workspace_id)
    require_cap(member, Cap.VIEW_MEMBERS)
    return db.query(Member).filter(Member.workspace_id == workspace_id).all()


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def update_workspace_visibility(
    workspace_id: str,
    body: WorkspaceVisibilityIn,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Workspace:
    """Flip a workspace's visibility. Admin-only, wall-gated."""
    require_same_workspace(member, workspace_id)
    require_cap(member, Cap.MANAGE_WORKSPACE)
    workspace = db.query(Workspace).filter(Workspace.workspace_id == workspace_id).one()
    workspace.visibility = body.visibility
    db.commit()
    db.refresh(workspace)
    return workspace


@router.patch(
    "/workspaces/{workspace_id}/members/{member_id}", response_model=MemberOut
)
def update_member_role(
    workspace_id: str,
    member_id: str,
    body: MemberRoleIn,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Member:
    """Assign a workspace member's role. `Cap.ASSIGN_ROLES`-gated, wall-gated.

    Guards, in order: the wall + capability check; the target must exist in
    the same workspace; the target must be human (not an agent/bot_app --
    agents are a member TYPE, not a role, spec §2); and moving the last
    remaining admin off of `admin` is rejected so a workspace can never end
    up with zero admins.
    """
    require_same_workspace(member, workspace_id)
    require_cap(member, Cap.ASSIGN_ROLES)
    target = db.query(Member).filter(Member.member_id == member_id).first()
    if target is None or target.workspace_id != workspace_id:
        raise NotFoundError(f"Member '{member_id}' not found")
    if target.member_type != "human":
        raise ForbiddenMemberTypeError(
            f"Member '{target.member_id}' has type '{target.member_type}'; "
            "only 'human' members may hold roles"
        )
    if body.role != "admin" and target.role == "admin":
        admin_count = (
            db.query(Member)
            .filter(Member.workspace_id == workspace_id, Member.role == "admin")
            .count()
        )
        if admin_count == 1:
            raise LastAdminError(
                f"Cannot demote member '{target.member_id}': the workspace "
                "must retain at least one admin"
            )
    target.role = body.role
    db.commit()
    db.refresh(target)
    return target


@router.delete("/workspaces/{workspace_id}/members/{member_id}")
def remove_member(
    workspace_id: str,
    member_id: str,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Remove a member from the workspace. `Cap.REMOVE_MEMBERS`-gated, wall-gated.

    Guards, in order: the wall + capability check; the target must exist in
    the same workspace (uniform 404); no self-removal (400 `self_removal`
    -- use workspace deletion, or a future 'leave', for that); removing the
    workspace's only admin is rejected (409 `last_admin`, same invariant
    `update_member_role` enforces).

    Removal deletes the membership row: the target's workspace-tier tokens
    die on their very next request (`get_current_member` resolves
    membership live off the `members` table, no caching -- a stale token
    404s "unknown member" -> 401 `invalid_token`); any open socket dies at
    its next reconnect (the same accepted window as logout). Cascades:
    channel memberships + their read cursors (`channel_members`, which
    holds `last_read_seq`) are deleted -- the member's presence disappears
    from every channel; their refresh tokens and any invites they created
    are deleted too (ephemeral/administrative, not history).

    Messages and mentions REMAIN (chat history is workspace property):
    `Message.sender_member_id` / `Mention.mentioned_member_id` are
    nullable as of migration `7a3b580f5d0c` specifically so a departing
    member's past messages/mentions can survive their row going away --
    hard-deleting the row outright is blocked by SQLite's FK enforcement
    (`PRAGMA foreign_keys=ON`) the instant the member has ever posted, so
    those two columns are nulled here *before* the row is deleted, and
    `build_message_payload` (app/schemas.py) renders a "(removed member)"
    placeholder wherever the sender is now null.
    """
    require_same_workspace(member, workspace_id)
    require_cap(member, Cap.REMOVE_MEMBERS)
    target = db.query(Member).filter(Member.member_id == member_id).first()
    if target is None or target.workspace_id != workspace_id:
        raise NotFoundError(f"Member '{member_id}' not found")
    if target.member_id == member.member_id:
        raise SelfRemovalError("Use workspace deletion or transfer admin first")
    if target.role == "admin":
        admin_count = (
            db.query(Member)
            .filter(Member.workspace_id == workspace_id, Member.role == "admin")
            .count()
        )
        if admin_count == 1:
            raise LastAdminError("Cannot remove the workspace's only admin")

    db.query(Message).filter(Message.sender_member_id == member_id).update(
        {"sender_member_id": None}
    )
    db.query(Mention).filter(Mention.mentioned_member_id == member_id).update(
        {"mentioned_member_id": None}
    )
    db.query(WorkspaceInvite).filter(WorkspaceInvite.created_by == member_id).delete()
    db.query(RefreshToken).filter(RefreshToken.member_id == member_id).delete()
    db.query(ChannelMember).filter(ChannelMember.member_id == member_id).delete()
    db.delete(target)
    db.commit()
    return {"status": "removed"}


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
    require_same_workspace(member, workspace_id)
    require_cap(member, Cap.MANAGE_WORKSPACE)
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
                msg,
                workspace,
                channel,
                (
                    members_by_id.get(msg.sender_member_id)
                    if msg.sender_member_id is not None
                    else None
                ),
                db,
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
    require_same_workspace(member, workspace_id)
    require_cap(member, Cap.MANAGE_WORKSPACE)
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
