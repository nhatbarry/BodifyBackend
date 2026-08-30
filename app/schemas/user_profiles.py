from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class UserProfileBase(BaseModel):
    name: str = Field(max_length=150)
    avatar_url: str | None = None
    date_of_birth: date | None = None
    gender: str | None = Field(default=None, max_length=30)


class UserProfileCreate(UserProfileBase):
    user_id: int


class UserProfileUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    avatar_url: str | None = None
    date_of_birth: date | None = None
    gender: str | None = Field(default=None, max_length=30)


class UserProfileRead(UserProfileBase):
    model_config = ConfigDict(from_attributes=True)

    profile_id: int
    user_id: int
    created_at: datetime
    updated_at: datetime
