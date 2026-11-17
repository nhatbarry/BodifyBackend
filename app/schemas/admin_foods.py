"""Admin console: the food catalog."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.admin_catalog import Language, _strip

Basis = Literal["per_100g", "per_serving"]
# auto = follow the duplicate-name rule; shown / hidden = the admin's word.
Visibility = Literal["auto", "shown", "hidden"]

_Macro = Field(default=None, ge=0, le=1000, max_digits=8, decimal_places=2)


class FoodTranslationIn(BaseModel):
    language: Language
    name: str = Field(min_length=1, max_length=200)

    _clean = field_validator("name", mode="before")(_strip)


class FoodTranslationRead(BaseModel):
    language: str
    name: str


class FoodComponentIn(BaseModel):
    name_vi: str = Field(min_length=1, max_length=200)
    grams: Decimal | None = Field(default=None, gt=0, le=10000, max_digits=8, decimal_places=1)

    _clean = field_validator("name_vi", mode="before")(_strip)


class FoodComponentRead(FoodComponentIn):
    source: str


class FoodWrite(BaseModel):
    """The whole food. Translations and components replace what is stored.

    A macro left null stays null - "not published" is not "zero". Energy is
    per 100 g or per serving according to `basis`, like every figure here.
    """

    source: str = Field(default="admin", min_length=1, max_length=16)
    source_code: str | None = Field(default=None, max_length=64)
    food_category_id: int | None = None
    basis: Basis
    serving_grams: Decimal | None = Field(default=None, gt=0, le=10000, max_digits=8, decimal_places=1)
    energy_kcal: Decimal = Field(ge=0, le=10000, max_digits=8, decimal_places=2)
    protein_g: Decimal | None = _Macro
    fat_g: Decimal | None = _Macro
    carb_g: Decimal | None = _Macro
    fiber_g: Decimal | None = _Macro
    nutrients: dict[str, Any] | None = None
    image_key: str | None = Field(default=None, max_length=500)
    visibility: Visibility = "auto"
    translations: list[FoodTranslationIn] = Field(min_length=1)
    components: list[FoodComponentIn] = Field(default_factory=list, max_length=100)

    _clean = field_validator("source", "source_code", "image_key", mode="before")(_strip)

    @model_validator(mode="after")
    def _rules(self) -> "FoodWrite":
        languages = [t.language for t in self.translations]
        if len(languages) != len(set(languages)):
            raise ValueError("Mỗi ngôn ngữ chỉ có một bản dịch")
        if "vi" not in languages:
            # Every catalog read, search and the duplicate rule key on it.
            raise ValueError("Cần có tên tiếng Việt")
        if self.image_key and self.image_key.startswith(("http://", "https://", "/")):
            raise ValueError("image_key là storage key, không phải URL")
        return self


class FoodUsage(BaseModel):
    diary_entries: int


class FoodAdminSummary(BaseModel):
    food_id: int
    name_vi: str | None
    name_en: str | None
    source: str
    source_code: str | None
    basis: str
    energy_kcal: Decimal
    protein_g: Decimal | None
    fat_g: Decimal | None
    carb_g: Decimal | None
    serving_grams: Decimal | None
    category_name: str | None
    is_hidden: bool
    visibility: Visibility
    image_url: str | None
    component_count: int
    updated_at: datetime


class FoodAdminDetail(BaseModel):
    food_id: int
    source: str
    source_code: str | None
    food_category_id: int | None
    category_name: str | None
    basis: str
    serving_grams: Decimal | None
    serving_source: str | None
    energy_kcal: Decimal
    protein_g: Decimal | None
    fat_g: Decimal | None
    carb_g: Decimal | None
    fiber_g: Decimal | None
    nutrients: dict[str, Any] | None
    image_key: str | None
    image_url: str | None
    # Provenance only: the path the picture came from upstream.
    source_image_url: str | None
    is_hidden: bool
    visibility: Visibility
    translations: list[FoodTranslationRead]
    components: list[FoodComponentRead]
    usage: FoodUsage
    created_at: datetime
    updated_at: datetime
