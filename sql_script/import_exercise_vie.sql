-- ============================================================
-- BODIFY - IMPORT VIETNAMESE EXERCISE TRANSLATIONS
-- CSV: all_exercises_vi.csv
-- 704 rows
--
-- IMPORTANT:
--   This imports VI TRANSLATIONS ONLY.
--   It does NOT create new exercises.
--   Matching is done by source_url.
--   VI rows without a matching exercise are skipped.
-- ============================================================

-- 1. STAGING TABLE
DROP TABLE IF EXISTS staging_exercises_vie;

CREATE TABLE staging_exercises_vie (
    source_url   TEXT,
    name         TEXT,
    overview     TEXT,
    instructions TEXT,
    tips         TEXT
);

-- ============================================================
-- 2. IMPORT CSV USING DBEAVER
--
-- Right click:
--   staging_exercises_vie
--   -> Import Data
--
-- File: all_exercises_vi.csv
-- Format: CSV
-- Header: Yes
-- Delimiter: ,
-- Quote: "
-- Encoding: UTF-8
--
-- Expected: 704 rows
-- ============================================================


-- 3. CHECK IMPORT
SELECT COUNT(*) AS staging_rows
FROM staging_exercises_vie;

SELECT COUNT(*) AS empty_source_url
FROM staging_exercises_vie
WHERE NULLIF(TRIM(source_url), '') IS NULL;


-- 4. CHECK DUPLICATE SOURCE URL
SELECT
    LOWER(TRIM(source_url)) AS source_url,
    COUNT(*) AS cnt
FROM staging_exercises_vie
WHERE NULLIF(TRIM(source_url), '') IS NOT NULL
GROUP BY LOWER(TRIM(source_url))
HAVING COUNT(*) > 1
ORDER BY cnt DESC;


-- 5. CHECK VI -> EXISTING EXERCISE MATCH
-- These are the VI rows that will actually be imported.
SELECT COUNT(DISTINCT s.source_url) AS matched_exercises
FROM staging_exercises_vie s
JOIN exercises e
  ON LOWER(TRIM(e.source_url)) = LOWER(TRIM(s.source_url));


-- These VI rows have no matching exercise and will be skipped.
SELECT
    s.name,
    s.source_url
FROM staging_exercises_vie s
LEFT JOIN exercises e
  ON LOWER(TRIM(e.source_url)) = LOWER(TRIM(s.source_url))
WHERE e.exercise_id IS NULL
ORDER BY s.source_url;


-- ============================================================
-- 6. UPDATE EXISTING VI TRANSLATIONS
-- ============================================================

UPDATE exercise_translations et
SET
    name = x.name,
    overview = x.overview,
    instructions = x.instructions,
    tips = x.tips,
    updated_at = NOW()
FROM (
    SELECT DISTINCT ON (LOWER(TRIM(source_url)))
        LOWER(TRIM(source_url)) AS source_url,
        NULLIF(TRIM(name), '') AS name,
        NULLIF(TRIM(overview), '') AS overview,
        NULLIF(TRIM(instructions), '') AS instructions,
        NULLIF(TRIM(tips), '') AS tips
    FROM staging_exercises_vie
    WHERE NULLIF(TRIM(source_url), '') IS NOT NULL
      AND NULLIF(TRIM(name), '') IS NOT NULL
    ORDER BY LOWER(TRIM(source_url))
) x
JOIN exercises e
  ON LOWER(TRIM(e.source_url)) = x.source_url
WHERE et.exercise_id = e.exercise_id
  AND et.language = 'vi';


-- ============================================================
-- 7. INSERT NEW VI TRANSLATIONS
-- ============================================================

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
    'vi',
    x.name,
    x.overview,
    x.instructions,
    x.tips
FROM (
    SELECT DISTINCT ON (LOWER(TRIM(source_url)))
        LOWER(TRIM(source_url)) AS source_url,
        NULLIF(TRIM(name), '') AS name,
        NULLIF(TRIM(overview), '') AS overview,
        NULLIF(TRIM(instructions), '') AS instructions,
        NULLIF(TRIM(tips), '') AS tips
    FROM staging_exercises_vie
    WHERE NULLIF(TRIM(source_url), '') IS NOT NULL
      AND NULLIF(TRIM(name), '') IS NOT NULL
    ORDER BY LOWER(TRIM(source_url))
) x
JOIN exercises e
  ON LOWER(TRIM(e.source_url)) = x.source_url
WHERE NOT EXISTS (
    SELECT 1
    FROM exercise_translations et
    WHERE et.exercise_id = e.exercise_id
      AND et.language = 'vi'
);


-- ============================================================
-- 8. VALIDATION
-- ============================================================

SELECT
    COUNT(*) FILTER (WHERE language = 'en') AS english_count,
    COUNT(*) FILTER (WHERE language = 'vi') AS vietnamese_count
FROM exercise_translations;


-- Expected: 0 rows
SELECT
    et.exercise_id,
    et.name
FROM exercise_translations et
LEFT JOIN exercises e
  ON e.exercise_id = et.exercise_id
WHERE et.language = 'vi'
  AND e.exercise_id IS NULL;


-- Expected: 0 rows
SELECT
    exercise_id,
    name
FROM exercise_translations
WHERE language = 'vi'
  AND NULLIF(TRIM(name), '') IS NULL;


-- ============================================================
-- 9. EXERCISES WITH EN BUT WITHOUT VI
-- NORMAL if the VI dataset is incomplete.
-- ============================================================

SELECT
    e.exercise_id,
    en.name AS english_name,
    e.source_url
FROM exercises e
JOIN exercise_translations en
  ON en.exercise_id = e.exercise_id
 AND en.language = 'en'
LEFT JOIN exercise_translations vi
  ON vi.exercise_id = e.exercise_id
 AND vi.language = 'vi'
WHERE vi.exercise_id IS NULL
ORDER BY e.exercise_id;

-- ============================================================
-- END
-- ============================================================
