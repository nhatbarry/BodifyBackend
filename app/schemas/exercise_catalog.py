from __future__ import annotations

from pydantic import BaseModel


class ExerciseMuscleItem(BaseModel):
    id: int
    name: str
    role: str


class ExerciseEquipmentItem(BaseModel):
    id: int
    name: str


class ExerciseMediaUrls(BaseModel):
    """Media as the app consumes it: ready-to-use URLs, not storage keys."""

    thumbnail_url: str | None = None
    video_url: str | None = None


class ExerciseListItem(BaseModel):
    exercise_id: int

    # Names live only in exercise_translations, so this is null for an exercise
    # with no translation at all.
    name: str | None = None
    language: str | None = None

    thumbnail_url: str | None = None

    exercise_type: str | None = None
    mechanics: str | None = None
    force_type: str | None = None
    experience_level: str | None = None


class ExerciseDetail(BaseModel):
    exercise_id: int

    name: str | None = None
    # Which translation row supplied the text; null means the untranslated
    # columns on `exercises` were used.
    language: str | None = None

    overview: str | None = None
    instructions: str | None = None
    tips: str | None = None

    exercise_type: str | None = None
    mechanics: str | None = None
    force_type: str | None = None
    experience_level: str | None = None

    muscles: list[ExerciseMuscleItem] = []
    equipment: list[ExerciseEquipmentItem] = []
    media: ExerciseMediaUrls = ExerciseMediaUrls()


class FilterOption(BaseModel):
    id: int
    name: str


class ExerciseFilters(BaseModel):
    """Everything the client needs to build the filter UI, in one call."""

    muscle_groups: list[FilterOption]
    equipment: list[FilterOption]
    experience_levels: list[str]
    exercise_types: list[str]
    mechanics: list[str]
    force_types: list[str]
