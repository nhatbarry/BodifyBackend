from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app.api.dependencies import CurrentUserDep, DbSession
from app.models.body_metrics import BodyMetric
from app.schemas.body_metrics import BodyMetricCreate, BodyMetricRead
from app.schemas.common import Page

router = APIRouter(prefix="/body-metrics", tags=["body-metrics"])


def _bmi(height_cm: Decimal | None, weight_kg: Decimal | None) -> float | None:
    """BMI = kg / m^2, computed on read so it can never disagree with its inputs."""
    if not height_cm or not weight_kg:
        return None
    height_m = float(height_cm) / 100
    if height_m <= 0:
        return None
    return round(float(weight_kg) / (height_m * height_m), 2)


def _to_read(metric: BodyMetric) -> BodyMetricRead:
    result = BodyMetricRead.model_validate(metric)
    result.bmi = _bmi(metric.height_cm, metric.weight_kg)
    return result


@router.post("", response_model=BodyMetricRead, status_code=status.HTTP_201_CREATED)
def create_body_metric(
    payload: BodyMetricCreate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> BodyMetricRead:
    """Record a new measurement.

    user_id comes from the token, never the request body, so one user cannot
    write into another user's history.
    """
    if payload.height_cm is None and payload.weight_kg is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one of height_cm or weight_kg",
        )

    metric = BodyMetric(
        user_id=current_user.id,
        height_cm=payload.height_cm,
        weight_kg=payload.weight_kg,
    )
    # Left unset, the column default records "now".
    if payload.recorded_at is not None:
        metric.recorded_at = payload.recorded_at

    db.add(metric)
    db.commit()
    db.refresh(metric)
    return _to_read(metric)


@router.get("", response_model=Page[BodyMetricRead])
def list_body_metrics(
    current_user: CurrentUserDep,
    db: DbSession,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    date_from: datetime | None = Query(None, description="Recorded at or after this time"),
    date_to: datetime | None = Query(None, description="Recorded at or before this time"),
) -> Page[BodyMetricRead]:
    """The caller's measurement history, newest first."""
    conditions = [BodyMetric.user_id == current_user.id]
    if date_from:
        conditions.append(BodyMetric.recorded_at >= date_from)
    if date_to:
        conditions.append(BodyMetric.recorded_at <= date_to)

    total = db.scalar(select(func.count()).select_from(BodyMetric).where(*conditions))

    rows = db.execute(
        select(BodyMetric)
        .where(*conditions)
        .order_by(BodyMetric.recorded_at.desc(), BodyMetric.metric_id.desc())
        .limit(limit)
        .offset((page - 1) * limit)
    ).scalars().all()

    return Page[BodyMetricRead](
        items=[_to_read(row) for row in rows],
        page=page,
        limit=limit,
        total=total or 0,
    )


# Declared before /{metric_id} so "latest" is not parsed as an id.
@router.get("/latest", response_model=BodyMetricRead)
def get_latest_body_metric(
    current_user: CurrentUserDep,
    db: DbSession,
) -> BodyMetricRead:
    metric = db.execute(
        select(BodyMetric)
        .where(BodyMetric.user_id == current_user.id)
        .order_by(BodyMetric.recorded_at.desc(), BodyMetric.metric_id.desc())
        .limit(1)
    ).scalar_one_or_none()

    if metric is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No body metrics recorded yet"
        )
    return _to_read(metric)


@router.delete("/{metric_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_body_metric(
    metric_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> Response:
    metric = db.execute(
        select(BodyMetric).where(
            BodyMetric.metric_id == metric_id,
            # Scoping to the caller makes someone else's id read as "not found"
            # instead of revealing that the record exists.
            BodyMetric.user_id == current_user.id,
        )
    ).scalar_one_or_none()

    if metric is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Body metric not found"
        )

    db.delete(metric)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
