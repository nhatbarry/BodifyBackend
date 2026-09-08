-- ============================================================
-- BODIFY - PHASE 5 (đợt 1: Push-up)
-- AI CAMERA: REP COUNTING AND FORM SCORING
--
-- The camera counts reps and scores each one by how deep it went: 0 for a rep
-- that never reached the first depth milestone, 50 for one that reached it,
-- 100 for one that reached the second. Every exercise has its own thresholds,
-- and therefore its own algorithm - which is why the algorithm is named on the
-- row rather than inferred from the exercise.
--
-- Nothing here stores a score. `ai_rep_scores` is the fact; the set's
-- percentage is derived from it on read, the same rule as volume_kg and BMI.
--
-- Assumptions from earlier phases:
--   exercises(exercise_id)          Phase 1
--   session_sets(session_set_id)    Phase 3
--   achievements(code)              Phase 4
--
-- Idempotent: every statement is guarded, so re-running is a no-op.
-- Rollback: sql_script/rollback_phase_5.sql
-- ============================================================


-- ============================================================
-- 1. WHICH ALGORITHM CAN SCORE A MOVEMENT
-- ============================================================
-- A push-up is counted from the elbow angle, a squat from the knee angle, and
-- neither algorithm can score the other movement. `tracking_type` cannot
-- answer this: Bench Press and Push-up are both counted in reps, but only one
-- of them can be judged from a phone propped against a wall.
--
-- Free text with no CHECK constraint, deliberately. The vocabulary lives in
-- the Flutter processor factory and grows with every algorithm added; an
-- unrecognised value already degrades safely there to "no AI for this
-- exercise", and a CHECK would turn every new algorithm into a migration.
--
-- NULL for every exercise until somebody assigns one by hand. There is no
-- backfill here on purpose - which catalog rows deserve which algorithm is a
-- judgment call, and the Phase 4 tracking_type sweep is the cautionary tale.

ALTER TABLE exercises
    ADD COLUMN IF NOT EXISTS ai_algorithm VARCHAR(50);

COMMENT ON COLUMN exercises.ai_algorithm IS
    'Client-side pose-detection algorithm that can count and score this '
    'movement, e.g. ''pushup_v1''. NULL means no AI support yet. Assigned by '
    'hand per exercise; the vocabulary lives in the Flutter processor factory.';


-- ============================================================
-- 2. HOW THE CAMERA JUDGED ONE SET
-- ============================================================
-- Both columns are snapshots, exactly like target_* on the same table:
-- they record what happened when the set was performed. Re-tuning an
-- algorithm, or renaming it, must not rewrite how last week's set was judged -
-- which is also why the PUT correction endpoint refuses to touch them.
--
-- ai_rep_scores is JSONB rather than a child table. It is written once,
-- read as a whole, small (one integer per rep), and never filtered on a
-- single element - a table with a cascade would be machinery for nothing.

ALTER TABLE session_sets
    ADD COLUMN IF NOT EXISTS ai_algorithm  VARCHAR(50),
    ADD COLUMN IF NOT EXISTS ai_rep_scores JSONB;

COMMENT ON COLUMN session_sets.ai_algorithm IS
    'Snapshot of the algorithm that scored this set, NULL if logged by hand.';
COMMENT ON COLUMN session_sets.ai_rep_scores IS
    'One score per counted rep, in order: 0, 50 or 100 by depth reached. The '
    'set percentage is derived from this on read, never stored.';


-- ============================================================
-- 3. A BADGE FOR CLEAN FORM
-- ============================================================
-- Deliberately an achievement and not a personal record. A record is a
-- ceiling on load - 50 kg, 12 reps - and a form score is a percentage about
-- how a movement was performed; putting the two in one table would mean
-- "my PRs" listing a percentage next to kilograms.

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
                                    'personal_record', 'plan_completion',
                                    'ai_form_quality'));
END $$;

-- 90% rather than 100: one half-depth rep in an otherwise clean set of ten is
-- not bad form, and a badge that only fires on perfection is one nobody sees.
INSERT INTO achievements (code, name, description, achievement_type,
                          requirement_value, requirement_unit, exercise_id)
VALUES
    ('FORM_MASTER', 'Form chuẩn',
     'Hoàn thành 10 set được camera chấm từ 90 điểm form trở lên.',
     'ai_form_quality', 10, 'set', NULL)
ON CONFLICT (code) DO UPDATE
    SET name              = EXCLUDED.name,
        description       = EXCLUDED.description,
        achievement_type  = EXCLUDED.achievement_type,
        requirement_value = EXCLUDED.requirement_value,
        requirement_unit  = EXCLUDED.requirement_unit,
        exercise_id       = EXCLUDED.exercise_id;


-- ============================================================
-- 4. VERIFY
-- ============================================================
DO $$
DECLARE
    n_ai_exercises BIGINT;
    n_achievements BIGINT;
BEGIN
    SELECT count(*) INTO n_ai_exercises
      FROM exercises WHERE ai_algorithm IS NOT NULL;
    SELECT count(*) INTO n_achievements FROM achievements;

    RAISE NOTICE 'exercises with an AI algorithm: %', n_ai_exercises;
    RAISE NOTICE 'achievements in catalog: %', n_achievements;

    IF n_ai_exercises = 0 THEN
        RAISE NOTICE 'None assigned yet. Pick the Push-up row and run e.g.:';
        RAISE NOTICE '  UPDATE exercises SET ai_algorithm = ''pushup_v1'' WHERE exercise_id = <id>;';
    END IF;
END $$;
