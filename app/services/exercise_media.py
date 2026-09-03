"""Turning stored media keys into URLs the app can use.

Two sizes exist for every catalog picture:

* `thumbnail` - the original, 800-1200 px wide, ~70 KB. Right for the exercise
  detail header, which draws it full width.
* `icon` - a 256 px webp, ~7 KB. Right for list rows, which draw it at 42-56 pt.

A list that used thumbnails paid ~1.4 MB per screen of twenty rows to render
pictures the size of a postage stamp. Icons bring that to ~120 KB.

`icon_url` is null for any exercise whose icon has not been uploaded, so every
caller must fall back to the thumbnail rather than assume both exist.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.exercise_media import ExerciseMedia
from app.services.storage import presigned_url


def url_or_none(storage_key: str | None) -> str | None:
    """Presigned URL for a media key, or None when there is no media.

    Signing is local computation, so resolving a whole screen's worth of
    pictures costs no network round trips.
    """
    return presigned_url(storage_key) if storage_key else None


def list_image_urls(
    db: Session, exercise_ids: Sequence[int]
) -> dict[int, tuple[str | None, str | None]]:
    """`{exercise_id: (icon_url, thumbnail_url)}` for a batch of exercises.

    One query for the whole batch. Fetched separately from the exercises
    themselves rather than joined: an exercise can hold several media rows,
    which would multiply the exercise rows in the outer query.
    """
    if not exercise_ids:
        return {}

    icons: dict[int, str] = {}
    thumbnails: dict[int, str] = {}

    for exercise_id, media_type, storage_key in db.execute(
        select(
            ExerciseMedia.exercise_id,
            ExerciseMedia.media_type,
            ExerciseMedia.storage_key,
        )
        .where(
            ExerciseMedia.exercise_id.in_(set(exercise_ids)),
            ExerciseMedia.media_type.in_(("icon", "thumbnail")),
        )
        # setdefault below keeps the first row per exercise, so order decides
        # which file wins when an exercise somehow has two of a kind.
        .order_by(ExerciseMedia.exercise_id, ExerciseMedia.sort_order, ExerciseMedia.media_id)
    ).all():
        bucket = icons if media_type == "icon" else thumbnails
        bucket.setdefault(exercise_id, storage_key)

    return {
        exercise_id: (
            url_or_none(icons.get(exercise_id)),
            url_or_none(thumbnails.get(exercise_id)),
        )
        for exercise_id in set(exercise_ids)
    }
