from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScheduleCreate(BaseModel):
    """Exactly one of scheduled_date / day_of_week, matching the DB's
    exclusive arc. A date is a one-off session; a weekday repeats."""

    workout_day_id: int
    scheduled_date: date | None = None
    day_of_week: int | None = Field(default=None, ge=1, le=7)
    is_active: bool = True

    @model_validator(mode="after")
    def exactly_one_target(self) -> "ScheduleCreate":
        if (self.scheduled_date is None) == (self.day_of_week is None):
            raise ValueError(
                "Provide exactly one of scheduled_date or day_of_week"
            )
        return self


class ScheduleUpdate(BaseModel):
    """Switching a schedule between one-off and recurring means clearing one
    column while setting the other, so both are validated together here rather
    than left to the database's CHECK."""

    workout_day_id: int | None = None
    scheduled_date: date | None = None
    day_of_week: int | None = Field(default=None, ge=1, le=7)
    is_active: bool | None = None

    @model_validator(mode="after")
    def not_both_targets(self) -> "ScheduleUpdate":
        fields = self.model_fields_set
        if "scheduled_date" in fields and "day_of_week" in fields:
            if (self.scheduled_date is None) == (self.day_of_week is None):
                raise ValueError(
                    "Provide exactly one of scheduled_date or day_of_week"
                )
        return self


class ScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    schedule_id: int
    workout_day_id: int
    scheduled_date: date | None
    # ISO weekday: 1 = Monday .. 7 = Sunday.
    day_of_week: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    # Denormalised for display, so the schedule list does not need a second call.
    workout_day_name: str | None = None
    plan_id: int | None = None
    plan_name: str | None = None
    is_rest_day: bool | None = None


class CalendarItem(BaseModel):
    """One real date, with recurrence already expanded by the server."""

    date: date
    schedule_id: int
    workout_day_id: int
    title: str
    is_rest_day: bool
    plan_id: int
    plan_name: str


class CalendarResponse(BaseModel):
    """Only dates that actually have a schedule row appear in `items`.

    That gives the client three distinguishable states:
      - in items, is_rest_day false -> training
      - in items, is_rest_day true  -> a planned rest day
      - absent                      -> nothing scheduled yet
    """

    from_date: date = Field(serialization_alias="from")
    to_date: date = Field(serialization_alias="to")
    items: list[CalendarItem]

    model_config = ConfigDict(populate_by_name=True)
