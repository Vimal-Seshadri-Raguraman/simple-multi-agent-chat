from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=72)


class UserOut(BaseModel):
    user_id: int
    username: str
