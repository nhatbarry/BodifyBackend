"""Responses of the admin analytics and operating-cost endpoints."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DateRange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: date = Field(alias="from", serialization_alias="from")
    to: date
    days: int


class Kpi(BaseModel):
    """The range's figure and the figure for the equally long range before it.

    None means there is nothing to measure (no cohort, no finished session),
    which is different from 0.
    """

    value: float | None
    previous: float | None


class OverviewKpis(BaseModel):
    total_users: Kpi
    new_users: Kpi
    dau: Kpi = Field(description="Average daily active accounts over the range")
    wau: Kpi = Field(description="Distinct active accounts in the 7 days ending on `to`")
    mau: Kpi = Field(description="Distinct active accounts in the 30 days ending on `to`")
    workouts_completed: Kpi
    workout_hours: Kpi
    completion_rate: Kpi = Field(description="Completed / (completed + abandoned), by start day, %")
    avg_daily_workouts: Kpi
    retention_d30: Kpi = Field(description="Share of a 30-day sign-up cohort active on day 30-36, %")


class OverviewRead(BaseModel):
    range: DateRange
    previous_range: DateRange
    scope: Literal["all", "real", "seeded"]
    seeded_accounts: int
    kpis: OverviewKpis


class GrowthPoint(BaseModel):
    day: date
    signups: int
    total: int


class ActiveUsersPoint(BaseModel):
    day: date
    dau: int
    wau: int
    mau: int


class WorkoutsPoint(BaseModel):
    day: date
    completed: int
    abandoned: int
    minutes: int
    completion_rate: float | None


class NutritionPoint(BaseModel):
    day: date
    loggers: int
    items_eaten: int
    items_planned: int
    items_from_photo: int


class MuscleShare(BaseModel):
    muscle_id: int
    name: str
    sets: int


class TopExercise(BaseModel):
    exercise_id: int
    name: str | None
    users: int
    sessions: int
    sets: int


class RetentionCohort(BaseModel):
    cohort: date
    size: int
    retained: list[float | None] = Field(
        description="% of the cohort active in month 0, 1, 2… after sign-up; the last may be partial"
    )


class FeatureUsage(BaseModel):
    feature: Literal["coach", "meal_photo", "health_connect", "camera"]
    users: int
    events: int
    share_of_active: float | None


class FeatureWeek(BaseModel):
    week: date
    coach: int
    meal_photo: int
    health_connect: int
    camera: int


class FeatureAdoptionRead(BaseModel):
    active_users: int
    features: list[FeatureUsage]
    weekly: list[FeatureWeek]


class AiUsageDay(BaseModel):
    day: date
    coach_replies: int
    photo_ok: int
    photo_failed: int


class PhotoModelUsage(BaseModel):
    model: str
    ok: int
    failed: int


class AiUsageRead(BaseModel):
    tokens_tracked: bool = Field(
        description="False until Gemini's usage metadata is stored; cost cannot be computed yet"
    )
    configured: bool
    coach_model: str
    vision_model: str
    daily: list[AiUsageDay]
    photo_by_model: list[PhotoModelUsage]


class InfrastructureRead(BaseModel):
    database_bytes: int


class CostLine(BaseModel):
    key: str
    item: str
    quantity: float
    unit: str
    unit_price_usd: float
    price_unit: str
    cost_usd: float
    basis: Literal["measured", "assumed", "plan"]
    note: str


class ProviderCost(BaseModel):
    key: Literal["supabase", "railway", "r2", "gemini"]
    name: str
    plan: str
    used_for: str
    total_usd: float
    lines: list[CostLine]


class CostDay(BaseModel):
    day: date
    supabase: float
    railway: float
    r2: float
    gemini: float
    total: float


class SimulatedAi(BaseModel):
    requests: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class SimulatedInfrastructure(BaseModel):
    r2_storage_gb: float
    railway_vcpu: float
    railway_ram_gb: float
    server_hours: int


class SimulatedCostsRead(BaseModel):
    simulated: bool = Field(description="Always true: every service is on a free plan")
    prices_as_of: str
    usd_to_vnd: int
    days: int
    total_usd: float
    providers: list[ProviderCost]
    daily: list[CostDay]
    ai: SimulatedAi
    infrastructure: SimulatedInfrastructure
