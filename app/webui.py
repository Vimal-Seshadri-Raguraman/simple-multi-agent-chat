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
  the API's own top-level prefixes (``/accounts``, ``/workspaces``, ...)
  stay a JSON 404 (the same envelope every other API 404 uses, via the
  ``StarletteHTTPException`` handler already registered in ``app/main.py``);
  every other path is a client-side route the SPA owns, so it gets
  ``index.html`` with the CSP header (web spec §1 -- self-only scripts,
  styles, and connections; ``ws:``/``wss:`` allowed for the live socket).

The passthrough prefix set is NOT a hand-maintained list: a review of the
first cut found it had silently missed ``/member`` (singular --
``GET /member`` in ``app/routers/members.py``), ``/health``, and ``/redoc``,
each of which fell through to the SPA shell instead of the API's JSON 404 on
a miss. Hand-growing that list only defers the same bug to the next router.
Instead, ``api_prefixes_for`` derives it from the app's OWN registered
routes at mount time -- a future router can never silently fall into the
SPA, because its top-level path segment is picked up automatically.
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


def _first_segment(path: str) -> str:
    """The first path segment of a route pattern, as a passthrough prefix.

    ``"/workspaces/{workspace_id}/token"`` -> ``"/workspaces"``
    ``"/health"`` -> ``"/health"``
    ``"/"`` -> ``""`` (never a useful prefix; callers filter it out)
    """
    parts = path.split("/", 2)
    segment = parts[1] if len(parts) > 1 else ""
    return f"/{segment}" if segment else ""


def api_prefixes_for(app: FastAPI) -> frozenset[str]:
    """Derive the passthrough prefix set from ``app``'s currently
    registered routes (HTTP routes, websocket routes, and mounts alike --
    all of Starlette's route types expose a ``.path``/mount-prefix
    attribute the same way).

    Must be called BEFORE the SPA's own catch-all route is registered, so
    it can't include itself. Exposed (not private) so tests can recompute
    the expected set independently and assert the served app's actual
    behavior matches it -- the drift guard for this mechanism.
    """
    prefixes: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        segment = _first_segment(path)
        if segment:
            prefixes.add(segment)
    return frozenset(prefixes)


def _index_response() -> FileResponse:
    return FileResponse(INDEX_FILE, headers={"Content-Security-Policy": CSP})


def mount_webui(app: FastAPI) -> None:
    """Mount the built asset directory and register the SPA catch-all.

    Must be called LAST, after every API router is included, so those
    routes keep matching priority ahead of the catch-all route below, and
    so ``api_prefixes_for`` sees the app's complete, real route table.
    """
    # Mounted BEFORE deriving the prefix set so "/webui" itself is in it too
    # (a miss under /webui/* -- e.g. a stale/renamed asset -- should be a
    # real 404, not the SPA shell; StaticFiles already 404s misses on its
    # own, so this only matters for keeping the derived set and what tests
    # independently recompute from app.routes in agreement).
    app.mount("/webui", StaticFiles(directory=WEBUI_DIR), name="webui")

    api_prefixes = api_prefixes_for(app)
    # Stashed on app.state so tests can read back exactly what the running
    # app decided its passthrough set was, without recomputing it through a
    # second, potentially-drifting code path.
    app.state.webui_api_prefixes = api_prefixes

    def _is_api_path(path: str) -> bool:
        candidate = "/" + path
        return any(
            candidate == prefix or candidate.startswith(prefix + "/")
            for prefix in api_prefixes
        )

    # A router built fresh per call (rather than a shared module-level
    # instance) so mount_webui stays safe to call more than once against
    # different app instances -- tests exercise that directly to prove the
    # derivation generalizes to routes this module has never heard of.
    spa_router = APIRouter()

    @spa_router.get("/", include_in_schema=False)
    async def spa_root() -> FileResponse:
        return _index_response()

    @spa_router.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        if _is_api_path(full_path):
            # No real route matched an API-prefixed path -- surface the
            # same JSON 404 envelope every other unmatched API path gets,
            # via the StarletteHTTPException handler main.py registers.
            raise HTTPException(status_code=404, detail="Not Found")
        return _index_response()

    app.include_router(spa_router)
