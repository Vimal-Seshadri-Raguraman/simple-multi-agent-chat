"""Tests for the Slack-model Member/Workspace columns and WorkspaceRecord."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Account, Member, Workspace, WorkspaceRecord


def _make(db_session, model, **kwargs):
    obj = model(**kwargs)
    db_session.add(obj)
    db_session.commit()
    return obj


def _account(db_session) -> Account:
    account = Account(account_type="human")
    db_session.add(account)
    db_session.flush()
    return account


def test_member_workspace_id_is_required(db_session):
    """Post-cutover: Member.workspace_id is NOT NULL -- a workspace-less
    member can no longer be created."""
    db_session.add(
        Member(
            member_name="A",
            member_type="human",
            account_id=_account(db_session).account_id,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_member_account_id_is_required(db_session):
    """Identity v2 (SMAC-79 Task 2): Member.account_id is NOT NULL -- a
    profile can no longer be created without a linked account."""
    ws = _make(db_session, Workspace, workspace_name="Acme")
    db_session.add(
        Member(member_name="A", member_type="human", workspace_id=ws.workspace_id)
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_member_can_belong_to_workspace_with_role(db_session):
    ws = _make(db_session, Workspace, workspace_name="Acme")
    member = _make(
        db_session,
        Member,
        member_name="Founder",
        member_type="human",
        workspace_id=ws.workspace_id,
        account_id=_account(db_session).account_id,
        role="admin",
        handle="founder",
    )
    loaded = db_session.get(Member, member.member_id)
    assert loaded.workspace_id == ws.workspace_id
    assert loaded.role == "admin"


def test_member_role_defaults_to_member(db_session):
    ws = _make(db_session, Workspace, workspace_name="Acme")
    member = _make(
        db_session,
        Member,
        member_name="Regular",
        member_type="human",
        workspace_id=ws.workspace_id,
        account_id=_account(db_session).account_id,
        handle="regular",
    )
    assert member.role == "member"


def test_workspace_visibility_defaults_private(db_session):
    ws = _make(db_session, Workspace, workspace_name="Acme")
    assert ws.visibility == "private"


def test_workspace_record_round_trip(db_session):
    ws = _make(db_session, Workspace, workspace_name="Acme")
    founder = _make(
        db_session,
        Member,
        member_name="F",
        member_type="human",
        workspace_id=ws.workspace_id,
        account_id=_account(db_session).account_id,
        handle="f",
    )
    record = _make(
        db_session,
        WorkspaceRecord,
        workspace_id=ws.workspace_id,
        workspace_name=ws.workspace_name,
        created_by=founder.member_id,
    )
    loaded = db_session.get(WorkspaceRecord, ws.workspace_id)
    assert loaded.status == "active"
    assert loaded.deleted_by is None and loaded.deleted_at is None
    assert loaded.created_at.tzinfo is not None
