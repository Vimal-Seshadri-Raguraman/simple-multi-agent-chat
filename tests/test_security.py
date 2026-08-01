"""Tests for password hashing and JWT/refresh-token primitives."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.security import (
    SECRET_KEY,
    check_secret_key_is_safe_for_environment,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)


def test_password_hash_verifies():
    hashed = hash_password("correct horse battery")
    assert hashed != "correct horse battery"
    assert verify_password("correct horse battery", hashed)


def test_wrong_password_rejected():
    hashed = hash_password("correct horse battery")
    assert not verify_password("wrong password", hashed)


def test_access_token_round_trip():
    token = create_access_token("member-123")
    assert decode_access_token(token) == "member-123"


def test_expired_access_token_rejected():
    expired = jwt.encode(
        {"sub": "member-123", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        SECRET_KEY,
        algorithm="HS256",
    )
    assert decode_access_token(expired) is None


def test_tampered_access_token_rejected():
    token = create_access_token("member-123")
    assert decode_access_token(token + "x") is None


def test_token_signed_with_other_key_rejected():
    forged = jwt.encode(
        {"sub": "member-123", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "some-other-secret",
        algorithm="HS256",
    )
    assert decode_access_token(forged) is None


def test_refresh_tokens_are_unique_and_hash_deterministic():
    a, b = generate_refresh_token(), generate_refresh_token()
    assert a != b
    assert hash_token(a) == hash_token(a)
    assert hash_token(a) != hash_token(b)


def test_default_secret_key_rejected_in_production(monkeypatch):
    """A production-like deployment must not silently run with the public default."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeError):
        check_secret_key_is_safe_for_environment()


def test_default_secret_key_allowed_in_development(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    check_secret_key_is_safe_for_environment()  # must not raise
