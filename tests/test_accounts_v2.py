"""Account-tier endpoints (SMAC-79 Task 1, spec §2): POST /accounts,
POST /accounts/login, GET /accounts/me.

Binding security invariants under test (spec §7): global login/signup
uniform failures, byte-identical bodies, timing parity (dummy verify) --
same discipline as the retiring POST /auth/discover.
"""

from tests.conftest import founder_auth, founder_headers

_PASSWORD = "correct-horse-battery-staple"


def _create_account(client, email: str, password: str = _PASSWORD):
    return client.post("/accounts", json={"email": email, "password": password})


def test_signup_happy_path_returns_tokens_and_account(client):
    response = _create_account(client, "alice@example.com")
    assert response.status_code == 200
    body = response.json()
    assert body["account"]["email"] == "alice@example.com"
    assert body["account"]["account_id"]
    assert "created_at" in body["account"]
    assert body["tokens"]["access_token"] and body["tokens"]["refresh_token"]
    assert body["tokens"]["token_type"] == "bearer"


def test_signup_account_token_works_immediately(client):
    body = _create_account(client, "bob@example.com").json()
    response = client.get(
        "/accounts/me",
        headers={"Authorization": f"Bearer {body['tokens']['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == "bob@example.com"


def test_duplicate_email_case_variant_is_409(client):
    _create_account(client, "carol@example.com")
    response = _create_account(client, "Carol@Example.com")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


def test_short_password_rejected(client):
    response = _create_account(client, "short@example.com", password="short")
    assert response.status_code == 422


def test_get_accounts_me_lists_no_memberships_initially(client):
    body = _create_account(client, "dana@example.com").json()
    response = client.get(
        "/accounts/me",
        headers={"Authorization": f"Bearer {body['tokens']['access_token']}"},
    )
    assert response.status_code == 200
    me = response.json()
    assert me["email"] == "dana@example.com"
    assert me["memberships"] == []


def test_get_accounts_me_requires_auth(client):
    response = client.get("/accounts/me")
    assert response.status_code == 401


def test_login_unknown_email_and_wrong_password_byte_identical(client):
    _create_account(client, "erin@example.com")
    wrong_password = client.post(
        "/accounts/login", json={"email": "erin@example.com", "password": "nope-nope"}
    )
    unknown_email = client.post(
        "/accounts/login", json={"email": "ghost@example.com", "password": "nope-nope"}
    )
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.content == unknown_email.content
    assert wrong_password.json()["error"]["code"] == "invalid_credentials"


def test_login_success_returns_tokens_and_workspaces(client):
    _create_account(client, "finn@example.com")
    response = client.post(
        "/accounts/login", json={"email": "finn@example.com", "password": _PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["account"]["email"] == "finn@example.com"
    assert body["tokens"]["access_token"] and body["tokens"]["refresh_token"]
    assert body["workspaces"] == []


def test_login_lists_workspaces_found_via_legacy_flow(client):
    """The same email founds two workspaces via the OLD (legacy) flow --
    dual-write must have linked both to one global account, so global
    login lists both."""
    client.post(
        "/workspaces",
        json={
            "workspace_name": "Alpha Co V2",
            "visibility": "private",
            "email": "greta@example.com",
            "password": "legacy-password-1",
            "first_name": "Greta",
            "last_name": "One",
        },
    )
    client.post(
        "/workspaces",
        json={
            "workspace_name": "Beta Co V2",
            "visibility": "private",
            "email": "greta@example.com",
            "password": "legacy-password-2",
            "first_name": "Greta",
            "last_name": "Two",
        },
    )
    # The FIRST (oldest) workspace's password is the account's real
    # password -- get_or_create_account_for_email never overwrites it.
    response = client.post(
        "/accounts/login",
        json={"email": "greta@example.com", "password": "legacy-password-1"},
    )
    assert response.status_code == 200
    names = sorted(w["workspace_name"] for w in response.json()["workspaces"])
    assert names == ["Alpha Co V2", "Beta Co V2"]
    for w in response.json()["workspaces"]:
        assert set(w.keys()) == {
            "workspace_id",
            "workspace_name",
            "member_id",
            "handle",
        }


def test_login_second_workspaces_password_no_longer_works_for_account(client):
    """The second workspace's password was never copied onto the shared
    account (get_or_create links, never overwrites) -- global login with
    it fails, even though the member row itself still has it for legacy
    /auth/login."""
    client.post(
        "/workspaces",
        json={
            "workspace_name": "Gamma Co V2",
            "visibility": "private",
            "email": "harold@example.com",
            "password": "first-password-here",
            "first_name": "Harold",
            "last_name": "One",
        },
    )
    client.post(
        "/workspaces",
        json={
            "workspace_name": "Delta Co V2",
            "visibility": "private",
            "email": "harold@example.com",
            "password": "second-password-here",
            "first_name": "Harold",
            "last_name": "Two",
        },
    )
    response = client.post(
        "/accounts/login",
        json={"email": "harold@example.com", "password": "second-password-here"},
    )
    assert response.status_code == 401


def test_legacy_founding_still_works_unmodified(client):
    """Baseline invariant: founding a workspace through the legacy door
    still returns exactly the old shape and still logs the founder in."""
    founder = founder_auth(client, "legacy-check")
    assert founder["access_token"]
    assert founder["member_id"]
    assert founder["workspace_id"]


def test_human_founding_dual_writes_linked_account(client):
    """Founding a workspace (legacy door) must dual-write: the legacy
    Member.email/password_hash columns stay populated (old login keeps
    working) AND a global Account is created and linked."""
    import app.database as database_module
    from app.models import Account, Member

    founder = founder_auth(client, "human-dual")
    with database_module.SessionLocal() as db:
        member = db.query(Member).filter(Member.member_id == founder["member_id"]).one()
        assert member.account_id is not None
        assert member.email == "human-dual@test.example"
        assert member.password_hash is not None
        account = db.get(Account, member.account_id)
        assert account is not None
        assert account.account_type == "human"
        assert account.email_key == "human-dual@test.example"
        assert account.password_hash is not None


def test_agent_creation_dual_writes_identity_only_account(client):
    """Agent creation dual-writes an identity-only Account: no
    email/password, account_type matches the member_type."""
    import app.database as database_module
    from app.models import Account, Member

    agent = client.post(
        "/members/agents",
        json={"member_name": "Dual Bot"},
        headers=founder_headers(client, "agent-dual"),
    ).json()
    with database_module.SessionLocal() as db:
        member = db.query(Member).filter(Member.member_id == agent["member_id"]).one()
        assert member.account_id is not None
        account = db.get(Account, member.account_id)
        assert account is not None
        assert account.account_type == "agent"
        assert account.email is None
        assert account.password_hash is None


def test_two_workspaces_same_email_link_to_one_account(client):
    """The get-or-create-by-email helper must LINK, not duplicate: two
    workspaces founded with the same email end up with two Member rows
    sharing one Account."""
    import app.database as database_module
    from app.models import Member

    client.post(
        "/workspaces",
        json={
            "workspace_name": "Linked Co One",
            "visibility": "private",
            "email": "ivy@example.com",
            "password": "password-one-here",
            "first_name": "Ivy",
            "last_name": "One",
        },
    )
    client.post(
        "/workspaces",
        json={
            "workspace_name": "Linked Co Two",
            "visibility": "private",
            "email": "ivy@example.com",
            "password": "password-two-here",
            "first_name": "Ivy",
            "last_name": "Two",
        },
    )
    with database_module.SessionLocal() as db:
        members = db.query(Member).filter(Member.email == "ivy@example.com").all()
        assert len(members) == 2
        assert members[0].account_id == members[1].account_id
