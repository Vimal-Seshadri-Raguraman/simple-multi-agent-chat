import os
from typing import Mapping, Optional

from dotenv import load_dotenv

load_dotenv()

DEV_SECRET_KEY = "dev-secret-key-insecure-do-not-use-in-production"


def resolve_secret_key(env: Optional[Mapping[str, str]] = None) -> str:
    env = env if env is not None else os.environ
    secret_key = env.get("SECRET_KEY")
    if secret_key:
        return secret_key
    if env.get("ENVIRONMENT") == "production":
        raise RuntimeError("SECRET_KEY environment variable must be set in production")
    return DEV_SECRET_KEY


ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./smac.db")
SECRET_KEY = resolve_secret_key()
