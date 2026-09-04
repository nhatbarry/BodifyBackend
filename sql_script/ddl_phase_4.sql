-- ============================================================
-- BODIFY - PHASE 4
-- PROGRESS, PERSONAL RECORDS, ACHIEVEMENTS
--
-- Phase 3 records what was performed. Phase 4 reads that record back and says
-- something about it: how much of the plan was completed, how the numbers are
-- moving, what the user has never beaten before, and what that unlocks.
--
-- Nothing here is a counter the application maintains. Every figure is derived
-- from `workout_sessions` / `session_sets` on read, for the same reason BMI is
-- computed rather than stored: a cached total is a second source of truth that
-- silently drifts from the first. The one exception is `personal_bests`, and
-- it earns that exception below.
--
-- Assumptions from earlier phases:
--   auth.users(id)                              Supabase Auth, UUID PK
--   exercises(exercise_id)
--   session_sets(session_set_id)                Phase 3
--   workout_exercise_sets(workout_exercise_set_id)   Phase 2
--   workout_plans(plan_id)                      Phase 2
--
-- Idempotent: every statement is guarded, so re-running is a no-op.
-- ============================================================


-- ============================================================
-- 1. HOW AN EXERCISE IS MEASURED
-- ============================================================
-- `exercise_type` cannot answer this. Bench Press and Plank are both
-- 'Strength', but one is counted in reps and the other in seconds, and the
-- app has to know which input to show before the user has typed anything.
--
-- Weight is deliberately NOT part of this vocabulary. A Farmer's Carry is
-- measured in metres and loaded with 40 kg; a Weighted Pull Up Hang is
-- measured in seconds and loaded with 20 kg. `tracking_type` says what the
-- *effort* is counted in; `weight_kg` stays available for every type.

ALTER TABLE exercises
    ADD COLUMN IF NOT EXISTS tracking_type VARCHAR(20) NOT NULL DEFAULT 'reps';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_exercises_tracking_type'
    ) THEN
        ALTER TABLE exercises
            ADD CONSTRAINT chk_exercises_tracking_type
            CHECK (tracking_type IN ('reps', 'duration', 'distance', 'reps_duration'));
    END IF;
END $$;

COMMENT ON COLUMN exercises.tracking_type IS
    'What the effort is counted in: reps, duration (seconds), distance (metres), '
    'or reps_duration (a repeated movement with a hold in it). Weight is '
    'orthogonal and applies to all four.';


-- ============================================================
-- 2. PLANNED TARGETS FOR THE NEW MEASURES
-- ============================================================
-- Phase 2 could only plan reps and weight, which is why a planned Plank had
-- nothing to aim at. Performance % needs a target on the same axis as the
-- actual, so each measure gets its planned counterpart.

ALTER TABLE workout_exercise_sets
    ADD COLUMN IF NOT EXISTS target_duration_seconds INTEGER,
    ADD COLUMN IF NOT EXISTS target_distance_m NUMERIC(8, 2);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_workout_set_target_duration'
    ) THEN
        ALTER TABLE workout_exercise_sets
            ADD CONSTRAINT chk_workout_set_target_duration
            CHECK (target_duration_seconds IS NULL OR target_duration_seconds > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_workout_set_target_distance'
    ) THEN
        ALTER TABLE workout_exercise_sets
            ADD CONSTRAINT chk_workout_set_target_distance
            CHECK (target_distance_m IS NULL OR target_distance_m > 0);
    END IF;
END $$;


-- ============================================================
-- 3. PERFORMED AND SNAPSHOTTED TARGETS ON THE SESSION SET
-- ============================================================
-- `duration_seconds` already exists here and has never been written to.
-- `distance_m` is new. Both targets are snapshots taken when the session
-- starts, exactly like target_reps / target_weight_kg: renaming or re-planning
-- afterwards must not rewrite what the session says the user was aiming at.

ALTER TABLE session_sets
    ADD COLUMN IF NOT EXISTS distance_m NUMERIC(8, 2),
    ADD COLUMN IF NOT EXISTS target_duration_seconds INTEGER,
    ADD COLUMN IF NOT EXISTS target_distance_m NUMERIC(8, 2);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_session_set_distance'
    ) THEN
        ALTER TABLE session_sets
            ADD CONSTRAINT chk_session_set_distance
            CHECK (distance_m IS NULL OR distance_m >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_session_set_target_duration'
    ) THEN
        ALTER TABLE session_sets
            ADD CONSTRAINT chk_session_set_target_duration
            CHECK (target_duration_seconds IS NULL OR target_duration_seconds > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_session_set_target_distance'
    ) THEN
        ALTER TABLE session_sets
            ADD CONSTRAINT chk_session_set_target_distance
            CHECK (target_distance_m IS NULL OR target_distance_m > 0);
    END IF;
END $$;


-- ============================================================
-- 4. WHEN A PLAN STARTED
-- ============================================================
-- "Complete a plan" needs a window, and `duration_weeks` alone gives a length
-- with no beginning. Existing plans are backfilled from `created_at`, which is
-- the closest honest answer: nobody recorded a start date, and the day the
-- plan was made is when the user began following it.

ALTER TABLE workout_plans
    ADD COLUMN IF NOT EXISTS start_date DATE;

UPDATE workout_plans
   SET start_date = created_at::date
 WHERE start_date IS NULL;

COMMENT ON COLUMN workout_plans.start_date IS
    'First day the plan is followed. With duration_weeks it gives the window '
    'plan-completion is measured over. Nullable: an open-ended plan has no end.';


-- ============================================================
-- 5. PERSONAL RECORDS
-- ============================================================
-- Two tables, because they answer two different questions.
--
--   personal_records  every time a record was broken.  40 -> 45 -> 50 kg.
--                     Append-only. This is what a progress chart plots.
--
--   personal_bests    the current best, one row per (user, exercise, type).
--                     This is what "my PRs" reads, and - more importantly -
--                     it is how the write path stays correct under a double
--                     tap. See section 5c.

-- --- 5a. The history table -----------------------------------------------
-- The FK to the set that produced the record was lost when Phase 3 replaced
-- `exercise_sets`. Restoring it as SET NULL, never CASCADE: deleting one
-- session must not erase months of records, exactly as a deleted plan does not
-- erase the sessions performed from it.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_personal_records_set'
    ) THEN
        ALTER TABLE personal_records
            ADD CONSTRAINT fk_personal_records_set
            FOREIGN KEY (set_id) REFERENCES session_sets(session_set_id)
            ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_personal_record_type'
    ) THEN
        ALTER TABLE personal_records
            ADD CONSTRAINT chk_personal_record_type
            CHECK (record_type IN ('max_weight', 'max_reps', 'max_volume',
                                   'max_duration', 'max_distance', 'estimated_1rm'));
    END IF;
END $$;

-- The existing index stops at achieved_at and cannot serve "the history of one
-- record type", which is every chart on the PR screen.
CREATE INDEX IF NOT EXISTS idx_personal_records_type
    ON personal_records (user_id, exercise_id, record_type, achieved_at DESC);


-- --- 5b. The current-best table ------------------------------------------
CREATE TABLE IF NOT EXISTS personal_bests (
    personal_best_id BIGSERIAL PRIMARY KEY,

    user_id     UUID           NOT NULL
                REFERENCES auth.users (id) ON DELETE CASCADE,
    exercise_id BIGINT         NOT NULL
                REFERENCES exercises (exercise_id) ON DELETE CASCADE,

    record_type VARCHAR(30)    NOT NULL,
    value       NUMERIC(10, 2) NOT NULL,
    unit        VARCHAR(20)    NOT NULL,

    achieved_at TIMESTAMPTZ    NOT NULL DEFAULT now(),

    -- Same rule as the history table: the record outlives the set it came from.
    session_set_id BIGINT
                REFERENCES session_sets (session_set_id) ON DELETE SET NULL,

    created_at  TIMESTAMPTZ    NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ    NOT NULL DEFAULT now(),

    CONSTRAINT chk_personal_best_value CHECK (value >= 0),
    CONSTRAINT chk_personal_best_type
        CHECK (record_type IN ('max_weight', 'max_reps', 'max_volume',
                               'max_duration', 'max_distance', 'estimated_1rm')),

    -- 5c. This is the point of the table.
    --
    -- "Insert only if it beats the current best" as SELECT-then-INSERT has a
    -- window: two finishes of the same session both read 50 and both write 55,
    -- and the history grows a duplicate. With this key the whole decision
    -- becomes one atomic statement:
    --
    --   INSERT INTO personal_bests (...) VALUES (...)
    --   ON CONFLICT (user_id, exercise_id, record_type)
    --   DO UPDATE SET value = EXCLUDED.value, ...
    --   WHERE personal_bests.value < EXCLUDED.value
    --   RETURNING personal_best_id;
    --
    -- No row returned means it was not a record, so nothing goes into the
    -- history either. The constraint, not application locking, is what keeps
    -- both tables honest - and it is why `personal_records` can stay
    -- append-only without ever growing a duplicate.
    CONSTRAINT uq_personal_bests_user_exercise_type
        UNIQUE (user_id, exercise_id, record_type)
);

CREATE INDEX IF NOT EXISTS idx_personal_bests_user
    ON personal_bests (user_id, exercise_id);


-- ============================================================
-- 6. ACHIEVEMENTS
-- ============================================================
-- A catalog plus what each user has unlocked. No progress counters are stored:
-- "7 workouts" is answered by counting completed sessions at the moment of
-- evaluation, so reopening and re-finishing a session cannot inflate it.

CREATE TABLE IF NOT EXISTS achievements (
    achievement_id BIGSERIAL PRIMARY KEY,

    -- The stable identity. Flutter maps `code` to a bundled asset, so renaming
    -- an icon file never becomes a database migration.
    code             VARCHAR(50)  NOT NULL UNIQUE,
    name             VARCHAR(100) NOT NULL,
    description      TEXT,

    -- Left NULL in Phase 4. Reserved for the day icons are served from R2
    -- rather than shipped in the bundle.
    icon_url         TEXT,

    achievement_type VARCHAR(30)  NOT NULL,
    requirement_value NUMERIC(10, 2) NOT NULL,
    requirement_unit VARCHAR(20)  NOT NULL,

    -- Only for achievements about one specific movement, e.g. holding a plank.
    -- NULL means the requirement is about training in general.
    exercise_id      BIGINT
                     REFERENCES exercises (exercise_id) ON DELETE CASCADE,

    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT chk_achievement_type
        CHECK (achievement_type IN ('workout_count', 'workout_streak',
                                    'total_volume', 'exercise_duration',
                                    'personal_record', 'plan_completion')),
    CONSTRAINT chk_achievement_requirement CHECK (requirement_value > 0)
);

CREATE INDEX IF NOT EXISTS idx_achievements_active
    ON achievements (achievement_type) WHERE is_active;


CREATE TABLE IF NOT EXISTS user_achievements (
    user_achievement_id BIGSERIAL PRIMARY KEY,

    user_id        UUID   NOT NULL
                   REFERENCES auth.users (id) ON DELETE CASCADE,
    achievement_id BIGINT NOT NULL
                   REFERENCES achievements (achievement_id) ON DELETE CASCADE,

    achieved_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- What the figure actually was when it unlocked - 7 workouts, 62 seconds.
    -- Kept because "you did it" is less satisfying than "you held it for 62s",
    -- and because it is the evidence behind the row.
    progress_value NUMERIC(10, 2),

    -- Unlocking is a one-time event. Evaluation runs on every finish, so
    -- without this the second finish of a reopened session would award it
    -- again; with it, the insert is a plain ON CONFLICT DO NOTHING.
    CONSTRAINT uq_user_achievements UNIQUE (user_id, achievement_id)
);

CREATE INDEX IF NOT EXISTS idx_user_achievements_user
    ON user_achievements (user_id, achieved_at DESC);


-- ============================================================
-- 7. SEED THE CATALOG
-- ============================================================
-- ON CONFLICT on `code` so re-running updates the wording without creating a
-- second copy - and without disturbing anybody's unlocked rows, which point at
-- achievement_id.
--
-- PLANK_60 and PLANK_120 are two rows sharing one icon; the UI shows them as
-- tiers of the same badge.

INSERT INTO achievements (code, name, description, achievement_type,
                          requirement_value, requirement_unit, exercise_id)
VALUES
    ('WORKOUT_FIRST', 'Buổi tập đầu tiên',
     'Hoàn thành buổi tập đầu tiên của bạn.',
     'workout_count', 1, 'workout', NULL),

    ('WORKOUT_7', 'Bảy buổi tập',
     'Hoàn thành 7 buổi tập.',
     'workout_count', 7, 'workout', NULL),

    ('WORKOUT_10', 'Mười buổi tập',
     'Hoàn thành 10 buổi tập.',
     'workout_count', 10, 'workout', NULL),

    ('STREAK_7', 'Bảy ngày liên tiếp',
     'Tập luyện 7 ngày liên tiếp.',
     'workout_streak', 7, 'day', NULL),

    -- The threshold is a product decision, not a fact. 10 tấn is roughly a
    -- month of steady training; change the number here, not in code.
    ('VOLUME_10000', 'Mười tấn',
     'Nâng tổng khối lượng 10.000 kg.',
     'total_volume', 10000, 'kg', NULL),

    ('PLANK_60', 'Plank 60 giây',
     'Giữ plank trong 60 giây.',
     'exercise_duration', 60, 'seconds',
     (SELECT exercise_id FROM exercises WHERE exercise_id = 8)),

    ('PLANK_120', 'Plank 120 giây',
     'Giữ plank trong 120 giây.',
     'exercise_duration', 120, 'seconds',
     (SELECT exercise_id FROM exercises WHERE exercise_id = 8)),

    ('PR_FIRST', 'Kỷ lục đầu tiên',
     'Thiết lập kỷ lục cá nhân đầu tiên.',
     'personal_record', 1, 'record', NULL),

    ('PLAN_COMPLETE', 'Hoàn thành giáo án',
     'Hoàn thành trọn vẹn một giáo án tập luyện.',
     'plan_completion', 1, 'plan', NULL)
ON CONFLICT (code) DO UPDATE
    SET name              = EXCLUDED.name,
        description       = EXCLUDED.description,
        achievement_type  = EXCLUDED.achievement_type,
        requirement_value = EXCLUDED.requirement_value,
        requirement_unit  = EXCLUDED.requirement_unit,
        exercise_id       = EXCLUDED.exercise_id;


-- ============================================================
-- 8. tracking_type BACKFILL
-- ============================================================
-- Reviewed one list at a time rather than inferred from a single regex, which
-- got this wrong twice: it missed every SMR entry and swept up eight exercises
-- that only *sound* like holds.
--
-- Corroborated against the instruction text: 18 of 19 SMR entries name a
-- duration, against 1 of 33 warmups. Warmups are therefore left as 'reps'.

-- 8a. Self-myofascial release: foam rolling, lacrosse ball, tiger tail.
--     Always timed, never counted.
UPDATE exercises SET tracking_type = 'duration'
 WHERE exercise_type = 'SMR';

-- 8b. Static holds.
UPDATE exercises SET tracking_type = 'duration'
 WHERE exercise_id IN (
        8,    -- Plank
        9,    -- Side Plank
        653,  -- Exercise Ball Plank
        496,  -- Side Plank with Hip Dip
        181,  -- Hollow Body Hold
        310,  -- GHD Back Extension Iso Hold
        415,  -- Weighted Pull Up Hang
        27,   -- Standing Stomach Vacuum
        229   -- Weight Plate Pinches
      );

-- 8c. Loaded carries. Measured over ground, not in reps or seconds - which is
--     why `distance` exists at all. They stay heavily loaded, so weight_kg
--     matters here as much as the metres do.
UPDATE exercises SET tracking_type = 'distance'
 WHERE exercise_id IN (
        281,  -- Barbell Suitcase Carry
        250,  -- Dumbbell Farmers Carry
        663,  -- Dumbbell Overhead Carry
        255,  -- Dumbbell Suitcase Carry
        266,  -- Fat Gripz Dumbbell Farmers Carry
        267,  -- Kettlebell Goblet Carry
        262,  -- Kettlebell Single Arm Bottoms Up Carry
        275,  -- Kettlebell Single Arm Racked Carry
        277,  -- Overhead Barbell Carry
        253,  -- Plate Pinch Carry
        258,  -- Trap Bar Farmers Carry
        380,  -- Trap Bar Overhead Carry
        383   -- Zercher Carry
      );

-- 8d. A repeated movement with a hold inside it: both numbers are real.
UPDATE exercises SET tracking_type = 'reps_duration'
 WHERE exercise_id IN (
        534,  -- Split Squat with Iso-Hold
        474   -- Chest Supported Dumbbell Row with Isohold
      );

-- Everything else keeps the 'reps' default. That deliberately includes the
-- eight the first regex caught by mistake - Hang Clean, Hanging Knee/Leg
-- Raise, L-Sit Chin/Pull Up, Plank to Hip Raise - which are counted, not held.


-- ============================================================
-- 9. VERIFY
-- ============================================================
DO $$
DECLARE
    n_duration      BIGINT;
    n_distance      BIGINT;
    n_reps_duration BIGINT;
    n_achievements  BIGINT;
BEGIN
    SELECT count(*) INTO n_duration      FROM exercises WHERE tracking_type = 'duration';
    SELECT count(*) INTO n_distance      FROM exercises WHERE tracking_type = 'distance';
    SELECT count(*) INTO n_reps_duration FROM exercises WHERE tracking_type = 'reps_duration';
    SELECT count(*) INTO n_achievements  FROM achievements;

    RAISE NOTICE 'tracking_type: % duration, % distance, % reps_duration',
                 n_duration, n_distance, n_reps_duration;
    RAISE NOTICE 'achievements seeded: %', n_achievements;

    IF n_duration <> 28 THEN
        RAISE WARNING 'expected 28 duration exercises, found %', n_duration;
    END IF;
    IF n_distance <> 13 THEN
        RAISE WARNING 'expected 13 distance exercises, found %', n_distance;
    END IF;
END $$;


-- ============================================================
-- 10. tracking_type - WARMUP REVIEW (reviewed 2026-09-04)
-- ============================================================
-- Held separately from section 8 because it needed a human eye rather than
-- evidence: only 1 of the 33 warmups names a duration in its instructions, so
-- the text signal that settled the SMR list says nothing useful here.
--
-- The five below are positions you settle into. The three left out - Rocking
-- Frog Stretch, Walking Quad Stretch, World's Greatest Stretch - move
-- continuously and are counted per side.

UPDATE exercises SET tracking_type = 'duration'
 WHERE exercise_id IN (
        306,  -- 90/90 Piriformis Stretch
        298,  -- Figure 4 Glute Stretch
        410,  -- Rack Lat Stretch (the one warmup whose text names seconds)
        48,   -- Deep Squat Prying
        473   -- Thoracic Extension on Foam Roller
      );
