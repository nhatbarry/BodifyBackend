from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.api.dependencies import CurrentUserDep, DbSession
from app.models.health_daily import HealthDaily
from app.schemas.health import HealthDayRead, HealthSyncRequest

router = APIRouter(prefix="/health-data", tags=["health-data"])

MAX_RANGE_DAYS = 366

MEASURES = (
    "steps",
    "distance_m",
    "heart_rate_avg",
    "heart_rate_min",
    "heart_rate_max",
)


@router.put("/daily", response_model=list[HealthDayRead])
def sync_daily(
    payload: HealthSyncRequest, current_user: CurrentUserDep, db: DbSession
) -> list[HealthDayRead]:
    """Store the phone's day roll-ups, one upsert per day.

    Re-syncing a day replaces the measures sent and keeps the ones left out, so
    a steps-only sync cannot wipe a heart rate stored earlier. The unique key
    does the merging atomically; two devices syncing at once cannot collide.
    """
    rows = []
    for day in payload.days:
        values = {"user_id": current_user.id, "source": payload.source, **day.model_dump()}
        stmt = insert(HealthDaily).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_health_daily_user_day",
            set_={
                **{m: stmt.excluded[m] for m in MEASURES if values[m] is not None},
                "source": stmt.excluded.source,
                "synced_at": func.now(),
            },
        ).returning(HealthDaily)
        rows.append(db.execute(stmt).scalar_one())
    db.commit()
    rows.sort(key=lambda r: r.day)
    return [HealthDayRead.model_validate(r) for r in rows]


@router.get("/daily", response_model=list[HealthDayRead])
def list_daily(
    current_user: CurrentUserDep,
    db: DbSession,
    date_from: date = Query(..., alias="from"),
    date_to: date = Query(..., alias="to"),
) -> list[HealthDayRead]:
    """Days that have data, oldest first. A missing date means never synced."""
    if date_to < date_from:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "to is before from")
    if (date_to - date_from).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"Range is limited to {MAX_RANGE_DAYS} days"
        )
    rows = db.scalars(
        select(HealthDaily)
        .where(
            HealthDaily.user_id == current_user.id,
            HealthDaily.day >= date_from,
            HealthDaily.day <= date_to,
        )
        .order_by(HealthDaily.day)
    ).all()
    return [HealthDayRead.model_validate(r) for r in rows]
