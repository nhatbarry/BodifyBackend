from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EquipmentBase(BaseModel):
    name: str = Field(max_length=100)
    category: str | None = Field(default=None, max_length=50)
    description: str | None = None


class EquipmentCreate(EquipmentBase):
    pass


class EquipmentUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=50)
    description: str | None = None


class EquipmentRead(EquipmentBase):
    model_config = ConfigDict(from_attributes=True)

    equipment_id: int
    created_at: datetime
