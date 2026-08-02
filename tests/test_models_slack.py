"""Tests for the Slack-model Member/Workspace columns and WorkspaceRecord."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Member, Workspace, WorkspaceRecord


def _make(db_session, model, **kwargs):
    obj = model(**kwargs)
    db_session.add(obj)
    db_session.commit()
    return obj


def test_member_workspace_id_is_required(db_session):
    """Post-cutover: Member.workspace_id is NOT NULL -- a workspace-less
    member can no longer be created."""
    db_session.add(Member(member_name="A", member_type="human"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_member_can_belong_to_workspace_with_admin_flag(db_session):
    ws = _make(db_session, Workspace, workspace_name="Acme")
    member = _make(
        db_session,
        Member,
        member_name="Founder",
        member_type="human",
        workspace_id=ws.workspace_id,
        is_admin=True,
    )
    loaded = db_session.get(Member, member.member_id)
    assert loaded.workspace_id == ws.workspace_id
    assert loaded.is_admin is True


def test_member_is_admin_defaults_false(db_session):
    ws = _make(db_session, Workspace, workspace_name="Acme")
    member = _make(
        db_session,
        Member,
        member_name="Regular",
        member_type="human",
        workspace_id=ws.workspace_id,
    )
    assert member.is_admin is False


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
