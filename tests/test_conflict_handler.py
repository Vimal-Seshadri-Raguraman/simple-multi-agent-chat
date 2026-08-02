"""A DB uniqueness race must surface as a 409 envelope, never a raw 500."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from tests.conftest import founder_auth


def test_integrity_race_returns_conflict_envelope(client, monkeypatch):
    ws = founder_auth(client, "w1")["workspace_id"]  # founded BEFORE the patch

    original_commit = OrmSession.commit
    state = {"fired": False}

    def racing_commit(self):
        # First commit after patching simulates losing a uniqueness race:
        # the pre-insert SELECT saw no duplicate, but the constraint fires.
        if not state["fired"]:
            state["fired"] = True
            raise IntegrityError(
                "INSERT INTO members", {}, Exception("UNIQUE constraint failed")
            )
        return original_commit(self)

    monkeypatch.setattr(OrmSession, "commit", racing_commit)

    response = client.post(
        f"/workspaces/{ws}/register",
        json={
            "email": "racer@test.example",
            "password": "racer-pass-12",
            "first_name": "Ra",
            "last_name": "Cer",
        },
    )
    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "conflict",
            "message": "The request conflicts with existing data",
        }
    }
    # And the raw DB error text must not appear anywhere in the body:
    assert "UNIQUE" not in response.text
