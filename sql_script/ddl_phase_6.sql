-- ============================================================
-- BODIFY - PHASE 6 (đợt 1: chat)
-- AI COACH: CONVERSATIONS AND THEIR MESSAGES
--
-- Two tables and nothing clever. A session owns an ordered list of messages;
-- a message was said either by the user or by the coach. What the coach is
-- allowed to read about the user, and how the prompt is built, is deliberately
-- not here - that is the next slice, and it changes nothing about these rows.
--
-- The model is called from the API, never from the app: the key lives in the
-- server's environment, and history has to be assembled from the database
-- rather than from whatever a client claims was said earlier.
--
-- Assumptions from earlier phases:
--   auth.users(id)    Supabase Auth, UUID PK
--
-- Idempotent: every statement is guarded, so re-running is a no-op.
-- Rollback: sql_script/rollback_phase_6.sql
-- ============================================================


-- ============================================================
-- 1. CONVERSATIONS
-- ============================================================
-- `title` is derived from the first thing the user asked, on the server, so
-- every client shows the same name for the same conversation. Stored rather
-- than computed on read because the first message can later be deleted, and a
-- history row that renames itself is a history row nobody trusts.

CREATE TABLE IF NOT EXISTS chat_sessions (
    chat_session_id BIGSERIAL PRIMARY KEY,

    user_id    UUID         NOT NULL
               REFERENCES auth.users (id) ON DELETE CASCADE,

    title      VARCHAR(120) NOT NULL DEFAULT '',

    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- Bumped on every message, because "most recent conversation" is the only
    -- order a chat history is ever read in.
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_user
    ON chat_sessions (user_id, updated_at DESC);


-- ============================================================
-- 2. MESSAGES
-- ============================================================
-- CASCADE from the session, unlike everything in Phase 3: a workout is a
-- record of something that happened and outlives the plan it came from, but a
-- message outside its conversation is not evidence of anything.
--
-- `role` is the vocabulary of this application - 'user' and 'assistant' - not
-- whatever the model provider happens to call them. Gemini says "model"; a
-- different provider says something else; mapping at the boundary keeps a
-- provider swap out of the database.

CREATE TABLE IF NOT EXISTS chat_messages (
    chat_message_id BIGSERIAL PRIMARY KEY,

    chat_session_id BIGINT      NOT NULL
                    REFERENCES chat_sessions (chat_session_id) ON DELETE CASCADE,

    role            VARCHAR(16) NOT NULL,
    content         TEXT        NOT NULL,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_chat_message_role CHECK (role IN ('user', 'assistant')),
    CONSTRAINT chk_chat_message_content CHECK (length(content) > 0)
);

-- Every read of a conversation is "this session's messages, oldest first".
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages (chat_session_id, created_at);


COMMENT ON TABLE chat_sessions IS
    'One AI coach conversation. Ordered by updated_at, which every message bumps.';
COMMENT ON TABLE chat_messages IS
    'Messages in a conversation. role is this app''s vocabulary (user/assistant), '
    'mapped to whatever the model provider calls them at the boundary.';


-- ============================================================
-- 3. VERIFY
-- ============================================================
DO $$
DECLARE
    n_sessions BIGINT;
    n_messages BIGINT;
BEGIN
    SELECT count(*) INTO n_sessions FROM chat_sessions;
    SELECT count(*) INTO n_messages FROM chat_messages;

    RAISE NOTICE 'chat_sessions: % row(s)', n_sessions;
    RAISE NOTICE 'chat_messages: % row(s)', n_messages;
END $$;
