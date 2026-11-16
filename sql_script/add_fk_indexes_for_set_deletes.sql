-- Indexes on the child side of three foreign keys, so deleting a set does not
-- scan a whole table.
--
-- Postgres indexes the referenced side of a foreign key, never the referencing
-- one. ON DELETE SET NULL therefore has to find the children some other way,
-- and with no index that is a sequential scan per deleted row:
--
--   delete a logged set      scans personal_records and personal_bests
--   delete a plan's set      scans session_sets
--
-- This cost nothing while the tables held a few thousand rows. With 490,000
-- session_sets on hosted (2026-11-09, see seed_fake_users.py in CLAUDE.md) it
-- was measured at 45-75 ms per row warm and over a second cold, paid once per
-- set: replacing a day's exercises or deleting a workout deletes a dozen or more.
-- With these: about 0.3 ms warm and 30 ms cold, for 10 MB of index in total.
--
-- Partial, because a NULL here means the link was already cleared and nothing
-- will ever look it up. The foreign key's own lookup is `$1 = column`, which
-- implies NOT NULL, so the planner can still use these.
--
-- CONCURRENTLY so that writes are not blocked while they build. It cannot run
-- inside a transaction:
--   * psql: run the file as it is, WITHOUT --single-transaction.
--   * Supabase SQL Editor: it wraps statements in a transaction, so remove the
--     word CONCURRENTLY there, and expect brief write blocking on these tables.
--
-- A concurrent build that fails leaves an INVALID index behind, which
-- IF NOT EXISTS would then skip. Check afterwards:
--   SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;
--
-- Undo:
--   DROP INDEX CONCURRENTLY idx_session_sets_planned_set;
--   DROP INDEX CONCURRENTLY idx_personal_records_set;
--   DROP INDEX CONCURRENTLY idx_personal_bests_session_set;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_session_sets_planned_set
    ON session_sets (workout_exercise_set_id)
    WHERE workout_exercise_set_id IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_personal_records_set
    ON personal_records (set_id)
    WHERE set_id IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_personal_bests_session_set
    ON personal_bests (session_set_id)
    WHERE session_set_id IS NOT NULL;
