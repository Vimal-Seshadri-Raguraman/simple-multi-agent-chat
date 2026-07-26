from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
import pytest

from app.models import User


def test_user_table_created(db_session):
    inspector = inspect(db_session.get_bind())
    assert "users" in inspector.get_table_names()


def test_create_user_instance(db_session):
    user = User(username="alice", password_hash="hashed_pw")
    db_session.add(user)
    db_session.commit()

    saved = db_session.query(User).filter_by(username="alice").first()
    assert saved is not None
    assert saved.username == "alice"
    assert saved.password_hash == "hashed_pw"


def test_duplicate_username_raises_integrity_error(db_session):
    db_session.add(User(username="bob", password_hash="hash1"))
    db_session.commit()

    db_session.add(User(username="bob", password_hash="hash2"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_timestamps_set_automatically(db_session):
    user = User(username="carol", password_hash="hash")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert user.created_at is not None
    assert user.updated_at is not None
