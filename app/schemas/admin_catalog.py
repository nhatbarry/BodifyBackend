"""Admin console: the catalog's reference data and the shared option lists."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Language = Literal["vi", "en"]


def _strip(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class TermTranslationIn(BaseModel):
    language: Language
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=2000)

    _clean = field_validator("name", "description", mode="before")(_strip)


class TermTranslationRead(BaseModel):
    language: str
    name: str
    description: str | None


def _one_per_language(items: list) -> list:
    seen = [t.language for t in items]
    if len(seen) != len(set(seen)):
        raise ValueError("Mỗi ngôn ngữ chỉ có một bản dịch")
    return items


class MuscleGroupWrite(BaseModel):
    """`name` is the canonical English label the import used and filters
    accept; the screen shows the translations."""

    name: str = Field(min_length=1, max_length=100)
    parent_id: int | None = None
    description: str | None = Field(default=None, max_length=2000)
    translations: list[TermTranslationIn] = Field(default_factory=list)

    _clean = field_validator("name", "description", mode="before")(_strip)
    _langs = field_validator("translations")(_one_per_language)


class MuscleGroupRead(BaseModel):
    muscle_id: int
    name: str
    parent_id: int | None
    parent_name: str | None
    description: str | None
    translations: list[TermTranslationRead]
    exercise_count: int
    child_count: int


class EquipmentWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=2000)
    translations: list[TermTranslationIn] = Field(default_factory=list)

    _clean = field_validator("name", "category", "description", mode="before")(_strip)
    _langs = field_validator("translations")(_one_per_language)


class EquipmentRead(BaseModel):
    equipment_id: int
    name: str
    category: str | None
    description: str | None
    translations: list[TermTranslationRead]
    exercise_count: int


class FoodCategoryWrite(BaseModel):
    """`source` defaults to `admin` for a category made here; the Institute's
    own categories keep `nin` and their code."""

    source: str = Field(default="admin", min_length=1, max_length=16)
    source_code: str = Field(min_length=1, max_length=64)
    name_vi: str = Field(min_length=1, max_length=200)
    name_en: str | None = Field(default=None, max_length=200)

    _clean = field_validator("source", "source_code", "name_vi", "name_en", mode="before")(_strip)


class FoodCategoryRead(BaseModel):
    food_category_id: int
    source: str
    source_code: str
    name_vi: str
    name_en: str | None
    food_count: int


class OptionItem(BaseModel):
    id: int
    name: str
    name_vi: str | None = None
    name_en: str | None = None


class CatalogOptions(BaseModel):
    muscle_groups: list[OptionItem]
    equipment: list[OptionItem]
    food_categories: list[OptionItem]
    # Free-text columns on `exercises`, as they are used today, most used first.
    exercise_types: list[str]
    mechanics: list[str]
    force_types: list[str]
    experience_levels: list[str]
    tracking_types: list[str]
    ai_algorithms: list[str]
    media_types: list[str]
    food_sources: list[str]
