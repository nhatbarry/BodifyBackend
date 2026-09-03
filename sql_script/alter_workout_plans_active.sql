-- ============================================================
-- BODIFY - PHASE 2.1
-- "The schedule I am currently following"
-- ============================================================
-- A user keeps several plans but follows exactly one at a time, so the app
-- needs a single flag it can trust rather than a convention.
--
-- status ('active' / 'archived') cannot carry this: it answers "is this plan
-- still relevant", which is true of every plan the user has not archived. The
-- two questions are different, so they get two columns - a plan can be
-- inactive without being archived.
--
-- The partial unique index is what actually enforces "only one". Application
-- code alone would let two devices activate two plans in the same instant.
-- It indexes only the rows where is_active is true, so any number of plans may
-- sit at false.
-- ============================================================

ALTER TABLE workout_plans
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT FALSE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_workout_plans_one_active
    ON workout_plans(user_id)
    WHERE is_active;

-- Activation is two statements in one transaction: clear the user's current
-- active plan, then set the new one. Order matters - doing it the other way
-- round trips the index, since two rows would be active between statements.
