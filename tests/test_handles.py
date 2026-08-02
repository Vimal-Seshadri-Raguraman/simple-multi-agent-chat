"""Handle slugging and per-workspace uniqueness."""

from app.handles import generate_unique_handle, slugify
from app.models import Member, Workspace


def test_slugify_rules():
    assert slugify("RMode") == "rmode"
    assert slugify("Fin Analyst!!") == "fin-analyst"
    assert slugify("--Weird--__Name--") == "weird-name"
    assert slugify("模型") == "member"  # nothing latin survives -> fallback
    assert len(slugify("x" * 100)) <= 32


def test_unique_handle_suffixes(db_session):
    ws = Workspace(workspace_name="Acme")
    db_session.add(ws)
    db_session.flush()
    first = generate_unique_handle(db_session, ws.workspace_id, "RMode")
    db_session.add(
        Member(
            member_name="R",
            member_type="human",
            workspace_id=ws.workspace_id,
            handle=first,
        )
    )
    db_session.flush()
    second = generate_unique_handle(db_session, ws.workspace_id, "RMode")
    assert (first, second) == ("rmode", "rmode2")


def test_same_handle_ok_across_workspaces(db_session):
    ws1, ws2 = Workspace(workspace_name="A"), Workspace(workspace_name="B")
    db_session.add_all([ws1, ws2])
    db_session.flush()
    db_session.add(
        Member(
            member_name="R",
            member_type="human",
            workspace_id=ws1.workspace_id,
            handle="rmode",
        )
    )
    db_session.flush()
    assert generate_unique_handle(db_session, ws2.workspace_id, "RMode") == "rmode"
