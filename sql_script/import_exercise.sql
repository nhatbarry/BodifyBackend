-- ============================================================
-- BODIFY - IMPORT EXERCISES V2 (DBEAVER)
-- Dataset: all_exercises.csv
-- Rows in source file: 704
--
-- IMPORTANT:
-- You already added exercises.source_url + unique index:
--   ALTER TABLE exercises ADD COLUMN source_url TEXT;
--   CREATE UNIQUE INDEX uq_exercises_source_url
--   ON exercises(source_url) WHERE source_url IS NOT NULL;
--
-- WORKFLOW:
-- 1. Run SECTION A only.
-- 2. DBeaver -> right click staging_exercises -> Import Data -> CSV.
-- 3. Import all_exercises.csv.
-- 4. Verify staging count = 704.
-- 5. Run SECTION B, C, D, E, F, G, H separately or together.
--
-- NO BEGIN/COMMIT is used intentionally, so one failed statement
-- does not poison the whole DBeaver session transaction.
-- ============================================================


-- ============================================================
-- SECTION A - STAGING TABLE
-- ============================================================

DROP TABLE IF EXISTS staging_exercises;

CREATE TABLE staging_exercises (
    "muscle_group" TEXT,
    "name" TEXT,
    "source_url" TEXT,
    "video_type" TEXT,
    "video_url" TEXT,
    "video_path" TEXT,
    "thumbnail_url" TEXT,
    "thumbnail_path" TEXT,
    "youtube_video_id" TEXT,
    "vimeo_video_id" TEXT,
    "target_muscle_group" TEXT,
    "exercise_type" TEXT,
    "equipment_required" TEXT,
    "mechanics" TEXT,
    "force_type" TEXT,
    "experience_level" TEXT,
    "secondary_muscles" TEXT,
    "overview" TEXT,
    "instructions" TEXT,
    "tips" TEXT,
    "scraped_at" TEXT
);


-- ============================================================
-- STOP.
-- Import CSV into staging_exercises using DBeaver.
--
-- File:
-- D:/BodifyBackend/sql_script/all_exercises.csv
--
-- Settings:
--   Format: CSV
--   Header: Yes
--   Delimiter: ,
--   Quote: "
--   Encoding: UTF-8
--
-- Expected: 704 rows
-- ============================================================


-- ============================================================
-- SECTION B - VALIDATE SOURCE
-- ============================================================

SELECT COUNT(*) AS staging_rows
FROM staging_exercises;


-- Check duplicate source URLs in CSV
SELECT
    LOWER(TRIM(NULLIF(TRIM(s."source_url"), ''))) AS source_url,
    COUNT(*) AS cnt
FROM staging_exercises s
WHERE NULLIF(TRIM(s."source_url"), '') IS NOT NULL
GROUP BY LOWER(TRIM(NULLIF(TRIM(s."source_url"), '')))
HAVING COUNT(*) > 1
ORDER BY cnt DESC;


-- ============================================================
-- SECTION C - MUSCLE GROUPS
-- ============================================================

INSERT INTO muscle_groups (name)
SELECT DISTINCT TRIM(x.muscle_name)
FROM (
    SELECT NULLIF(TRIM(s."muscle_group"), '') AS muscle_name
    FROM staging_exercises s

    UNION

    SELECT NULLIF(TRIM(s."target_muscle_group"), '') AS muscle_name
    FROM staging_exercises s

    UNION

    SELECT TRIM(value) AS muscle_name
    FROM staging_exercises s
    CROSS JOIN LATERAL regexp_split_to_table(
        COALESCE(NULLIF(TRIM(s."secondary_muscles"), ''), ''),
        '\s*,\s*|\s*;\s*|\s*\|\s*'
    ) AS value
) x
WHERE x.muscle_name IS NOT NULL
  AND x.muscle_name <> ''
ON CONFLICT (name) DO NOTHING;


-- ============================================================
-- SECTION D - EQUIPMENT
-- ============================================================

INSERT INTO equipment (name)
SELECT DISTINCT TRIM(value)
FROM staging_exercises s
CROSS JOIN LATERAL regexp_split_to_table(
    COALESCE(NULLIF(TRIM(s."equipment_required"), ''), ''),
    '\s*,\s*|\s*;\s*|\s*\|\s*'
) AS value
WHERE TRIM(value) <> ''
ON CONFLICT (name) DO NOTHING;


-- ============================================================
-- SECTION E - EXERCISES
-- ============================================================
-- source_url is now the stable identity.
--
-- Existing source_url -> update canonical exercise fields.
-- New source_url -> insert.
-- Rows without source_url are matched by EN name only as fallback.

UPDATE exercises e
SET
    exercise_type = NULLIF(TRIM(s."exercise_type"), ''),
    mechanics = NULLIF(TRIM(s."mechanics"), ''),
    force_type = NULLIF(TRIM(s."force_type"), ''),
    experience_level = NULLIF(TRIM(s."experience_level"), ''),
    overview = NULLIF(TRIM(s."overview"), ''),
    instructions = NULLIF(TRIM(s."instructions"), ''),
    tips = NULLIF(TRIM(s."tips"), ''),
    updated_at = NOW()
FROM staging_exercises s
WHERE NULLIF(TRIM(s."source_url"), '') IS NOT NULL
  AND LOWER(TRIM(e.source_url)) = LOWER(TRIM(NULLIF(TRIM(s."source_url"), '')));


INSERT INTO exercises (
    source_url,
    exercise_type,
    mechanics,
    force_type,
    experience_level,
    overview,
    instructions,
    tips
)
SELECT
    TRIM(NULLIF(TRIM(s."source_url"), '')),
    NULLIF(TRIM(s."exercise_type"), ''),
    NULLIF(TRIM(s."mechanics"), ''),
    NULLIF(TRIM(s."force_type"), ''),
    NULLIF(TRIM(s."experience_level"), ''),
    NULLIF(TRIM(s."overview"), ''),
    NULLIF(TRIM(s."instructions"), ''),
    NULLIF(TRIM(s."tips"), '')
FROM staging_exercises s
WHERE NULLIF(TRIM(s."source_url"), '') IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM exercises e
      WHERE LOWER(TRIM(e.source_url)) = LOWER(TRIM(NULLIF(TRIM(s."source_url"), '')))
  );


-- ============================================================
-- SECTION F - EN TRANSLATIONS
-- ============================================================

-- Update existing EN translation.
UPDATE exercise_translations et
SET
    name = TRIM(NULLIF(TRIM(s."name"), '')),
    overview = NULLIF(TRIM(s."overview"), ''),
    instructions = NULLIF(TRIM(s."instructions"), ''),
    tips = NULLIF(TRIM(s."tips"), ''),
    updated_at = NOW()
FROM staging_exercises s
JOIN exercises e
  ON LOWER(TRIM(e.source_url)) = LOWER(TRIM(NULLIF(TRIM(s."source_url"), '')))
WHERE et.exercise_id = e.exercise_id
  AND et.language = 'en';


-- Insert missing EN translation.
INSERT INTO exercise_translations (
    exercise_id,
    language,
    name,
    overview,
    instructions,
    tips
)
SELECT
    e.exercise_id,
    'en',
    TRIM(NULLIF(TRIM(s."name"), '')),
    NULLIF(TRIM(s."overview"), ''),
    NULLIF(TRIM(s."instructions"), ''),
    NULLIF(TRIM(s."tips"), '')
FROM staging_exercises s
JOIN exercises e
  ON LOWER(TRIM(e.source_url)) = LOWER(TRIM(NULLIF(TRIM(s."source_url"), '')))
WHERE NULLIF(TRIM(s."name"), '') IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM exercise_translations et
      WHERE et.exercise_id = e.exercise_id
        AND et.language = 'en'
  );


-- ============================================================
-- SECTION G - EXERCISE -> MUSCLE
-- ============================================================

-- Remove existing mappings for imported exercises
DELETE FROM exercise_muscles em
USING exercises e
WHERE em.exercise_id = e.exercise_id
  AND EXISTS (
      SELECT 1
      FROM staging_exercises s
      WHERE NULLIF(TRIM(s.source_url), '') IS NOT NULL
        AND LOWER(TRIM(e.source_url)) = LOWER(TRIM(s.source_url))
  );


-- Rebuild muscle mappings.
--
-- If the same muscle appears as both primary and secondary,
-- keep PRIMARY.
--
-- UNIQUE key:
--     (exercise_id, muscle_id)

INSERT INTO exercise_muscles (
    exercise_id,
    muscle_id,
    role
)
SELECT DISTINCT ON (e.exercise_id, mg.muscle_id)
    e.exercise_id,
    mg.muscle_id,
    x.role
FROM staging_exercises s

JOIN exercises e
    ON LOWER(TRIM(e.source_url))
     = LOWER(TRIM(s.source_url))

CROSS JOIN LATERAL (

    -- Primary muscle
    SELECT
        TRIM(s.muscle_group) AS muscle_name,
        'primary' AS role
    WHERE NULLIF(TRIM(s.muscle_group), '') IS NOT NULL

    UNION ALL

    -- Target muscle
    SELECT
        TRIM(s.target_muscle_group) AS muscle_name,
        'primary' AS role
    WHERE NULLIF(TRIM(s.target_muscle_group), '') IS NOT NULL

    UNION ALL

    -- Secondary muscles
    SELECT
        TRIM(value) AS muscle_name,
        'secondary' AS role
    FROM regexp_split_to_table(
        COALESCE(s.secondary_muscles, ''),
        '\s*,\s*|\s*;\s*|\s*\|\s*'
    ) AS value
    WHERE TRIM(value) <> ''

) x

JOIN muscle_groups mg
    ON LOWER(TRIM(mg.name))
     = LOWER(TRIM(x.muscle_name))

ORDER BY
    e.exercise_id,
    mg.muscle_id,
    CASE
        WHEN x.role = 'primary' THEN 1
        ELSE 2
    END;


-- ============================================================
-- SECTION H - EXERCISE -> EQUIPMENT
-- ============================================================

DELETE FROM exercise_equipment ee
USING exercises e
WHERE ee.exercise_id = e.exercise_id
  AND EXISTS (
      SELECT 1
      FROM staging_exercises s
      WHERE NULLIF(TRIM(s."source_url"), '') IS NOT NULL
        AND LOWER(TRIM(e.source_url)) = LOWER(TRIM(NULLIF(TRIM(s."source_url"), '')))
  );


INSERT INTO exercise_equipment (
    exercise_id,
    equipment_id
)
SELECT DISTINCT
    e.exercise_id,
    eq.equipment_id
FROM staging_exercises s
JOIN exercises e
  ON LOWER(TRIM(e.source_url)) = LOWER(TRIM(NULLIF(TRIM(s."source_url"), '')))
CROSS JOIN LATERAL regexp_split_to_table(
    COALESCE(NULLIF(TRIM(s."equipment_required"), ''), ''),
    '\s*,\s*|\s*;\s*|\s*\|\s*'
) AS value
JOIN equipment eq
  ON LOWER(TRIM(eq.name)) = LOWER(TRIM(value))
WHERE TRIM(value) <> ''
ON CONFLICT (exercise_id, equipment_id) DO NOTHING;


-- ============================================================
-- SECTION I - EXERCISE MEDIA
-- ============================================================
-- Remove current imported media and rebuild it from CSV.
-- This makes re-import safe when paths change.

DELETE FROM exercise_media em
USING exercises e
WHERE em.exercise_id = e.exercise_id
  AND EXISTS (
      SELECT 1
      FROM staging_exercises s
      WHERE NULLIF(TRIM(s."source_url"), '') IS NOT NULL
        AND LOWER(TRIM(e.source_url)) = LOWER(TRIM(NULLIF(TRIM(s."source_url"), '')))
  );


-- Video
INSERT INTO exercise_media (
    exercise_id,
    media_type,
    storage_key,
    sort_order
)
SELECT DISTINCT
    e.exercise_id,
    'video',
    NULLIF(REPLACE(REPLACE(TRIM(s."video_path"), '\\', '/'), '//', '/'), ''),
    0
FROM staging_exercises s
JOIN exercises e
  ON LOWER(TRIM(e.source_url)) = LOWER(TRIM(NULLIF(TRIM(s."source_url"), '')))
WHERE NULLIF(REPLACE(REPLACE(TRIM(s."video_path"), '\\', '/'), '//', '/'), '') IS NOT NULL;


-- Thumbnail
INSERT INTO exercise_media (
    exercise_id,
    media_type,
    storage_key,
    sort_order
)
SELECT DISTINCT
    e.exercise_id,
    'thumbnail',
    NULLIF(REPLACE(REPLACE(TRIM(s."thumbnail_path"), '\\', '/'), '//', '/'), ''),
    0
FROM staging_exercises s
JOIN exercises e
  ON LOWER(TRIM(e.source_url)) = LOWER(TRIM(NULLIF(TRIM(s."source_url"), '')))
WHERE NULLIF(REPLACE(REPLACE(TRIM(s."thumbnail_path"), '\\', '/'), '//', '/'), '') IS NOT NULL;


-- ============================================================
-- SECTION J - FINAL VALIDATION
-- ============================================================

SELECT 'staging_exercises' AS table_name, COUNT(*) AS row_count
FROM staging_exercises

UNION ALL
SELECT 'exercises', COUNT(*)
FROM exercises

UNION ALL
SELECT 'exercise_translations_en', COUNT(*)
FROM exercise_translations
WHERE language = 'en'

UNION ALL
SELECT 'muscle_groups', COUNT(*)
FROM muscle_groups

UNION ALL
SELECT 'equipment', COUNT(*)
FROM equipment

UNION ALL
SELECT 'exercise_muscles', COUNT(*)
FROM exercise_muscles

UNION ALL
SELECT 'exercise_equipment', COUNT(*)
FROM exercise_equipment

UNION ALL
SELECT 'exercise_media', COUNT(*)
FROM exercise_media;


-- ============================================================
-- QUALITY CHECK 1: imported exercises missing EN name
-- ============================================================

SELECT
    e.exercise_id,
    e.source_url
FROM exercises e
JOIN staging_exercises s
  ON LOWER(TRIM(e.source_url)) = LOWER(TRIM(NULLIF(TRIM(s."source_url"), '')))
LEFT JOIN exercise_translations et
  ON et.exercise_id = e.exercise_id
 AND et.language = 'en'
WHERE et.exercise_id IS NULL;


-- ============================================================
-- QUALITY CHECK 2: imported exercises missing muscle mapping
-- ============================================================

SELECT
    e.exercise_id,
    et.name
FROM exercises e
JOIN staging_exercises s
  ON LOWER(TRIM(e.source_url)) = LOWER(TRIM(NULLIF(TRIM(s."source_url"), '')))
JOIN exercise_translations et
  ON et.exercise_id = e.exercise_id
 AND et.language = 'en'
LEFT JOIN exercise_muscles em
  ON em.exercise_id = e.exercise_id
WHERE em.exercise_id IS NULL;


-- ============================================================
-- QUALITY CHECK 3: imported exercises missing equipment
-- ============================================================
-- Empty result can be OK for bodyweight exercises.

SELECT
    e.exercise_id,
    et.name
FROM exercises e
JOIN staging_exercises s
  ON LOWER(TRIM(e.source_url)) = LOWER(TRIM(NULLIF(TRIM(s."source_url"), '')))
JOIN exercise_translations et
  ON et.exercise_id = e.exercise_id
 AND et.language = 'en'
LEFT JOIN exercise_equipment ee
  ON ee.exercise_id = e.exercise_id
WHERE ee.exercise_id IS NULL;


-- ============================================================
-- END
-- ============================================================
