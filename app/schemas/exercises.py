from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ExerciseBase(BaseModel):
    exercise_type: str | None = Field(default=None, max_length=50)
    mechanics: str | None = Field(default=None, max_length=50)
    force_type: str | None = Field(default=None, max_length=50)
    experience_level: str | None = Field(default=None, max_length=30)
    overview: str | None = None
    instructions: str | None = None
    tips: str | None = None


class ExerciseCreate(ExerciseBase):
    pass


class ExerciseUpdate(BaseModel):
    exercise_type: str | None = Field(default=None, max_length=50)
    mechanics: str | None = Field(default=None, max_length=50)
    force_type: str | None = Field(default=None, max_length=50)
    experience_level: str | None = Field(default=None, max_length=30)
    overview: str | None = None
    instructions: str | None = None
    tips: str | None = None


class ExerciseRead(ExerciseBase):
    model_config = ConfigDict(from_attributes=True)

    exercise_id: int
    created_at: datetime
    updated_at: datetime
