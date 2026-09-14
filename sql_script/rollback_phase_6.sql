-- ============================================================
-- BODIFY - ROLLBACK OF ddl_phase_6.sql
--
-- Drops the AI coach's conversations and every message in them.
--
-- DESTRUCTIVE and unrecoverable. Unlike the Phase 5 rollback, there is no
-- narrower option here: the tables *are* the feature. Dump first if any real
-- conversation has happened:
--
--   pg_dump --data-only --table=chat_sessions --table=chat_messages <url> \
--     > chat_backup.sql
--
-- Messages go first, though the FK would cascade anyway - dropping in
-- dependency order keeps the script readable and means a half-run leaves a
-- consistent database rather than an orphaned child table.
-- ============================================================

DO $$
DECLARE
    n_sessions BIGINT := 0;
    n_messages BIGINT := 0;
BEGIN
    IF to_regclass('public.chat_sessions') IS NOT NULL THEN
        SELECT count(*) INTO n_sessions FROM chat_sessions;
    END IF;
    IF to_regclass('public.chat_messages') IS NOT NULL THEN
        SELECT count(*) INTO n_messages FROM chat_messages;
    END IF;

    IF n_sessions > 0 OR n_messages > 0 THEN
        RAISE WARNING 'dropping % conversation(s) and % message(s) - unrecoverable',
                      n_sessions, n_messages;
    END IF;
END $$;

DROP TABLE IF EXISTS chat_messages;
DROP TABLE IF EXISTS chat_sessions;
