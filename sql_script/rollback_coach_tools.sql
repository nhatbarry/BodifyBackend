-- ============================================================
-- ROLLBACK: AI COACH TOOLS
-- Undoes sql_script/ddl_coach_tools.sql.
--
-- Refuses while any message carries attachments: an applied proposal is the
-- only record of where a plan's dates came from, and suggestions are part of
-- a conversation the user can reopen. Clear them deliberately first if that
-- is really intended:  UPDATE chat_messages SET attachments = NULL;
-- ============================================================

DO $$
DECLARE
    n BIGINT;
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'chat_messages' AND column_name = 'attachments'
    ) THEN
        EXECUTE 'SELECT count(*) FROM chat_messages WHERE attachments IS NOT NULL'
           INTO n;
        IF n > 0 THEN
            RAISE EXCEPTION
                'Refusing: % message(s) carry attachments', n;
        END IF;
    END IF;
END $$;

ALTER TABLE chat_messages DROP COLUMN IF EXISTS attachments;
