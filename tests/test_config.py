import pytest

from app.config import resolve_secret_key


def test_resolve_secret_key_uses_env_value_when_present():
    assert resolve_secret_key({"SECRET_KEY": "abc123"}) == "abc123"


def test_resolve_secret_key_raises_in_production_when_missing():
    with pytest.raises(RuntimeError):
        resolve_secret_key({"ENVIRONMENT": "production"})


def test_resolve_secret_key_falls_back_outside_production_when_missing():
    key = resolve_secret_key({"ENVIRONMENT": "development"})
    assert key == "dev-secret-key-insecure-do-not-use-in-production"


def test_resolve_secret_key_falls_back_when_environment_unset():
    key = resolve_secret_key({})
    assert key == "dev-secret-key-insecure-do-not-use-in-production"
