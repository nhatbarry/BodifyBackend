from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ExerciseEquipmentCreate(BaseModel):
    exercise_id: int
    equipment_id: int


class ExerciseEquipmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    exercise_equipment_id: int
    exercise_id: int
    equipment_id: int
