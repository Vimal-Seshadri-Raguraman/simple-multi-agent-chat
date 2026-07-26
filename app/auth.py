from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import SECRET_KEY

ALGORITHM = "HS256"
TOKEN_EXPIRE_SECONDS = 86400

_bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-constant-time-login")


def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(seconds=TOKEN_EXPIRE_SECONDS)
    payload = {"user_id": user_id, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> int:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload["user_id"]


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> int:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return decode_access_token(credentials.credentials)
