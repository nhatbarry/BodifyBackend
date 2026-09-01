from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.dependencies import DbSession
from app.models.equipment import Equipment
from app.schemas.equipment import EquipmentRead

router = APIRouter(prefix="/equipment", tags=["equipment"])


@router.get("", response_model=list[EquipmentRead])
def list_equipment(
    db: DbSession,
    category: str | None = Query(None, description="Filter by category"),
) -> list[Equipment]:
    """Equipment for filter dropdowns."""
    stmt = select(Equipment)
    if category:
        stmt = stmt.where(func.lower(Equipment.category) == category.lower())

    return list(db.execute(stmt.order_by(Equipment.name)).scalars().all())
