-- ============================================================
-- BODIFY - ROLLBACK OF ddl_phase_5.sql
--
-- Undoes Phase 5 at the database level: the three columns, the FORM_MASTER
-- badge, and the widened achievement_type CHECK.
--
-- DESTRUCTIVE. Dropping session_sets.ai_rep_scores throws away how the camera
-- judged every set ever recorded with it, and there is no soft delete. Take a
-- dump first if any real session has been scored:
--
--   pg_dump --data-only --table=session_sets <url> > session_sets_backup.sql
--
-- Section 1 alone (the badge) is the reversible part; sections 2-3 are not.
-- Idempotent: guarded throughout, so re-running is a no-op.
-- ============================================================


-- ============================================================
-- 1. THE BADGE
-- ============================================================
-- Unlocked rows go with it (user_achievements cascades on achievement_id), so
-- anyone who earned it loses it. That is the correct behaviour for a rollback:
-- the badge no longer exists to have been earned.

DELETE FROM achievements WHERE code = 'FORM_MASTER';


-- ============================================================
-- 2. THE CHECK CONSTRAINT
-- ============================================================
-- Back to the Phase 4 vocabulary. Runs after the delete above, because the
-- narrower constraint cannot be added while an 'ai_form_quality' row exists.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_achievement_type'
    ) THEN
        ALTER TABLE achievements DROP CONSTRAINT chk_achievement_type;
    END IF;

    ALTER TABLE achievements
        ADD CONSTRAINT chk_achievement_type
        CHECK (achievement_type IN ('workout_count', 'workout_streak',
                                    'total_volume', 'exercise_duration',
                                    'personal_record', 'plan_completion'));
END $$;


-- ============================================================
-- 3. THE COLUMNS
-- ============================================================
-- Reports what is about to be lost before losing it, so a rollback run by
-- reflex on a database with real scored sets says so out loud.

DO $$
DECLARE
    n_scored BIGINT;
BEGIN
    SELECT count(*) INTO n_scored
      FROM session_sets WHERE ai_rep_scores IS NOT NULL;

    IF n_scored > 0 THEN
        RAISE WARNING 'dropping AI scores for % set(s) - unrecoverable', n_scored;
    END IF;
END $$;

ALTER TABLE session_sets
    DROP COLUMN IF EXISTS ai_algorithm,
    DROP COLUMN IF EXISTS ai_rep_scores;

ALTER TABLE exercises
    DROP COLUMN IF EXISTS ai_algorithm;
