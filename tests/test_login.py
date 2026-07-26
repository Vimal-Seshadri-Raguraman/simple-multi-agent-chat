from datetime import datetime, timedelta, timezone

import jwt

from app.auth import ALGORITHM, SECRET_KEY


def _register(client, username="loginuser", password="supersecret"):
    return client.post("/register", json={"username": username, "password": password})


def test_login_with_correct_credentials_returns_200_and_token(client):
    _register(client)
    response = client.post(
        "/login", json={"username": "loginuser", "password": "supersecret"}
    )

    assert response.status_code == 200
    assert "token" in response.json()


def test_login_token_contains_user_id(client):
    register_response = _register(client)
    user_id = register_response.json()["user_id"]

    login_response = client.post(
        "/login", json={"username": "loginuser", "password": "supersecret"}
    )
    token = login_response.json()["token"]

    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    assert payload["user_id"] == user_id


def test_login_wrong_password_returns_401(client):
    _register(client)
    response = client.post(
        "/login", json={"username": "loginuser", "password": "wrongpassword"}
    )

    assert response.status_code == 401


def test_login_nonexistent_user_returns_401(client):
    response = client.post(
        "/login", json={"username": "ghost", "password": "supersecret"}
    )

    assert response.status_code == 401


def test_login_missing_fields_returns_400(client):
    response = client.post("/login", json={"username": "loginuser"})

    assert response.status_code == 400


def test_protected_route_with_valid_token_returns_200(client):
    register_response = _register(client)
    user_id = register_response.json()["user_id"]
    login_response = client.post(
        "/login", json={"username": "loginuser", "password": "supersecret"}
    )
    token = login_response.json()["token"]

    response = client.post("/test-auth", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == user_id
    assert body["message"] == "authenticated"


def test_protected_route_without_token_returns_401(client):
    response = client.post("/test-auth")

    assert response.status_code == 401


def test_protected_route_with_invalid_token_returns_401(client):
    response = client.post(
        "/test-auth", headers={"Authorization": "Bearer not-a-real-token"}
    )

    assert response.status_code == 401


def test_protected_route_with_expired_token_returns_401(client):
    expired_payload = {
        "user_id": 1,
        "exp": datetime.now(timezone.utc) - timedelta(seconds=10),
    }
    expired_token = jwt.encode(expired_payload, SECRET_KEY, algorithm=ALGORITHM)

    response = client.post(
        "/test-auth", headers={"Authorization": f"Bearer {expired_token}"}
    )

    assert response.status_code == 401
