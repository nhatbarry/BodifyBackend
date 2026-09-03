-- ============================================================
-- BODIFY - remove dead data and superseded tables
--
-- Housekeeping before Phase 4. Everything here is either an import artifact
-- or a table from a design that was replaced and never written to. It matters
-- now because the AI work will read this schema directly, and a model that
-- learns "None" is a muscle group, or that plans live in `workout_templates`,
-- learns something false.
--
-- Reversible: `rollback_none_muscle.sql` restores section 1.
-- Idempotent: safe to run more than once.
-- ============================================================

BEGIN;

-- ============================================================
-- 1. The "None" muscle group
-- ============================================================
-- muscle_groups id 6 is named the literal string "None" - an import artifact
-- meaning "this exercise has no secondary muscle". It was attached to 234
-- exercises as a secondary muscle. The API already filters it out of
-- /exercises/filters and exercise detail, so this only removes rows nothing
-- was reading.
--
-- Checked before deleting: all 234 links are secondary, no exercise is left
-- with zero muscles, and no term_translations row refers to it.

DO $$
DECLARE
    orphaned BIGINT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM muscle_groups WHERE muscle_id = 6 AND name = 'None') THEN
        RETURN;  -- already cleaned
    END IF;

    SELECT count(*) INTO orphaned FROM (
        SELECT em.exercise_id
          FROM exercise_muscles em
         GROUP BY em.exercise_id
        HAVING count(*) FILTER (WHERE em.muscle_id <> 6) = 0
    ) x;

    IF orphaned > 0 THEN
        RAISE EXCEPTION
            'Refusing to remove muscle group 6: % exercises would be left with '
            'no muscle at all.', orphaned;
    END IF;
END
$$;

DELETE FROM exercise_muscles WHERE muscle_id = 6;
DELETE FROM muscle_groups WHERE muscle_id = 6 AND name = 'None';


-- ============================================================
-- 2. Tables from the superseded Phase 1 design
-- ============================================================
-- Phase 2 replaced these with workout_plans / workout_days /
-- workout_exercises / workout_exercise_sets / schedules. They were created,
-- never written to, and are now a trap: the next person to open the schema
-- finds two plausible homes for a workout plan.
--
-- The guard refuses to drop anything holding rows, rather than trusting this
-- comment.

DO $$
DECLARE
    candidate TEXT;
    found     BIGINT;
BEGIN
    FOREACH candidate IN ARRAY ARRAY[
        'workout_schedule_days', 'workout_schedules',
        'workout_template_exercises', 'workout_templates'
    ]
    LOOP
        IF to_regclass('public.' || candidate) IS NOT NULL THEN
            EXECUTE format('SELECT count(*) FROM %I', candidate) INTO found;
            IF found > 0 THEN
                RAISE EXCEPTION
                    'Refusing to drop %: it holds % rows.', candidate, found;
            END IF;
        END IF;
    END LOOP;
END
$$;

-- Order matters: children before parents.
DROP TABLE IF EXISTS workout_schedule_days;
DROP TABLE IF EXISTS workout_schedules;
DROP TABLE IF EXISTS workout_template_exercises;
DROP TABLE IF EXISTS workout_templates;

-- staging_exercises / staging_exercises_vie are deliberately KEPT.
--
-- They looked empty in pg_stat_user_tables, which reports an estimate, not a
-- count - they actually hold 704 rows each: the raw CSV import the whole
-- catalog was built from. That is provenance worth keeping, and the guard
-- above would refuse to drop them anyway.

COMMIT;
