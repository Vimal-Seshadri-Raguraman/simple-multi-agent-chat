"""Account-tier endpoints (SMAC-79 spec §2): POST /accounts,
POST /accounts/login, GET /accounts/me.

Binding security invariants under test (spec §7): global login/signup
uniform failures, byte-identical bodies, timing parity (dummy verify) --
the discipline `POST /auth/discover` established before it retired in
Task 2 (this file's `test_discover_*`-descended tests port its
workspace-ordering and private-workspace-visibility coverage onto
`POST /accounts/login`'s `workspaces` list, its permanent successor)."""

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


def _found(client, account_token, workspace_name, visibility="private"):
    return client.post(
        "/workspaces",
        json={
            "workspace_name": workspace_name,
            "visibility": visibility,
            "display_first_name": "Test",
            "display_last_name": "Founder",
        },
        headers={"Authorization": f"Bearer {account_token}"},
    )


def test_login_lists_every_workspace_the_account_has_founded(client):
    """One account founds two workspaces (spec Decision 1's per-workspace
    profile model, from the founding side): both memberships link back to
    the SAME global account, so global login lists both."""
    account_token = _create_account(client, "greta@example.com").json()["tokens"][
        "access_token"
    ]
    _found(client, account_token, "Alpha Co V2")
    _found(client, account_token, "Beta Co V2")
    response = client.post(
        "/accounts/login",
        json={"email": "greta@example.com", "password": _PASSWORD},
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


def test_login_orders_workspaces_by_name_not_creation_order(client):
    """Ported from the retired POST /auth/discover's coverage: create Zeta
    before Alpha, login must still list them alphabetically."""
    account_token = _create_account(client, "order@example.com").json()["tokens"][
        "access_token"
    ]
    _found(client, account_token, "Zeta Co")
    _found(client, account_token, "Alpha Co")
    response = client.post(
        "/accounts/login",
        json={"email": "order@example.com", "password": _PASSWORD},
    )
    names = [w["workspace_name"] for w in response.json()["workspaces"]]
    assert names == ["Alpha Co", "Zeta Co"]


def test_login_lists_private_workspaces_too(client):
    """Ported from the retired POST /auth/discover: private workspaces
    are the caller's own memberships -- they must appear."""
    account_token = _create_account(client, "priv@example.com").json()["tokens"][
        "access_token"
    ]
    found = _found(client, account_token, "Private Co V2", visibility="private").json()
    response = client.post(
        "/accounts/login",
        json={"email": "priv@example.com", "password": _PASSWORD},
    )
    workspaces = response.json()["workspaces"]
    assert len(workspaces) == 1
    assert workspaces[0]["workspace_id"] == found["workspace"]["workspace_id"]
    assert workspaces[0]["workspace_name"] == "Private Co V2"


def test_founder_auth_helper_returns_account_id_and_token(client):
    """The conftest contract (SMAC-79 Task 2): founder_auth/member_auth
    return the same dict shape as before, PLUS `account_id`/`account_token`
    -- both real and usable (account_token works on an account-tier
    endpoint, account_id matches the founder's linked account)."""
    import app.database as database_module
    from app.models import Member

    founder = founder_auth(client, "helper-contract")
    assert founder["access_token"]
    assert founder["member_id"]
    assert founder["workspace_id"]
    assert founder["account_id"]
    assert founder["account_token"]

    with database_module.SessionLocal() as db:
        member = db.query(Member).filter(Member.member_id == founder["member_id"]).one()
        assert member.account_id == founder["account_id"]

    me = client.get(
        "/accounts/me",
        headers={"Authorization": f"Bearer {founder['account_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["account_id"] == founder["account_id"]


def test_human_founding_links_the_caller_account(client):
    """Founding a workspace links the CALLER's own account (from their
    account token) into the new admin profile -- no password is created
    or stored on the Member profile anymore (Identity v2, SMAC-79 Task 2:
    email/password live only on the account)."""
    import app.database as database_module
    from app.models import Account, Member

    account_body = _create_account(client, "human-link@test.example").json()
    account_token = account_body["tokens"]["access_token"]
    founded = _found(client, account_token, "Human Link Co").json()

    with database_module.SessionLocal() as db:
        member = (
            db.query(Member)
            .filter(Member.member_id == founded["member"]["member_id"])
            .one()
        )
        assert member.account_id == account_body["account"]["account_id"]
        account = db.get(Account, member.account_id)
        assert account is not None
        assert account.account_type == "human"
        assert account.email_key == "human-link@test.example"
        assert account.password_hash is not None


def test_agent_creation_creates_identity_only_account(client):
    """Agent creation creates an identity-only Account: no
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


def test_same_account_two_workspaces_share_one_account_id(client):
    """One account founding two workspaces produces two Member rows
    sharing one account_id (spec Decision 1's per-workspace-profile
    model, DB-level view)."""
    import app.database as database_module
    from app.models import Member

    account_token = _create_account(client, "ivy@example.com").json()["tokens"][
        "access_token"
    ]
    _found(client, account_token, "Linked Co One")
    _found(client, account_token, "Linked Co Two")

    with database_module.SessionLocal() as db:
        from app.models import Account as AccountModel

        members = (
            db.query(Member)
            .join(AccountModel, AccountModel.account_id == Member.account_id)
            .filter(AccountModel.email_key == "ivy@example.com")
            .all()
        )
        assert len(members) == 2
        assert members[0].account_id == members[1].account_id
