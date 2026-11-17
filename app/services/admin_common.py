"""Small pieces shared by the admin console's write endpoints."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# Constraint name -> the sentence the console shows. Anything not listed is
# reported generically rather than leaking the database's wording.
_CONSTRAINT_MESSAGES = {
    "muscle_groups_name_key": "Đã có nhóm cơ mang tên này",
    "equipment_name_key": "Đã có thiết bị mang tên này",
    "uq_food_categories_source": "Đã có nhóm thực phẩm với nguồn và mã này",
    "uq_foods_source_code": "Đã có thực phẩm với nguồn và mã này",
    "uq_foods_source": "Đã có thực phẩm với nguồn và mã này",
    "fk_muscle_groups_parent": "Nhóm cơ cha không tồn tại",
}


def not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"Không tìm thấy {what}")


def conflict(message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, message)


@contextmanager
def committing(db: Session) -> Iterator[None]:
    """Commit, turning a constraint violation into a 409 with a readable reason."""
    try:
        yield
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        raise conflict(
            _CONSTRAINT_MESSAGES.get(name or "", "Thay đổi xung đột với dữ liệu hiện có")
        ) from exc
