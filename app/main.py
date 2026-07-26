import re

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    get_current_user_id,
    hash_password,
    verify_password,
)
from app.database import Base, engine, get_db
from app.models import User
from app.schemas import UserCreate

Base.metadata.create_all(bind=engine)

app = FastAPI()

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"detail": exc.errors()})


@app.post("/register", status_code=201)
def register(user: UserCreate, db: Session = Depends(get_db)):
    if len(user.username) < 3 or not USERNAME_PATTERN.match(user.username):
        raise HTTPException(
            status_code=400,
            detail="Username must be at least 3 characters and contain only letters, numbers, and underscores",
        )
    if len(user.password) < 8:
        raise HTTPException(
            status_code=400, detail="Password must be at least 8 characters"
        )

    existing = db.query(User).filter(User.username == user.username).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Username already exists")

    new_user = User(username=user.username, password_hash=hash_password(user.password))
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {"user_id": new_user.id, "username": new_user.username}


@app.post("/login")
def login(user: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.username == user.username).first()
    if existing is None or not verify_password(user.password, existing.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_access_token(existing.id)
    return {"token": token}


@app.post("/test-auth")
def test_auth(user_id: int = Depends(get_current_user_id)):
    return {"user_id": user_id, "message": "authenticated"}
