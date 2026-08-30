from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

MuscleRole = Literal["primary", "secondary"]


class ExerciseMuscleBase(BaseModel):
    role: MuscleRole = "primary"


class ExerciseMuscleCreate(ExerciseMuscleBase):
    exercise_id: int
    muscle_id: int


class ExerciseMuscleUpdate(BaseModel):
    role: MuscleRole | None = None


class ExerciseMuscleRead(ExerciseMuscleBase):
    model_config = ConfigDict(from_attributes=True)

    exercise_muscle_id: int
    exercise_id: int
    muscle_id: int
