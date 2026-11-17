"""Admin console: the exercise catalog."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.admin_catalog import Language, _strip

TrackingType = Literal["reps", "duration", "distance", "reps_duration"]
MediaType = Literal["thumbnail", "icon", "video", "image"]


class ExerciseTranslationIn(BaseModel):
    language: Language
    name: str = Field(min_length=1, max_length=255)
    overview: str | None = None
    instructions: str | None = None
    tips: str | None = None

    _clean = field_validator("name", "overview", "instructions", "tips", mode="before")(_strip)


class ExerciseTranslationRead(BaseModel):
    language: str
    name: str
    overview: str | None
    instructions: str | None
    tips: str | None


class ExerciseMuscleIn(BaseModel):
    muscle_id: int
    role: Literal["primary", "secondary"] = "primary"


class ExerciseMuscleRead(ExerciseMuscleIn):
    name: str


class ExerciseEquipmentRead(BaseModel):
    equipment_id: int
    name: str


class ExerciseMediaIn(BaseModel):
    """A key inside the catalog bucket, without the old `media/` prefix
    (`abductors/cable-hip-abduction/...`). There is no upload endpoint: the
    file has to be in the bucket already, or the app gets a URL that 404s."""

    media_type: MediaType
    storage_key: str = Field(min_length=3, max_length=500)
    sort_order: int = Field(default=0, ge=0, le=1000)

    @field_validator("storage_key", mode="before")
    @classmethod
    def _key(cls, value: str) -> str:
        value = (value or "").strip()
        if value.startswith(("http://", "https://")):
            raise ValueError("Hãy nhập storage key, không phải URL")
        if value.startswith("/") or value.startswith("media/"):
            raise ValueError("Storage key không bắt đầu bằng '/' hay 'media/'")
        return value


class ExerciseMediaRead(ExerciseMediaIn):
    media_id: int
    url: str | None


class ExerciseWrite(BaseModel):
    """The whole exercise. Lists replace what is stored: a muscle, piece of
    equipment, media row or translation left out is removed.

    `ai_algorithm` is deliberately absent: which exercises the camera can
    count is decided by code shipped in the app (`setupGuideFor`), and a value
    set here that the installed app does not know would advertise a feature
    it does not have."""

    exercise_type: str | None = Field(default=None, max_length=50)
    mechanics: str | None = Field(default=None, max_length=50)
    force_type: str | None = Field(default=None, max_length=50)
    experience_level: str | None = Field(default=None, max_length=30)
    tracking_type: TrackingType = "reps"
    translations: list[ExerciseTranslationIn] = Field(min_length=1)
    muscles: list[ExerciseMuscleIn] = Field(default_factory=list)
    equipment_ids: list[int] = Field(default_factory=list)
    media: list[ExerciseMediaIn] = Field(default_factory=list)

    _clean = field_validator(
        "exercise_type", "mechanics", "force_type", "experience_level", mode="before"
    )(_strip)

    @model_validator(mode="after")
    def _unique(self) -> "ExerciseWrite":
        languages = [t.language for t in self.translations]
        if len(languages) != len(set(languages)):
            raise ValueError("Mỗi ngôn ngữ chỉ có một bản dịch")
        muscles = [m.muscle_id for m in self.muscles]
        if len(muscles) != len(set(muscles)):
            raise ValueError("Mỗi nhóm cơ chỉ được chọn một lần")
        if len(self.equipment_ids) != len(set(self.equipment_ids)):
            raise ValueError("Mỗi thiết bị chỉ được chọn một lần")
        return self


class ExerciseUsage(BaseModel):
    """Why a delete would be refused, counted."""

    plan_exercises: int
    session_exercises: int
    personal_records: int
    achievements: int


class ExerciseAdminSummary(BaseModel):
    exercise_id: int
    name_vi: str | None
    name_en: str | None
    exercise_type: str | None
    tracking_type: str
    experience_level: str | None
    ai_algorithm: str | None
    primary_muscles: list[str]
    equipment: list[str]
    icon_url: str | None
    updated_at: datetime


class ExerciseAdminDetail(BaseModel):
    exercise_id: int
    exercise_type: str | None
    mechanics: str | None
    force_type: str | None
    experience_level: str | None
    tracking_type: str
    ai_algorithm: str | None
    translations: list[ExerciseTranslationRead]
    muscles: list[ExerciseMuscleRead]
    equipment: list[ExerciseEquipmentRead]
    media: list[ExerciseMediaRead]
    usage: ExerciseUsage
    created_at: datetime
    updated_at: datetime
