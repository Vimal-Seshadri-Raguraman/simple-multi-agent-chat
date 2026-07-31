def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unmatched_route_returns_error_envelope(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"


def test_wrong_method_returns_error_envelope(client):
    response = client.delete("/health")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "http_error"
