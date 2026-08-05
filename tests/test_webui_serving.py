"""Server-side serving tests for the committed web UI bundle (SMAC-85 Task 1).

Web spec §1 (`docs/superpowers/specs/2026-08-04-smac-web-ui-design.md`):
`/` and any unmatched non-API client route fall through to the SPA's
`index.html` with a strict, self-only CSP header; `/webui/*` serves the
built static assets; API routers, `/ws`, `/meta`, `/docs`, and
`/openapi.json` keep routing priority -- an unmatched path under one of
those prefixes must still return the API's own JSON error envelope, never
the SPA shell.
"""

import pytest

from app.webui import CSP, WEBUI_DIR

API_PREFIXES = (
    "/accounts",
    "/workspaces",
    "/members",
    "/mentions",
    "/auth",
    "/ws",
    "/meta",
    "/docs",
    "/openapi.json",
)


def test_root_serves_index_html_with_exact_csp_header(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.headers["content-security-policy"] == CSP
    assert '<div id="root">' in response.text


def test_unmatched_client_route_falls_back_to_index(client):
    response = client.get("/some/client/route")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == CSP
    assert response.text == client.get("/").text


def test_deeply_nested_client_route_falls_back_to_index(client):
    response = client.get("/workspace/abc123/channel/general")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == CSP
    assert '<div id="root">' in response.text


def test_api_404_envelope_survives_the_spa_catchall(client):
    """The exact example from the task brief: a real API prefix with no
    matching sub-route must still 404 as JSON, not fall back to the SPA."""
    response = client.get("/workspaces/nonexistent")
    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert "code" in body["error"]
    assert "message" in body["error"]
    assert "content-security-policy" not in {k.lower() for k in response.headers}


@pytest.mark.parametrize("prefix", API_PREFIXES)
def test_all_api_prefixes_pass_through_json_404_on_miss(client, prefix):
    response = client.get(f"{prefix}/definitely-not-a-real-sub-path-xyz")
    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert "code" in body["error"]
    assert "message" in body["error"]


def test_meta_and_docs_and_openapi_still_work_normally(client):
    # Real, matched routes must be entirely unaffected by the catch-all.
    assert client.get("/meta").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_webui_static_asset_is_served(client):
    assets_dir = WEBUI_DIR / "assets"
    asset_files = sorted(p for p in assets_dir.glob("*.js") if p.is_file())
    assert asset_files, "expected at least one built JS asset under webui/assets"
    asset = asset_files[0]

    response = client.get(f"/webui/assets/{asset.name}")

    assert response.status_code == 200
    assert response.text == asset.read_text()
