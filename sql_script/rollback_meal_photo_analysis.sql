-- Undo ddl_meal_photo_analysis.sql. Refuses while any analysis exists: the
-- rows record requests that were made, and meal items point at them.
DO $$
BEGIN
    IF to_regclass('public.meal_photo_analyses') IS NOT NULL
       AND EXISTS (SELECT 1 FROM meal_photo_analyses) THEN
        RAISE EXCEPTION 'meal_photo_analyses holds rows; refusing to drop';
    END IF;
END $$;

DROP INDEX IF EXISTS idx_meal_items_photo_analysis;
ALTER TABLE meal_items DROP COLUMN IF EXISTS photo_analysis_id;
DROP TABLE IF EXISTS meal_photo_analyses;
