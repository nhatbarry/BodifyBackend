"""Admin console: roles, permissions, and who the caller is."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class PermissionRead(BaseModel):
    permission_key: str
    function_key: str
    action: str
    group_name: str
    label: str


class AdminRoleRef(BaseModel):
    admin_role_id: int
    name: str


class AdminRoleWrite(BaseModel):
    """The whole role: what is sent replaces what is stored."""

    name: str = Field(min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Tên vai trò không được để trống")
        return value

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class AdminRoleMember(BaseModel):
    user_id: uuid.UUID
    email: str | None
    name: str | None


class AdminRoleRead(BaseModel):
    admin_role_id: int
    name: str
    description: str | None
    permissions: list[str]
    member_count: int
    created_at: datetime
    updated_at: datetime


class AdminRoleDetail(AdminRoleRead):
    members: list[AdminRoleMember]


class AdminMe(BaseModel):
    """What the console draws from: the caller's roles and what they allow."""

    user_id: uuid.UUID
    email: str | None
    role: str
    is_superadmin: bool
    roles: list[AdminRoleRef]
    permissions: list[str]


class UserAdminRolesUpdate(BaseModel):
    """Every role the person should hold. Empty removes console access."""

    admin_role_ids: list[int] = Field(default_factory=list, max_length=50)
