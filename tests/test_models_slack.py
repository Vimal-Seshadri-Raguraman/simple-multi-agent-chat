"""Tests for the Slack-model schema staging: new Member columns, visibility, WorkspaceRecord."""

from app.models import Member, Workspace, WorkspaceRecord


def _make(db_session, model, **kwargs):
    obj = model(**kwargs)
    db_session.add(obj)
    db_session.commit()
    return obj


def test_member_slack_columns_default(db_session):
    member = _make(db_session, Member, member_name="A", member_type="human")
    assert (
        member.workspace_id is None
    )  # nullable during staging; NOT NULL after cutover
    assert member.is_admin is False


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


def test_workspace_visibility_defaults_private(db_session):
    ws = _make(db_session, Workspace, workspace_name="Acme")
    assert ws.visibility == "private"


def test_workspace_record_round_trip(db_session):
    founder = _make(db_session, Member, member_name="F", member_type="human")
    ws = _make(db_session, Workspace, workspace_name="Acme")
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
