"""GET /meta: unauthenticated version/handshake endpoint (SMAC-72 task 1)."""

from app import __version__


def test_meta_unauthenticated_returns_version_and_api_version(client):
    response = client.get("/meta")
    assert response.status_code == 200
    assert response.json() == {"server_version": __version__, "api_version": 1}


def test_meta_requires_no_auth_header(client):
    """No Authorization header is sent -- the endpoint must not 401."""
    response = client.get("/meta", headers={})
    assert response.status_code == 200
