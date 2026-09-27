-- ============================================================
-- BODIFY - PHASE 8
-- DAILY HEALTH DATA FROM THE PHONE (Android Health Connect)
--
-- The backend cannot reach a phone's health store, so the app reads it on the
-- device and pushes it here. What is stored is one ROLL-UP per user per day,
-- not the raw samples: a heart-rate stream is thousands of rows a day. The
-- intraday heart-rate chart is drawn from the phone directly.
--
-- Three measures only: steps, distance, heart rate. Calories were dropped: a
-- phone without a watch cannot measure them, only guess.
--
-- UNIQUE (user_id, day) is what makes a re-sync safe: syncing the same day
-- twice as steps grow through it updates the row instead of stacking a second
-- one, and two devices syncing at once cannot both insert.
--
-- Every measure is nullable. A watch-less phone has steps and nothing else,
-- and 0 bpm is not "no reading".
--
-- Idempotent. Rollback: sql_script/rollback_phase_8.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS health_daily (
    health_daily_id     BIGSERIAL     PRIMARY KEY,
    user_id             UUID          NOT NULL
                        REFERENCES auth.users (id) ON DELETE CASCADE,
    day                 DATE          NOT NULL,

    steps               INTEGER,
    distance_m          NUMERIC(9, 1),
    heart_rate_avg      SMALLINT,
    heart_rate_min      SMALLINT,
    heart_rate_max      SMALLINT,

    source              VARCHAR(32)   NOT NULL,
    synced_at           TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT uq_health_daily_user_day UNIQUE (user_id, day),
    CONSTRAINT chk_health_daily_steps  CHECK (steps IS NULL OR steps >= 0),
    CONSTRAINT chk_health_daily_dist   CHECK (distance_m IS NULL OR distance_m >= 0),
    CONSTRAINT chk_health_daily_hr     CHECK (
        (heart_rate_avg IS NULL OR heart_rate_avg BETWEEN 20 AND 250)
        AND (heart_rate_min IS NULL OR heart_rate_min BETWEEN 20 AND 250)
        AND (heart_rate_max IS NULL OR heart_rate_max BETWEEN 20 AND 250)
    )
);
