def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unmatched_route_falls_back_to_the_web_ui(client):
    """Since SMAC-85, an unmatched GET outside the API's own prefixes is a
    client-side route the web UI's SPA catch-all owns (app/webui.py) -- it
    now serves index.html, not a bare JSON 404. See
    tests/test_webui_serving.py for the full serving contract, including
    proof that unmatched API-prefixed paths still get the JSON envelope."""
    response = client.get("/does-not-exist")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_unmatched_api_prefixed_route_still_returns_error_envelope(client):
    response = client.get("/accounts/does-not-exist/at-all")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"


def test_wrong_method_returns_error_envelope(client):
    response = client.delete("/health")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "http_error"
