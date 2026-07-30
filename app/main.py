from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.database import init_db
from app.errors import AppError

app = FastAPI(title="Simple Multi-Agent Chat")

init_db()


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
