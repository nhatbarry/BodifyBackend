-- ============================================================
-- BODIFY - PHASE 3
-- WORKOUT SESSION + SESSION EXERCISE + SESSION SET
--
-- Phase 2 records what the user *plans* to do. Phase 3 records what they
-- actually did. The two never share a row: a plan is edited freely for months,
-- while a finished session is a statement about one afternoon and must not
-- change underneath the user.
--
-- Assumption from earlier phases:
--   auth.users(id)               -- Supabase Auth, UUID primary key
--   exercises(exercise_id)
--   workout_days(workout_day_id)
--   workout_exercise_sets(workout_exercise_set_id)
-- ============================================================


-- ============================================================
-- 0. REPLACE THE SUPERSEDED PHASE 1 TABLES
-- ============================================================
-- ddl_phase1.sql sections 18-20 defined workout_sessions / session_exercises /
-- exercise_sets against the design that was later dropped: user_id BIGINT
-- referencing a public.users table that was never created, and template_id
-- referencing workout_templates, which Phase 2 replaced. They were applied but
-- never written to.
--
-- The guard below refuses to drop them if that assumption is wrong. Losing
-- somebody's training history to a migration would be unrecoverable, so this
-- fails loudly rather than trusting a comment.

DO $$
DECLARE
    -- Counted one table at a time: after this script has run once,
    -- exercise_sets is gone, and a guard that named it unconditionally would
    -- make the script fail on its second run instead of being a no-op.
    candidate TEXT;
    found     BIGINT;
    total     BIGINT := 0;
BEGIN
    FOREACH candidate IN ARRAY ARRAY['workout_sessions', 'session_exercises',
                                     'exercise_sets', 'session_sets']
    LOOP
        IF to_regclass('public.' || candidate) IS NOT NULL THEN
            EXECUTE format('SELECT count(*) FROM %I', candidate) INTO found;
            total := total + found;
        END IF;
    END LOOP;

    IF total > 0 THEN
        RAISE EXCEPTION
            'Refusing to drop the session tables: they hold % rows. '
            'Migrate that data before running this script.', total;
    END IF;
END
$$;

-- personal_records points at the old exercise_sets. It is empty and belongs to
-- a later phase; the constraint is dropped so the table survives, rather than
-- cascading the table away.
ALTER TABLE IF EXISTS personal_records
    DROP CONSTRAINT IF EXISTS fk_personal_records_set;

DROP TABLE IF EXISTS exercise_sets;
DROP TABLE IF EXISTS session_sets;
DROP TABLE IF EXISTS session_exercises;
DROP TABLE IF EXISTS workout_sessions;


-- ============================================================
-- 1. WORKOUT SESSIONS
-- ============================================================
-- One training session: a start time, an end time once finished, and the
-- exercises performed in between.
--
-- workout_day_id is ON DELETE SET NULL, not CASCADE: deleting a plan must
-- never erase the record of workouts already done from it. plan_name and
-- day_name are snapshots taken when the session starts, so the history still
-- reads correctly after the plan is renamed or removed entirely - that is the
-- "historical records stay stable" rule, and a foreign key alone cannot give
-- it.

CREATE TABLE IF NOT EXISTS workout_sessions (
    session_id       BIGSERIAL PRIMARY KEY,

    user_id          UUID NOT NULL,

    workout_day_id   BIGINT,

    -- Snapshots. Nullable: a session can be started from scratch, with no plan
    -- behind it at all.
    plan_name        VARCHAR(150),
    day_name         VARCHAR(150),

    -- The calendar date this session belongs to, which is not always the date
    -- it was recorded - a late-night workout logged after midnight still
    -- belongs to the day the user considers it.
    scheduled_date   DATE,

    started_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at     TIMESTAMPTZ,

    status           VARCHAR(20) NOT NULL DEFAULT 'in_progress',

    notes            TEXT,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_workout_session_day
        FOREIGN KEY (workout_day_id)
        REFERENCES workout_days(workout_day_id)
        ON DELETE SET NULL,

    CONSTRAINT chk_workout_session_status
        CHECK (status IN ('in_progress', 'completed', 'abandoned')),

    CONSTRAINT chk_workout_session_times
        CHECK (completed_at IS NULL OR completed_at >= started_at),

    -- "Completed" and "has an end time" are the same fact stated twice; letting
    -- them disagree would make duration nonsense for exactly the rows that
    -- matter. Duration itself is never stored - it is computed from these two,
    -- the same reason BMI is not stored.
    CONSTRAINT chk_workout_session_completion
        CHECK ((status = 'completed') = (completed_at IS NOT NULL))
);


-- At most one session in progress per user. Two devices cannot both be
-- recording, and "resume my workout" has exactly one answer. Not deferrable:
-- two in-progress rows must never exist, even between statements.
CREATE UNIQUE INDEX IF NOT EXISTS uq_workout_sessions_one_active
    ON workout_sessions(user_id)
    WHERE status = 'in_progress';


-- ============================================================
-- 2. SESSION EXERCISES
-- ============================================================
-- exercise_name is snapshotted from the catalog translation at the moment the
-- exercise is recorded. Names live only in exercise_translations and can be
-- corrected at any time; a session from March should keep saying what it said
-- in March. exercise_id is kept alongside it so "how has my bench press gone
-- this year" is still one join.

CREATE TABLE IF NOT EXISTS session_exercises (
    session_exercise_id   BIGSERIAL PRIMARY KEY,

    session_id            BIGINT NOT NULL,

    exercise_id           BIGINT NOT NULL,
    exercise_name         VARCHAR(200),

    exercise_order        INTEGER NOT NULL,

    notes                 TEXT,

    created_at            TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_session_exercise_session
        FOREIGN KEY (session_id)
        REFERENCES workout_sessions(session_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_session_exercise_exercise
        FOREIGN KEY (exercise_id)
        REFERENCES exercises(exercise_id)
        ON DELETE RESTRICT,

    CONSTRAINT chk_session_exercise_order
        CHECK (exercise_order > 0),

    CONSTRAINT uq_session_exercise_order
        UNIQUE (session_id, exercise_order)
        DEFERRABLE INITIALLY DEFERRED
);


-- ============================================================
-- 3. SESSION SETS
-- ============================================================
-- What was actually lifted. The target columns are a *copy* of the plan's
-- numbers at the time of the set, not a reference to them: the plan is a
-- living document and editing next week's target must not silently rewrite
-- last week's "you aimed for 40, you did 45".
--
-- workout_exercise_set_id keeps the link for anyone who wants it, but is
-- ON DELETE SET NULL, so deleting a plan leaves the history intact and merely
-- unlinked.

CREATE TABLE IF NOT EXISTS session_sets (
    session_set_id          BIGSERIAL PRIMARY KEY,

    session_exercise_id     BIGINT NOT NULL,

    set_order               INTEGER NOT NULL,

    -- Performed.
    reps                    INTEGER,
    weight_kg               NUMERIC(7,2),
    duration_seconds        INTEGER,
    rest_seconds            INTEGER,
    completed               BOOLEAN NOT NULL DEFAULT TRUE,

    -- Planned, snapshotted.
    target_reps             INTEGER,
    target_weight_kg        NUMERIC(7,2),
    workout_exercise_set_id BIGINT,

    notes                   TEXT,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_session_set_exercise
        FOREIGN KEY (session_exercise_id)
        REFERENCES session_exercises(session_exercise_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_session_set_planned
        FOREIGN KEY (workout_exercise_set_id)
        REFERENCES workout_exercise_sets(workout_exercise_set_id)
        ON DELETE SET NULL,

    CONSTRAINT chk_session_set_order
        CHECK (set_order > 0),

    CONSTRAINT chk_session_set_reps
        CHECK (reps IS NULL OR reps >= 0),

    CONSTRAINT chk_session_set_weight
        CHECK (weight_kg IS NULL OR weight_kg >= 0),

    CONSTRAINT chk_session_set_duration
        CHECK (duration_seconds IS NULL OR duration_seconds >= 0),

    CONSTRAINT chk_session_set_rest
        CHECK (rest_seconds IS NULL OR rest_seconds >= 0),

    CONSTRAINT chk_session_set_target_reps
        CHECK (target_reps IS NULL OR target_reps > 0),

    CONSTRAINT chk_session_set_target_weight
        CHECK (target_weight_kg IS NULL OR target_weight_kg >= 0),

    CONSTRAINT uq_session_set_order
        UNIQUE (session_exercise_id, set_order)
        DEFERRABLE INITIALLY DEFERRED
);


-- ============================================================
-- 4. INDEXES
-- ============================================================

-- The history screen reads "my sessions, newest first".
CREATE INDEX IF NOT EXISTS idx_workout_sessions_user_date
    ON workout_sessions(user_id, scheduled_date DESC, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_workout_sessions_day
    ON workout_sessions(workout_day_id);

CREATE INDEX IF NOT EXISTS idx_session_exercises_session
    ON session_exercises(session_id);

-- "How has this exercise gone over time" reads by exercise across sessions.
CREATE INDEX IF NOT EXISTS idx_session_exercises_exercise
    ON session_exercises(exercise_id);

CREATE INDEX IF NOT EXISTS idx_session_sets_exercise
    ON session_sets(session_exercise_id);


-- ============================================================
-- 5. UPDATED_AT TRIGGER
-- ============================================================
-- Reuses set_updated_at() from ddl_phase1.sql.

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


DROP TRIGGER IF EXISTS trg_workout_sessions_updated_at ON workout_sessions;
CREATE TRIGGER trg_workout_sessions_updated_at
BEFORE UPDATE ON workout_sessions
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_session_exercises_updated_at ON session_exercises;
CREATE TRIGGER trg_session_exercises_updated_at
BEFORE UPDATE ON session_exercises
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_session_sets_updated_at ON session_sets;
CREATE TRIGGER trg_session_sets_updated_at
BEFORE UPDATE ON session_sets
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
