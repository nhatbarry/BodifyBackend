from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class BodyMetricCreate(BaseModel):
    height_cm: Decimal | None = Field(default=None, gt=0, max_digits=5, decimal_places=2)
    weight_kg: Decimal | None = Field(default=None, gt=0, max_digits=6, decimal_places=2)
    recorded_at: datetime | None = None


class BodyMetricRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    metric_id: int
    height_cm: Decimal | None
    weight_kg: Decimal | None

    # Derived, never stored: BMI would go stale the moment either input changes.
    bmi: float | None = None

    recorded_at: datetime
    created_at: datetime
