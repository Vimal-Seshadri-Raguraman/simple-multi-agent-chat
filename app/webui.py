"""Serves the committed web UI bundle (SMAC-85 Task 1).

The bundle lives at ``app/static/webui/`` and is committed with the source
(design system constitution §8's trade: pip-install users get the UI without
Node; Node stays a developer-only dependency). ``smac_web/`` produces it via
``npm run build`` -- see that package's README/scripts for the pipeline.

``mount_webui`` must be called LAST in ``app/main.py``, after every API
router is included, so this module's catch-all route only ever sees paths
that no real route claimed. Two things live here:

- ``/webui/*`` -- the built static assets (JS/CSS), served via
  ``StaticFiles``. The build's Vite ``base`` is set to ``/webui/`` so the
  bundle's own asset references already point here.
- A catch-all GET route for everything else: unmatched paths under one of
  the API's own prefixes (``/accounts``, ``/workspaces``, ...) stay a JSON
  404 (the same envelope every other API 404 uses, via the
  ``StarletteHTTPException`` handler already registered in ``app/main.py``);
  every other path is a client-side route the SPA owns, so it gets
  ``index.html`` with the CSP header (web spec §1 -- self-only scripts,
  styles, and connections; ``ws:``/``wss:`` allowed for the live socket).
"""

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

WEBUI_DIR = Path(__file__).parent / "static" / "webui"
INDEX_FILE = WEBUI_DIR / "index.html"

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "connect-src 'self' ws: wss:; img-src 'self' data:"
)

#: Prefixes the API surface owns: routers, the websocket routes, the version
#: handshake, and FastAPI's own docs/schema endpoints. A GET under one of
#: these that no real route matched is a genuine API 404, not a client
#: route -- it must keep the JSON error envelope, never fall back to the SPA
#: shell (this is the authoritative set from the task brief).
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

router = APIRouter()


def _is_api_path(path: str) -> bool:
    candidate = "/" + path
    return any(
        candidate == prefix or candidate.startswith(prefix + "/")
        for prefix in API_PREFIXES
    )


def _index_response() -> FileResponse:
    return FileResponse(INDEX_FILE, headers={"Content-Security-Policy": CSP})


@router.get("/", include_in_schema=False)
async def spa_root() -> FileResponse:
    return _index_response()


@router.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str) -> FileResponse:
    if _is_api_path(full_path):
        # No real route matched an API-prefixed path -- surface the same
        # JSON 404 envelope every other unmatched API path gets, via the
        # StarletteHTTPException handler main.py already registers.
        raise HTTPException(status_code=404, detail="Not Found")
    return _index_response()


def mount_webui(app: FastAPI) -> None:
    """Mount the built asset directory and register the SPA catch-all.

    Must be called LAST, after every API router is included, so those
    routes keep matching priority ahead of the catch-all route above.
    """
    app.mount("/webui", StaticFiles(directory=WEBUI_DIR), name="webui")
    app.include_router(router)
