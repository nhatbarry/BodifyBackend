from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

AuthProvider = Literal["local", "google"]


class UserBase(BaseModel):
    email: EmailStr = Field(max_length=255)
    username: str | None = Field(default=None, max_length=100)
    auth_provider: AuthProvider = "local"


class UserCreate(UserBase):
    pass


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, max_length=100)


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    created_at: datetime
    updated_at: datetime
