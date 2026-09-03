-- ============================================================
-- BODIFY - PHASE 2
-- WORKOUT PLAN + WORKOUT DAY + WORKOUT EXERCISE
-- + WORKOUT EXERCISE SET + SCHEDULE
--
-- PostgreSQL
--
-- Assumption from Phase 1:
--   auth.users(id)          -- Supabase Auth, UUID primary key
--   exercises(exercise_id)
--
-- NOTE: there is no public.users table. Section 1 of ddl_phase1.sql
-- was never applied; auth is owned entirely by Supabase Auth. Every
-- user_id below is therefore uuid with NO foreign key, matching
-- user_profiles and body_metrics. Ownership of a workout_day is two
-- joins away (day -> plan -> user_id) and cannot be expressed as a
-- constraint, so the API layer must enforce it - see section 5.
-- ============================================================


-- ============================================================
-- 1. WORKOUT PLANS
-- ============================================================

CREATE TABLE IF NOT EXISTS workout_plans (
    plan_id          BIGSERIAL PRIMARY KEY,

    user_id          UUID NOT NULL,

    name             VARCHAR(150) NOT NULL,
    description      TEXT,

    goal             VARCHAR(50),

    duration_weeks   INTEGER,

    status           VARCHAR(20) NOT NULL DEFAULT 'active',

    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_workout_plan_duration
        CHECK (
            duration_weeks IS NULL
            OR duration_weeks > 0
        ),

    CONSTRAINT chk_workout_plan_status
        CHECK (
            status IN ('active', 'archived')
        )
);


-- ============================================================
-- 2. WORKOUT DAYS
-- ============================================================
-- day_order is the position inside the plan ("Day 1 - Push"), NOT a
-- weekday. Nothing here knows about dates; only schedules does.
--
-- is_rest_day makes a planned rest a first-class row instead of an
-- absence. Without it the calendar cannot tell "resting today" from
-- "nothing scheduled yet" - both would simply be a date with no
-- schedule row. A rest day is still scheduled like any other day; it
-- just carries no workout_exercises. SQL cannot express that emptiness
-- (it is a cross-table rule), so the API must refuse to add exercises
-- to a rest day.
--
-- The UNIQUE below is DEFERRABLE because reordering is a core Phase 2
-- feature: swapping day 1 and day 2 breaks uniqueness in the middle of
-- the transaction even though the end state is valid. Deferring the
-- check to COMMIT is what makes a plain two-UPDATE swap possible.

CREATE TABLE IF NOT EXISTS workout_days (
    workout_day_id   BIGSERIAL PRIMARY KEY,

    plan_id          BIGINT NOT NULL,

    name             VARCHAR(150) NOT NULL,

    day_order        INTEGER NOT NULL,

    is_rest_day      BOOLEAN NOT NULL DEFAULT FALSE,

    description      TEXT,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_workout_day_plan
        FOREIGN KEY (plan_id)
        REFERENCES workout_plans(plan_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_workout_day_order
        CHECK (day_order > 0),

    CONSTRAINT uq_workout_day_order
        UNIQUE (plan_id, day_order)
        DEFERRABLE INITIALLY DEFERRED
);


-- ============================================================
-- 3. WORKOUT EXERCISES
-- ============================================================
-- ON DELETE RESTRICT on exercise_id: a catalog exercise somebody has
-- already planned must not vanish from under them.

CREATE TABLE IF NOT EXISTS workout_exercises (
    workout_exercise_id   BIGSERIAL PRIMARY KEY,

    workout_day_id        BIGINT NOT NULL,

    exercise_id           BIGINT NOT NULL,

    exercise_order        INTEGER NOT NULL,

    notes                 TEXT,

    created_at            TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_workout_exercise_day
        FOREIGN KEY (workout_day_id)
        REFERENCES workout_days(workout_day_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_workout_exercise_exercise
        FOREIGN KEY (exercise_id)
        REFERENCES exercises(exercise_id)
        ON DELETE RESTRICT,

    CONSTRAINT chk_workout_exercise_order
        CHECK (exercise_order > 0),

    CONSTRAINT uq_workout_exercise_order
        UNIQUE (workout_day_id, exercise_order)
        DEFERRABLE INITIALLY DEFERRED
);


-- ============================================================
-- 4. WORKOUT EXERCISE SETS
-- ============================================================
-- One row per set, not a target_sets counter: "10 / 10 / 8 reps" is
-- three rows, each free to carry its own weight and rest.

CREATE TABLE IF NOT EXISTS workout_exercise_sets (
    workout_exercise_set_id   BIGSERIAL PRIMARY KEY,

    workout_exercise_id       BIGINT NOT NULL,

    set_order                 INTEGER NOT NULL,

    target_reps               INTEGER,

    target_weight_kg          NUMERIC(7,2),

    rest_seconds              INTEGER,

    notes                     TEXT,

    created_at                TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_workout_set_exercise
        FOREIGN KEY (workout_exercise_id)
        REFERENCES workout_exercises(workout_exercise_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_workout_set_order
        CHECK (set_order > 0),

    CONSTRAINT chk_target_reps
        CHECK (
            target_reps IS NULL
            OR target_reps > 0
        ),

    CONSTRAINT chk_target_weight
        CHECK (
            target_weight_kg IS NULL
            OR target_weight_kg >= 0
        ),

    CONSTRAINT chk_rest_seconds
        CHECK (
            rest_seconds IS NULL
            OR rest_seconds >= 0
        ),

    CONSTRAINT uq_workout_set_order
        UNIQUE (workout_exercise_id, set_order)
        DEFERRABLE INITIALLY DEFERRED
);


-- ============================================================
-- 5. SCHEDULES
-- ============================================================
-- The only table that knows about dates. A workout_day can be
-- scheduled many times without duplicating its exercises.
--
-- scheduled_date and day_of_week are an exclusive arc: exactly one is
-- set, never both. A one-off session has a date; a weekly repeat has a
-- weekday. A row carrying both would have no defined meaning. Same
-- num_nonnulls pattern as term_translations.
--
-- WARNING - ownership is NOT enforced here. user_id and workout_day_id
-- are independent columns, so SQL alone happily lets one user schedule
-- a workout_day belonging to somebody else. The owner sits two joins
-- away (workout_days -> workout_plans.user_id), which no FK can
-- express. Every write to this table MUST verify that ownership in the
-- API and answer 404 (not 403) when it fails, matching the
-- body-metrics rule.

CREATE TABLE IF NOT EXISTS schedules (
    schedule_id        BIGSERIAL PRIMARY KEY,

    user_id            UUID NOT NULL,

    workout_day_id     BIGINT NOT NULL,

    scheduled_date     DATE,

    day_of_week        SMALLINT,

    is_active          BOOLEAN NOT NULL DEFAULT TRUE,

    created_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_schedule_workout_day
        FOREIGN KEY (workout_day_id)
        REFERENCES workout_days(workout_day_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_schedule_day_of_week
        CHECK (
            day_of_week IS NULL
            OR day_of_week BETWEEN 1 AND 7
        ),

    CONSTRAINT chk_schedule_target
        CHECK (
            num_nonnulls(scheduled_date, day_of_week) = 1
        )
);


-- Duplicate guard, one index per arm of the exclusive arc. A single
-- UNIQUE across both columns would not work: NULL is never equal to
-- NULL, so the recurring rows would slip past it entirely.

CREATE UNIQUE INDEX IF NOT EXISTS uq_schedules_date
    ON schedules(user_id, scheduled_date, workout_day_id)
    WHERE scheduled_date IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_schedules_day_of_week
    ON schedules(user_id, day_of_week, workout_day_id)
    WHERE day_of_week IS NOT NULL;


-- ============================================================
-- 6. INDEXES
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_workout_plans_user
    ON workout_plans(user_id);

CREATE INDEX IF NOT EXISTS idx_workout_days_plan
    ON workout_days(plan_id);

CREATE INDEX IF NOT EXISTS idx_workout_exercises_day
    ON workout_exercises(workout_day_id);

CREATE INDEX IF NOT EXISTS idx_workout_exercises_exercise
    ON workout_exercises(exercise_id);

CREATE INDEX IF NOT EXISTS idx_workout_sets_exercise
    ON workout_exercise_sets(workout_exercise_id);

CREATE INDEX IF NOT EXISTS idx_schedules_user
    ON schedules(user_id);

CREATE INDEX IF NOT EXISTS idx_schedules_date
    ON schedules(scheduled_date);

CREATE INDEX IF NOT EXISTS idx_schedules_day_of_week
    ON schedules(day_of_week);

CREATE INDEX IF NOT EXISTS idx_schedules_workout_day
    ON schedules(workout_day_id);


-- ============================================================
-- 7. UPDATED_AT TRIGGER
-- ============================================================
-- Reuses set_updated_at() from ddl_phase1.sql instead of declaring a
-- second function that does the same thing. CREATE OR REPLACE keeps
-- this script runnable on its own.

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


DROP TRIGGER IF EXISTS trg_workout_plans_updated_at
ON workout_plans;

CREATE TRIGGER trg_workout_plans_updated_at
BEFORE UPDATE ON workout_plans
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


DROP TRIGGER IF EXISTS trg_workout_days_updated_at
ON workout_days;

CREATE TRIGGER trg_workout_days_updated_at
BEFORE UPDATE ON workout_days
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


DROP TRIGGER IF EXISTS trg_workout_exercises_updated_at
ON workout_exercises;

CREATE TRIGGER trg_workout_exercises_updated_at
BEFORE UPDATE ON workout_exercises
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


DROP TRIGGER IF EXISTS trg_workout_exercise_sets_updated_at
ON workout_exercise_sets;

CREATE TRIGGER trg_workout_exercise_sets_updated_at
BEFORE UPDATE ON workout_exercise_sets
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


DROP TRIGGER IF EXISTS trg_schedules_updated_at
ON schedules;

CREATE TRIGGER trg_schedules_updated_at
BEFORE UPDATE ON schedules
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
