"""The admin console's permissions: the fixed list the code enforces.

Each key here is checked by at least one admin route (`require_permission`).
The `permissions` table holds the same rows, seeded by
`sql_script/ddl_admin_roles.sql`, so that roles can reference them with a
foreign key and the console can draw its checkbox grid from the database.

The two must agree. `tests/test_admin_roles.py` compares them, and every key
a route asks for must be listed here. A row added to the table without a
route that checks it would be a checkbox that grants nothing.

Managing roles and assigning them is deliberately *not* a permission: whoever
held it could add every other permission to their own role. Only a
superadmin (`user_profiles.role = 'superadmin'`) does that.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDef:
    function_key: str
    action: str
    group_name: str
    label: str

    @property
    def key(self) -> str:
        return f"{self.function_key}.{self.action}"


# Order is the console's display order.
PERMISSIONS: tuple[PermissionDef, ...] = (
    PermissionDef("dashboard", "view", "Tổng quan", "Xem bảng điều khiển"),
    PermissionDef("analytics", "view", "Tổng quan", "Xem phân tích"),
    PermissionDef("costs", "view", "Tổng quan", "Xem chi phí vận hành"),
    PermissionDef("users", "view", "Người dùng", "Xem tài khoản"),
    PermissionDef("users", "ban", "Người dùng", "Khoá / mở khoá tài khoản"),
    PermissionDef("exercises", "view", "Tập luyện", "Xem bài tập, nhóm cơ, thiết bị"),
    PermissionDef("exercises", "create", "Tập luyện", "Thêm bài tập, nhóm cơ, thiết bị"),
    PermissionDef("exercises", "update", "Tập luyện", "Sửa bài tập, nhóm cơ, thiết bị"),
    PermissionDef("exercises", "delete", "Tập luyện", "Xoá bài tập, nhóm cơ, thiết bị"),
    PermissionDef("foods", "view", "Dinh dưỡng", "Xem thực phẩm, nhóm thực phẩm"),
    PermissionDef("foods", "create", "Dinh dưỡng", "Thêm thực phẩm, nhóm thực phẩm"),
    PermissionDef("foods", "update", "Dinh dưỡng", "Sửa thực phẩm, nhóm thực phẩm"),
    PermissionDef("foods", "delete", "Dinh dưỡng", "Xoá thực phẩm, nhóm thực phẩm"),
)

ALL_PERMISSIONS: frozenset[str] = frozenset(p.key for p in PERMISSIONS)

_FUNCTIONS_WITH_VIEW = frozenset(p.function_key for p in PERMISSIONS if p.action == "view")


def with_implied_view(keys: set[str] | frozenset[str]) -> set[str]:
    """Any action on a function implies seeing it: nobody edits what they cannot see."""
    result = set(keys)
    for key in keys:
        function_key = key.split(".", 1)[0]
        if function_key in _FUNCTIONS_WITH_VIEW:
            result.add(f"{function_key}.view")
    return result
