-- ============================================================
-- BODIFY - AI COACH TOOLS
-- WHAT A COACH ANSWER CARRIES BESIDES ITS TEXT
--
-- The coach can now look things up (exercises, the calendar, progress) and
-- hand back two kinds of result the app draws as cards rather than prose:
-- exercises it suggests, and a workout schedule it proposes. Both are stored
-- on the assistant message that produced them, so reopening a conversation
-- shows the same cards - and a proposal keeps whether it was applied.
--
-- A proposal is never written to the plan by the model. It sits here as
-- `pending` until the user taps Apply, which goes through the same
-- per-date services the calendar uses. Same rule as the meal photo analysis:
-- the model drafts, the user writes.
--
-- JSONB rather than child tables: written by one request, read as a whole
-- with its message, never filtered on - the same reasoning as
-- session_sets.ai_rep_scores.
--
-- Expand-only, so the deployed backend keeps working against it.
-- Idempotent: re-running is a no-op.
-- Rollback: sql_script/rollback_coach_tools.sql
-- ============================================================

ALTER TABLE chat_messages
    ADD COLUMN IF NOT EXISTS attachments JSONB;

COMMENT ON COLUMN chat_messages.attachments IS
    'Cards an assistant answer carries: suggested exercises and a proposed '
    'workout schedule (with its pending/applied/dismissed status). NULL for '
    'plain text answers and for every user message.';


-- ============================================================
-- VERIFY
-- ============================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'chat_messages' AND column_name = 'attachments'
    ) THEN
        RAISE EXCEPTION 'chat_messages.attachments was not created';
    END IF;
    RAISE NOTICE 'chat_messages.attachments: ok';
END $$;
