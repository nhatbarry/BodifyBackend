-- ============================================================
-- BODIFY - MEAL PHOTO ANALYSIS
--
-- One row per photo the model was asked about. It is what three things read:
--
--   * the per-user daily limit - COUNT(*) over the last 24 hours, never a
--     counter the application increments (the Phase 4 rule);
--   * an audit of what the model said, kept beside what the user then saved,
--     so "how often is the first guess right" is answerable later;
--   * meal_items.photo_analysis_id, which marks an entry as having come from
--     a photo rather than from typing.
--
-- The analysis never writes meal_items itself: it returns a draft, the user
-- confirms it, and the existing /meal-items endpoints write the rows. So a
-- meal item points at its analysis, not the other way round.
--
-- meal_id is SET NULL: deleting a meal must not erase the record of a request
-- that was made and paid for.
--
-- Expand-only and idempotent. Rollback: sql_script/rollback_meal_photo_analysis.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS meal_photo_analyses (
    analysis_id     BIGSERIAL     PRIMARY KEY,
    user_id         UUID          NOT NULL
                    REFERENCES auth.users (id) ON DELETE CASCADE,
    meal_id         BIGINT
                    REFERENCES meals (meal_id) ON DELETE SET NULL,
    photo_key       TEXT,
    model           VARCHAR(80)   NOT NULL,
    status          VARCHAR(10)   NOT NULL,
    result          JSONB,
    error           TEXT,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT chk_meal_photo_analyses_status CHECK (status IN ('ok', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_meal_photo_analyses_user_created
    ON meal_photo_analyses (user_id, created_at);

ALTER TABLE meal_items
    ADD COLUMN IF NOT EXISTS photo_analysis_id BIGINT
        REFERENCES meal_photo_analyses (analysis_id) ON DELETE SET NULL;

-- Partial: almost every item was typed or picked, and the index exists only so
-- deleting an analysis does not scan meal_items for the SET NULL.
CREATE INDEX IF NOT EXISTS idx_meal_items_photo_analysis
    ON meal_items (photo_analysis_id)
    WHERE photo_analysis_id IS NOT NULL;
