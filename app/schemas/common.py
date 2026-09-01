from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Page-numbered list response, as used across the Phase 1 spec."""

    items: list[T]
    page: int
    limit: int
    total: int
