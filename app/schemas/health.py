from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# A month is the most one request may carry, so a sync cannot become a bulk
# import. The first sync after installing reads two weeks.
MAX_DAYS_PER_SYNC = 31

Source = Literal["health_connect"]


class HealthDayUpsert(BaseModel):
    """One day's roll-up. Omitted or null measures keep what is stored."""

    day: date
    steps: int | None = Field(default=None, ge=0, le=200_000)
    distance_m: Decimal | None = Field(
        default=None, ge=0, le=500_000, max_digits=9, decimal_places=1
    )
    heart_rate_avg: int | None = Field(default=None, ge=20, le=250)
    heart_rate_min: int | None = Field(default=None, ge=20, le=250)
    heart_rate_max: int | None = Field(default=None, ge=20, le=250)

    @model_validator(mode="after")
    def coherent(self) -> "HealthDayUpsert":
        # A day is dated in the phone's timezone, which can be a day ahead of
        # the server's; more than that is a wrong date, not a timezone.
        if self.day > date.today() + timedelta(days=1):
            raise ValueError("day cannot be in the future")
        lo, avg, hi = self.heart_rate_min, self.heart_rate_avg, self.heart_rate_max
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("heart_rate_min cannot exceed heart_rate_max")
        if avg is not None and (
            (lo is not None and avg < lo) or (hi is not None and avg > hi)
        ):
            raise ValueError("heart_rate_avg must lie between min and max")
        return self


class HealthSyncRequest(BaseModel):
    source: Source
    days: list[HealthDayUpsert] = Field(min_length=1, max_length=MAX_DAYS_PER_SYNC)

    @model_validator(mode="after")
    def unique_days(self) -> "HealthSyncRequest":
        if len({d.day for d in self.days}) != len(self.days):
            raise ValueError("each day may appear once")
        return self


class HealthDayRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    day: date
    steps: int | None
    distance_m: Decimal | None
    heart_rate_avg: int | None
    heart_rate_min: int | None
    heart_rate_max: int | None
    source: str
    synced_at: datetime
