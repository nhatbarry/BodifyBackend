"""Admin console roles (sql_script/ddl_admin_roles.sql).

`permissions` is seeded by the script and mirrors app/core/permissions.py;
nothing in the API writes it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Permission(Base):
    __tablename__ = "permissions"

    permission_key: Mapped[str] = mapped_column(String(40), primary_key=True)
    function_key: Mapped[str] = mapped_column(String(30), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    group_name: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)


class AdminRole(Base):
    __tablename__ = "admin_roles"

    admin_role_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AdminRolePermission(Base):
    __tablename__ = "admin_role_permissions"

    admin_role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_roles.admin_role_id", ondelete="CASCADE"), primary_key=True
    )
    permission_key: Mapped[str] = mapped_column(
        String(40), ForeignKey("permissions.permission_key"), primary_key=True
    )


class UserAdminRole(Base):
    __tablename__ = "user_admin_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    admin_role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("admin_roles.admin_role_id", ondelete="RESTRICT"), primary_key=True
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
