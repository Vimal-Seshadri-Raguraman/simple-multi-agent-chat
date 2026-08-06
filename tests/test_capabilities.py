import pytest

from app.capabilities import Cap, caps_for


def _m(role="member", member_type="human"):
    from app.models import Member

    return Member(
        member_id="m",
        workspace_id="w",
        account_id="a",
        member_type=member_type,
        role=role,
        first_name="T",
        last_name="T",
        handle="t",
    )


def test_member_caps():
    caps = caps_for(_m())
    assert Cap.POST in caps and Cap.CREATE_CHANNELS in caps
    assert Cap.MANAGE_AGENTS not in caps and Cap.MINT_HUMAN_INVITES not in caps


def test_agent_admin_gets_agent_powers_only():
    caps = caps_for(_m(role="agent_admin"))
    assert Cap.MANAGE_AGENTS in caps and Cap.MINT_AGENT_INVITES in caps
    assert Cap.MANAGE_WORKSPACE not in caps and Cap.ASSIGN_ROLES not in caps


def test_admin_gets_everything():
    assert caps_for(_m(role="admin")) == frozenset(Cap)


def test_agent_type_cap_beats_role():
    # Even if a data bug ever put a role on an agent, type cap wins.
    caps = caps_for(_m(role="admin", member_type="agent"))
    assert caps == {Cap.POST, Cap.READ, Cap.ACK_MENTIONS}


def test_bot_app_same_ceiling():
    assert Cap.CREATE_CHANNELS not in caps_for(_m(member_type="bot_app"))
