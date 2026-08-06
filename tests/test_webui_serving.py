"""Server-side serving tests for the committed web UI bundle (SMAC-85 Task 1).

Web spec §1 (`docs/superpowers/specs/2026-08-04-smac-web-ui-design.md`):
`/` and any unmatched non-API client route fall through to the SPA's
`index.html` with a strict, self-only CSP header; `/webui/*` serves the
built static assets; every real API route keeps routing priority -- an
unmatched path under one of the API's own top-level prefixes must still
return the API's own JSON error envelope, never the SPA shell.

The passthrough prefix set is derived from the app's actual registered
routes (`app.webui.api_prefixes_for`), not a hand-maintained list -- a
review of the first cut found a hand-written list had silently missed
`/member` (singular), `/health`, and `/redoc`, each falling through to the
SPA instead of the API's JSON 404. The tests below both regress those three
specific misses and guard the *mechanism* generically (a synthetic app with
a route this module has never heard of still gets correct passthrough
behavior), so a future router can't reintroduce the same class of bug.
"""

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import webui
from app.main import app as main_app
from app.webui import CSP, WEBUI_DIR, mount_webui


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


def _assert_api_404(response) -> None:
    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert "code" in body["error"]
    assert "message" in body["error"]


def test_member_singular_prefix_passes_through_to_json_404(client):
    """Regression: GET /member (singular -- app/routers/members.py's
    `get_member`) was missing from the original hand-written prefix list;
    an unmatched sub-path under it was silently served the SPA shell."""
    _assert_api_404(client.get("/member/nope"))


def test_health_prefix_passes_through_to_json_404(client):
    """Regression: /health (a plain @app.get in app/main.py, not a
    router) was also missing from the original hand-written list."""
    _assert_api_404(client.get("/health/nope"))


def test_redoc_prefix_passes_through_to_json_404(client):
    """Regression: /redoc (FastAPI's own alternate docs UI, alongside
    /docs) was also missing from the original hand-written list."""
    _assert_api_404(client.get("/redoc/nope"))


def test_all_live_api_prefixes_pass_through_json_404_on_miss(client):
    """Exercises every prefix the running app actually decided to protect
    (`app.state.webui_api_prefixes`, set by `mount_webui`) rather than a
    second, independently-drifting hardcoded list in this test file."""
    for prefix in main_app.state.webui_api_prefixes:
        response = client.get(f"{prefix}/definitely-not-a-real-sub-path-xyz")
        assert response.status_code in (404, 405), prefix
        if response.status_code == 404:
            _assert_api_404(response)


def test_passthrough_prefixes_cover_every_currently_registered_route(client):
    """The drift guard: independently recompute the expected prefix set
    from the app's live route table and assert it's exactly what
    `mount_webui` stored. Fails the day someone adds a router (or a plain
    `@app.get`) without it landing in `app.state.webui_api_prefixes` --
    e.g. because a route got registered on the app AFTER `mount_webui` ran,
    which would break the "mount last" contract."""
    expected = {
        webui._first_segment(getattr(route, "path", "") or "")
        for route in main_app.routes
    }
    # The SPA's own routes ("/" and the "{full_path:path}" catch-all
    # itself) aren't API routes -- exclude their segments before comparing.
    expected.discard("")
    expected.discard("/{full_path:path}")

    assert expected == main_app.state.webui_api_prefixes


def test_meta_and_docs_and_openapi_still_work_normally(client):
    # Real, matched routes must be entirely unaffected by the catch-all.
    assert client.get("/meta").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/health").status_code == 200


def test_webui_static_asset_is_served(client):
    assets_dir = WEBUI_DIR / "assets"
    asset_files = sorted(p for p in assets_dir.glob("*.js") if p.is_file())
    assert asset_files, "expected at least one built JS asset under webui/assets"
    asset = asset_files[0]

    response = client.get(f"/webui/assets/{asset.name}")

    assert response.status_code == 200
    assert response.text == asset.read_text()


def test_a_brand_new_router_is_automatically_covered_by_the_passthrough():
    """Proves the mechanism is structural, not a hardcoded list: mount a
    router this module has never heard of, call `mount_webui`, and confirm
    an unmatched sub-path under it still 404s as JSON rather than silently
    falling back to the SPA shell -- exactly the class of bug the review
    caught for /member, /health, and /redoc."""
    synthetic = FastAPI()

    widgets = APIRouter()

    @widgets.get("/widgets/{widget_id}")
    def get_widget(widget_id: str) -> dict[str, str]:
        return {"widget_id": widget_id}

    synthetic.include_router(widgets)

    @synthetic.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request, exc):  # type: ignore[no-untyped-def]
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "http_error", "message": exc.detail}},
        )

    mount_webui(synthetic)

    with TestClient(synthetic) as test_client:
        # Two path segments under /widgets/{widget_id} -- doesn't match the
        # single-segment route, so it's an unmatched path under a prefix
        # `mount_webui` has never explicitly heard of.
        response = test_client.get("/widgets/does-not-exist/nope")
        _assert_api_404(response)

        # A genuine client route with no registered-route collision still
        # falls back to the SPA shell.
        spa_response = test_client.get("/some/client/route")
        assert spa_response.status_code == 200
        assert spa_response.headers["content-security-policy"] == CSP
