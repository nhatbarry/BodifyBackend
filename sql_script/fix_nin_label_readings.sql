-- Corrects ingredient rows the label OCR misread, checked against the card.
--
-- The OCR pass only cross-checked a card's total energy against the dish, so
-- a misread line survived whenever the kcal total was read right. Two found
-- by using the app (2026-10-22), each confirmed against the image in R2:
--
--   Bánh bèo  (catalog/food/nin/f9ac1f495fe69d14.webp)
--     Rau mùi    77.9 g -> 1.0 g   (77.9 is Chả quế's kcal, one column over)
--     Ớt đỏ       1.0 g -> 0.5 g   ("Ớt  đỏ" also had a double space)
--   Phở xào   (catalog/food/nin/e262886f3a96a8bb.webp)
--     Hành tây  132.0 g -> 24.0 g  (132.0 is Bánh phở's line, read twice)
--     "Cànb tay"        -> "Cần tây"
--     "Bột đậu"         -> "Bột đao"
--
-- serving_grams was the sum of those lines, so it is recomputed from them:
-- 408.9 -> 331.5 g and 423.7 -> 315.7 g. Energy per serving was right all
-- along and is untouched. Diary entries keep the grams they were logged with
-- (meal_items is a snapshot), so nothing already eaten changes.
--
-- The same fix is in data/nin/label_readings.json, so a reseed through
-- scripts/extract_nin_servings.py --write does not bring the errors back.
-- Idempotent: every statement sets a value rather than adjusting one.
-- Undo: the old values are in the comments above.

BEGIN;

UPDATE food_components c SET name_vi = 'Rau mùi', grams = 1.0
FROM foods f
WHERE c.food_id = f.food_id
  AND f.image_key = 'catalog/food/nin/f9ac1f495fe69d14.webp'
  AND c.name_vi = 'Rau mùi';

UPDATE food_components c SET name_vi = 'Ớt đỏ', grams = 0.5
FROM foods f
WHERE c.food_id = f.food_id
  AND f.image_key = 'catalog/food/nin/f9ac1f495fe69d14.webp'
  AND c.name_vi IN ('Ớt  đỏ', 'Ớt đỏ');

UPDATE food_components c SET grams = 24.0
FROM foods f
WHERE c.food_id = f.food_id
  AND f.image_key = 'catalog/food/nin/e262886f3a96a8bb.webp'
  AND c.name_vi = 'Hành tây';

UPDATE food_components c SET name_vi = 'Cần tây'
FROM foods f
WHERE c.food_id = f.food_id
  AND f.image_key = 'catalog/food/nin/e262886f3a96a8bb.webp'
  AND c.name_vi IN ('Cànb tay', 'Cần tây');

UPDATE food_components c SET name_vi = 'Bột đao'
FROM foods f
WHERE c.food_id = f.food_id
  AND f.image_key = 'catalog/food/nin/e262886f3a96a8bb.webp'
  AND c.name_vi IN ('Bột đậu', 'Bột đao');

UPDATE foods f SET serving_grams = s.total, updated_at = now()
FROM (
    SELECT c.food_id, sum(c.grams) AS total
    FROM food_components c
    JOIN foods f2 ON f2.food_id = c.food_id
    WHERE f2.image_key IN ('catalog/food/nin/f9ac1f495fe69d14.webp',
                           'catalog/food/nin/e262886f3a96a8bb.webp')
    GROUP BY c.food_id
) s
WHERE f.food_id = s.food_id
  AND f.serving_source = 'nin_label';

COMMIT;
