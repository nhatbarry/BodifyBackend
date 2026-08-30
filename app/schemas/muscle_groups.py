from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MuscleGroupBase(BaseModel):
    name: str = Field(max_length=100)
    parent_id: int | None = None
    description: str | None = None


class MuscleGroupCreate(MuscleGroupBase):
    pass


class MuscleGroupUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    parent_id: int | None = None
    description: str | None = None


class MuscleGroupRead(MuscleGroupBase):
    model_config = ConfigDict(from_attributes=True)

    muscle_id: int
    created_at: datetime
