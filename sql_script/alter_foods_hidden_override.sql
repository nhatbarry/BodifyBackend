-- ============================================================
-- BODIFY - admin console: an admin's show/hide choice on a food
--
-- `foods.is_hidden` is recomputed from scratch by hide_duplicate_foods.sql
-- on every run. Written directly by the admin console, an admin's choice
-- would be silently undone the next time that script ran after a reseed.
--
-- `hidden_override` is the admin's own word on a row:
--   NULL   follow the duplicate rule (every row, until an admin touches it)
--   true   hidden, whatever the rule says
--   false  shown, whatever the rule says
-- and hide_duplicate_foods.sql now sets is_hidden = coalesce(override, rule).
-- `is_hidden` stays the one column every read filters on.
--
-- Expand-only and idempotent: the deployed backend never reads the column.
-- Undo: ALTER TABLE foods DROP COLUMN hidden_override;
-- ============================================================

ALTER TABLE foods ADD COLUMN IF NOT EXISTS hidden_override BOOLEAN;

COMMENT ON COLUMN foods.hidden_override IS
    'Set by the admin console. NULL = follow hide_duplicate_foods.sql; '
    'true/false = hidden/shown regardless of it.';
