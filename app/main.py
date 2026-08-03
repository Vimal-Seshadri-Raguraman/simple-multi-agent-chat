from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.database import init_db
from app.errors import AppError
from app.routers import (
    auth,
    channels,
    invites,
    members,
    mentions,
    messages,
    unreads,
    websocket,
    workspaces,
)
from app.schemas import MetaOut

#: Bumped independently of `server_version` only when the wire contract
#: itself changes; the TUI's /meta handshake (spec Decision 6) compares
#: both so a mismatched client can tell "newer server" from "incompatible
#: server" apart.
API_VERSION = 1


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Simple Multi-Agent Chat", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(members.router)
app.include_router(mentions.router)
app.include_router(unreads.router)
app.include_router(invites.router)
app.include_router(workspaces.router)
app.include_router(channels.router)
app.include_router(messages.router)
app.include_router(websocket.router)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(IntegrityError)
async def integrity_error_handler(
    request: Request, exc: IntegrityError
) -> JSONResponse:
    """A database constraint fired that the request-level checks didn't catch.

    Happens only when two requests race past the same SELECT-then-INSERT
    check; the UNIQUE constraint is the backstop. The message is generic on
    purpose — DB error text would leak schema details.
    """
    return JSONResponse(
        status_code=409,
        content={
            "error": {
                "code": "conflict",
                "message": "The request conflicts with existing data",
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "invalid_message", "message": str(exc.errors())}},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "http_error", "message": exc.detail}},
    )


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/meta", response_model=MetaOut)
def get_meta() -> MetaOut:
    """Unauthenticated version handshake for TUI/client compatibility checks."""
    return MetaOut(server_version=__version__, api_version=API_VERSION)
