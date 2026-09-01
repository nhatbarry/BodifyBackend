from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.dependencies import DbSession
from app.models.muscle_groups import MuscleGroup
from app.schemas.muscle_groups import MuscleGroupRead

router = APIRouter(prefix="/muscle-groups", tags=["muscle-groups"])


@router.get("", response_model=list[MuscleGroupRead])
def list_muscle_groups(
    db: DbSession,
    parent_id: int | None = Query(None, description="Only children of this group"),
    top_level: bool = Query(False, description="Only groups with no parent"),
) -> list[MuscleGroup]:
    """Muscle groups for filter dropdowns."""
    stmt = select(MuscleGroup)
    if top_level:
        stmt = stmt.where(MuscleGroup.parent_id.is_(None))
    elif parent_id is not None:
        stmt = stmt.where(MuscleGroup.parent_id == parent_id)

    return list(db.execute(stmt.order_by(MuscleGroup.name)).scalars().all())
