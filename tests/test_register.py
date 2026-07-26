from app.models import User


def test_register_with_valid_credentials_returns_201(client):
    response = client.post(
        "/register", json={"username": "alice123", "password": "supersecret"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "alice123"
    assert "user_id" in body


def test_register_hashes_password(client, db_session):
    client.post("/register", json={"username": "alice123", "password": "supersecret"})

    saved = db_session.query(User).filter_by(username="alice123").first()
    assert saved.password_hash != "supersecret"


def test_register_duplicate_username_returns_409(client):
    client.post("/register", json={"username": "bob", "password": "supersecret"})
    response = client.post(
        "/register", json={"username": "bob", "password": "anotherpass"}
    )

    assert response.status_code == 409


def test_register_short_username_returns_400(client):
    response = client.post(
        "/register", json={"username": "ab", "password": "supersecret"}
    )

    assert response.status_code == 400


def test_register_short_password_returns_400(client):
    response = client.post(
        "/register", json={"username": "validuser", "password": "short"}
    )

    assert response.status_code == 400


def test_register_missing_fields_returns_400(client):
    response = client.post("/register", json={"username": "onlyusername"})

    assert response.status_code == 400
