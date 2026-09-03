-- ============================================================
-- BODIFY - PHASE 3.1: pausing a workout
--
-- A session is not one continuous block. People stop to take a call, move
-- gyms, or eat, and counting that as training time makes every duration a
-- lie. Pausing stops the clock, so the recorded duration is time actually
-- spent training.
--
-- Idempotent.
-- ============================================================

BEGIN;

-- 1. 'paused' joins the status vocabulary.
--
--    It is an *active* status, not a terminal one: the session is still
--    running, only the clock is not. Every rule that applies to an
--    in-progress session applies to a paused one, including the "one workout
--    at a time" index below.
ALTER TABLE workout_sessions
    DROP CONSTRAINT IF EXISTS chk_workout_session_status;

ALTER TABLE workout_sessions
    ADD CONSTRAINT chk_workout_session_status
    CHECK (status IN ('in_progress', 'paused', 'completed', 'abandoned'));


-- 2. Where the paused time is kept.
--
--    Two columns rather than one because they answer different questions:
--    paused_seconds is how much has already been sat out, paused_at is when
--    the current pause started and is NULL whenever the clock is running.
--    Elapsed time is then
--        (end - started_at) - paused_seconds - (now - paused_at, if paused)
--    which stays correct across app restarts, because none of it is held in
--    the client.
ALTER TABLE workout_sessions
    ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ;

ALTER TABLE workout_sessions
    ADD COLUMN IF NOT EXISTS paused_seconds INTEGER NOT NULL DEFAULT 0;

ALTER TABLE workout_sessions
    DROP CONSTRAINT IF EXISTS chk_workout_session_paused;

ALTER TABLE workout_sessions
    ADD CONSTRAINT chk_workout_session_paused
    CHECK (paused_seconds >= 0);

-- paused_at is only meaningful while the status says so. Letting a completed
-- row keep a dangling paused_at would make its duration drift every second.
ALTER TABLE workout_sessions
    DROP CONSTRAINT IF EXISTS chk_workout_session_pause_state;

ALTER TABLE workout_sessions
    ADD CONSTRAINT chk_workout_session_pause_state
    CHECK ((paused_at IS NOT NULL) = (status = 'paused'));


-- 3. "One workout at a time" now has to cover both active statuses.
--    Without this, pausing would free the slot and let a second session start,
--    leaving two half-finished workouts and no way to say which is current.
DROP INDEX IF EXISTS uq_workout_sessions_one_active;

CREATE UNIQUE INDEX IF NOT EXISTS uq_workout_sessions_one_active
    ON workout_sessions(user_id)
    WHERE status IN ('in_progress', 'paused');

COMMIT;
