"""Resolving a catalog exercise's display name.

Shared by the Phase 1 explorer and the Phase 2 planner. Kept in one place on
purpose: two copies of the fallback rule would drift, and a plan that resolved
names differently from the explorer would show a user two names for one
exercise.
"""

from __future__ import annotations

from sqlalchemy import case, func, select

from app.models.exercise_translations import ExerciseTranslation

DEFAULT_LANG = "vi"


def best_translation(language: str):
    """One translation row per exercise: requested language, else English, else any.

    Exercise names exist only in `exercise_translations` - the `exercises`
    table has no name column - so without this every listed row is nameless.

    Both vi and en cover 703 of the 704 catalog rows, so the fallback rarely
    fires; the one exercise with no translation at all still yields no name,
    which is why callers must treat `name` as nullable.
    """
    preference = case(
        (ExerciseTranslation.language == language, 0),
        (ExerciseTranslation.language == "en", 1),
        else_=2,
    )
    ranked = select(
        ExerciseTranslation.exercise_id.label("exercise_id"),
        ExerciseTranslation.language.label("language"),
        ExerciseTranslation.name.label("name"),
        ExerciseTranslation.overview.label("overview"),
        ExerciseTranslation.instructions.label("instructions"),
        ExerciseTranslation.tips.label("tips"),
        func.row_number()
        .over(
            partition_by=ExerciseTranslation.exercise_id,
            order_by=(preference.asc(), ExerciseTranslation.language.asc()),
        )
        .label("rn"),
    ).subquery()

    return select(ranked).where(ranked.c.rn == 1).subquery()
