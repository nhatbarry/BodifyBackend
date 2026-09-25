-- ============================================================
-- BODIFY - PHASE 7: one row per food name in the catalog
--
-- The Institute surveyed many dishes in several places ("Cháo lòng" nine
-- times, 258 to 500 kcal) and published some foods twice - once as a cooked
-- dish per serving and once as an ingredient per 100 g ("Bánh bèo"), or with
-- only punctuation or capitals between them ("Gan lợn luộc" / "Gan lợn,
-- luộc"). A picker listing all of them is a wall of identical headings.
--
-- **Hidden, not deleted.** A diary entry logged against a duplicate keeps its
-- link, so it can still be rescaled and its recipe opened; deleting would
-- set those links to NULL for good. Every catalog read filters
-- `is_hidden`; `GET /foods/{id}` does not, for exactly those diary links.
-- The flags are recomputed from scratch on every run, so re-running after a
-- reseed is the whole procedure, and the rule below can be changed and
-- re-applied at will.
--
-- Same name: lower-case, commas and full stops dropped, whitespace collapsed.
-- Diacritics are KEPT - they distinguish real Vietnamese words, the lesson of
-- the barbell/dumbbell filter bug.
--
-- The row kept per name is the most complete one:
--   1. a dish with a published serving weight - loggable in servings AND grams
--   2. the longest ingredient breakdown
--   3. has a picture
--   4. the most macros published (protein, fat, carbohydrate, fibre)
--   5. lowest food_id
--
-- Idempotent. Undo: UPDATE foods SET is_hidden = false;
-- ============================================================

ALTER TABLE foods ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT false;

WITH named AS (
    SELECT
        f.food_id,
        f.basis,
        f.serving_grams,
        f.image_key,
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
    SELECT
        food_id,
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
SET is_hidden = (r.rank > 1)
FROM ranked r
WHERE r.food_id = f.food_id
  AND f.is_hidden IS DISTINCT FROM (r.rank > 1);

CREATE INDEX IF NOT EXISTS idx_foods_visible
    ON foods (food_id) WHERE NOT is_hidden;

DO $$
DECLARE
    n_total  INT;
    n_hidden INT;
BEGIN
    SELECT count(*), count(*) FILTER (WHERE is_hidden) INTO n_total, n_hidden FROM foods;
    RAISE NOTICE 'foods: % total, % shown, % hidden as duplicates',
        n_total, n_total - n_hidden, n_hidden;
END $$;
