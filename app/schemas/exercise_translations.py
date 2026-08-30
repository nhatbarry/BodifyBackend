from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ExerciseTranslationBase(BaseModel):
    language: str = Field(max_length=10)
    name: str = Field(max_length=255)
    overview: str | None = None
    instructions: str | None = None
    tips: str | None = None


class ExerciseTranslationCreate(ExerciseTranslationBase):
    exercise_id: int


class ExerciseTranslationUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    overview: str | None = None
    instructions: str | None = None
    tips: str | None = None


class ExerciseTranslationRead(ExerciseTranslationBase):
    model_config = ConfigDict(from_attributes=True)

    translation_id: int
    exercise_id: int
    created_at: datetime
    updated_at: datetime
