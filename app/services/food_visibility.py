"""Which foods the catalog shows: one row per name, unless an admin said otherwise.

The same rule as `sql_script/hide_duplicate_foods.sql`, kept here because
the image does not ship `sql_script/`. **Change both together.** Run after
every admin write to a food, so a new "Phở bò" that is more complete than
the old one takes its place at once, and putting a row back on "auto" gives
it the rule's answer instead of leaving the last override standing.

About 2100 rows; tens of milliseconds.
"""

from __future__ import annotations

import unicodedata

from sqlalchemy import text
from sqlalchemy.orm import Session

_RECOMPUTE = text(
    """
    WITH named AS (
        SELECT
            f.food_id, f.basis, f.serving_grams, f.image_key,
            num_nonnulls(f.protein_g, f.fat_g, f.carb_g, f.fiber_g) AS macros,
            (SELECT count(*) FROM food_components c WHERE c.food_id = f.food_id) AS components,
            lower(btrim(regexp_replace(
                coalesce(
                    (SELECT t.name FROM food_translations t
                     WHERE t.food_id = f.food_id AND t.language_code = 'vi'),
                    (SELECT t.name FROM food_translations t
                     WHERE t.food_id = f.food_id ORDER BY t.language_code LIMIT 1)
                ),
                '[[:space:],.]+', ' ', 'g'
            ))) AS norm
        FROM foods f
    ),
    ranked AS (
        SELECT food_id,
               row_number() OVER (
                   PARTITION BY norm
                   ORDER BY
                       (basis = 'per_serving' AND serving_grams IS NOT NULL) DESC,
                       components DESC,
                       (image_key IS NOT NULL) DESC,
                       macros DESC,
                       food_id
               ) AS rank
        FROM named
        WHERE norm IS NOT NULL
    )
    UPDATE foods f
    SET is_hidden = coalesce(f.hidden_override, r.rank > 1)
    FROM ranked r
    WHERE r.food_id = f.food_id
      AND f.is_hidden IS DISTINCT FROM coalesce(f.hidden_override, r.rank > 1)
    """
)


def recompute(db: Session) -> None:
    db.execute(_RECOMPUTE)


def strip_accents(value: str) -> str:
    """`name_ascii`, exactly as `scripts/seed_nin_food.py` writes it."""
    decomposed = unicodedata.normalize("NFD", value.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower()
