from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Bounds wide enough for any real person and narrow enough to catch a slip of
# the finger. The tallest and shortest adults ever recorded sit comfortably
# inside these, as does every newborn; 6776 kg does not.
#
# These matter more than they look. BMI is computed from them, the AI work in
# Phase 4 will train on them, and a single fat-fingered 6776 kg poisons every
# average it lands in. The column alone only caps the digit count, so without
# this the API accepted it.
MIN_HEIGHT_CM = Decimal("30")
MAX_HEIGHT_CM = Decimal("280")
MIN_WEIGHT_KG = Decimal("2")
MAX_WEIGHT_KG = Decimal("650")

# A measurement can be backdated - people catch up on a week of weigh-ins - but
# a future one is a typo in the date, not a reading.
FUTURE_TOLERANCE = timedelta(minutes=5)


class BodyMetricCreate(BaseModel):
    height_cm: Decimal | None = Field(
        default=None,
        ge=MIN_HEIGHT_CM,
        le=MAX_HEIGHT_CM,
        max_digits=5,
        decimal_places=2,
        description=f"Between {MIN_HEIGHT_CM} and {MAX_HEIGHT_CM} cm",
    )
    weight_kg: Decimal | None = Field(
        default=None,
        ge=MIN_WEIGHT_KG,
        le=MAX_WEIGHT_KG,
        max_digits=6,
        decimal_places=2,
        description=f"Between {MIN_WEIGHT_KG} and {MAX_WEIGHT_KG} kg",
    )
    recorded_at: datetime | None = None

    @field_validator("recorded_at")
    @classmethod
    def not_in_the_future(cls, value: datetime | None) -> datetime | None:
        """A few minutes of slack absorbs clock skew between phone and server;
        anything beyond that is a mistyped date."""
        if value is None:
            return value
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if moment > datetime.now(timezone.utc) + FUTURE_TOLERANCE:
            raise ValueError("recorded_at cannot be in the future")
        return value


class BodyMetricRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    metric_id: int
    height_cm: Decimal | None
    weight_kg: Decimal | None

    # Derived, never stored: BMI would go stale the moment either input changes.
    bmi: float | None = None

    recorded_at: datetime
    created_at: datetime
