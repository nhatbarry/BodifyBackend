from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MediaType = Literal["thumbnail", "video", "image"]


class ExerciseMediaBase(BaseModel):
    media_type: MediaType
    storage_key: str
    sort_order: int = Field(default=0, ge=0)


class ExerciseMediaCreate(ExerciseMediaBase):
    exercise_id: int


class ExerciseMediaUpdate(BaseModel):
    media_type: MediaType | None = None
    storage_key: str | None = None
    sort_order: int | None = Field(default=None, ge=0)


class ExerciseMediaRead(ExerciseMediaBase):
    model_config = ConfigDict(from_attributes=True)

    media_id: int
    exercise_id: int
    created_at: datetime
