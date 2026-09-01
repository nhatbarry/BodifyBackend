-- ============================================================
-- BODIFY FITNESS APP
-- PHASE 1 - CORE DATABASE SCHEMA
-- PostgreSQL 16+
-- ============================================================

BEGIN;

-- ============================================================
-- 1. USERS
-- ============================================================
-- NOTE (superseded): the live database does NOT have this table.
-- Auth is handled by Supabase Auth (auth.users, UUID primary key).
-- Every user_id column below (user_profiles, body_metrics, and all
-- other phase-1+ tables) is UUID, referencing auth.users(id), not
-- this BIGSERIAL table. This block is kept only as historical
-- record of the original design and is not applied to the DB.
-- ============================================================

CREATE TABLE users (
    user_id BIGSERIAL PRIMARY KEY,

    email VARCHAR(255) NOT NULL UNIQUE,
    username VARCHAR(100) UNIQUE,

    auth_provider VARCHAR(30) NOT NULL DEFAULT 'local',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_users_auth_provider
        CHECK (auth_provider IN ('local', 'google'))
);


-- ============================================================
-- 2. USER PROFILES
-- ============================================================
-- user_id is UUID in the live DB (references Supabase auth.users.id).
-- role lives here (not on a local users table) since profile is the
-- table this backend actually owns.

CREATE TABLE user_profiles (
    profile_id BIGSERIAL PRIMARY KEY,

    user_id UUID NOT NULL UNIQUE,

    name VARCHAR(150) NOT NULL,

    avatar_url TEXT,

    date_of_birth DATE,

    gender VARCHAR(30),

    role VARCHAR(20) NOT NULL DEFAULT 'user',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_user_profiles_user
        FOREIGN KEY (user_id)
        REFERENCES auth.users(id)
        ON DELETE CASCADE,

    CONSTRAINT chk_user_profiles_role
        CHECK (role IN ('user', 'admin'))
);


-- ============================================================
-- 3. USER PREFERENCES
-- ============================================================

CREATE TABLE user_preferences (
    preference_id BIGSERIAL PRIMARY KEY,

    user_id BIGINT NOT NULL UNIQUE,

    language VARCHAR(10) NOT NULL DEFAULT 'vi',

    units VARCHAR(20) NOT NULL DEFAULT 'metric',

    notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_user_preferences_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_user_preferences_units
        CHECK (units IN ('metric', 'imperial'))
);


-- ============================================================
-- 4. BODY METRICS
-- ============================================================

CREATE TABLE body_metrics (
    metric_id BIGSERIAL PRIMARY KEY,

    user_id BIGINT NOT NULL,

    height_cm NUMERIC(5,2),

    weight_kg NUMERIC(6,2),

    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_body_metrics_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_body_metrics_height
        CHECK (height_cm IS NULL OR height_cm > 0),

    CONSTRAINT chk_body_metrics_weight
        CHECK (weight_kg IS NULL OR weight_kg > 0)
);


-- ============================================================
-- 5. MUSCLE GROUPS
-- ============================================================

CREATE TABLE muscle_groups (
    muscle_id BIGSERIAL PRIMARY KEY,

    name VARCHAR(100) NOT NULL UNIQUE,

    parent_id BIGINT,

    description TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_muscle_groups_parent
        FOREIGN KEY (parent_id)
        REFERENCES muscle_groups(muscle_id)
        ON DELETE SET NULL
);


-- ============================================================
-- 6. EQUIPMENT
-- ============================================================

CREATE TABLE equipment (
    equipment_id BIGSERIAL PRIMARY KEY,

    name VARCHAR(100) NOT NULL UNIQUE,

    category VARCHAR(50),

    description TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- 7. EXERCISES
-- ============================================================

CREATE TABLE exercises (
    exercise_id BIGSERIAL PRIMARY KEY,

    exercise_type VARCHAR(50),

    mechanics VARCHAR(50),

    force_type VARCHAR(50),

    experience_level VARCHAR(30),

    overview TEXT,

    instructions TEXT,

    tips TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- 8. EXERCISE TRANSLATIONS
-- ============================================================

CREATE TABLE exercise_translations (
    translation_id BIGSERIAL PRIMARY KEY,

    exercise_id BIGINT NOT NULL,

    language VARCHAR(10) NOT NULL,

    name VARCHAR(255) NOT NULL,

    overview TEXT,

    instructions TEXT,

    tips TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_exercise_translations_exercise
        FOREIGN KEY (exercise_id)
        REFERENCES exercises(exercise_id)
        ON DELETE CASCADE,

    CONSTRAINT uq_exercise_translation_language
        UNIQUE (exercise_id, language)
);


-- ============================================================
-- 9. EXERCISE - MUSCLE GROUP
-- ============================================================

CREATE TABLE exercise_muscles (
    exercise_muscle_id BIGSERIAL PRIMARY KEY,

    exercise_id BIGINT NOT NULL,

    muscle_id BIGINT NOT NULL,

    role VARCHAR(20) NOT NULL DEFAULT 'primary',

    CONSTRAINT fk_exercise_muscles_exercise
        FOREIGN KEY (exercise_id)
        REFERENCES exercises(exercise_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_exercise_muscles_muscle
        FOREIGN KEY (muscle_id)
        REFERENCES muscle_groups(muscle_id)
        ON DELETE CASCADE,

    CONSTRAINT uq_exercise_muscle
        UNIQUE (exercise_id, muscle_id),

    CONSTRAINT chk_exercise_muscle_role
        CHECK (role IN ('primary', 'secondary'))
);


-- ============================================================
-- 10. EXERCISE - EQUIPMENT
-- ============================================================

CREATE TABLE exercise_equipment (
    exercise_equipment_id BIGSERIAL PRIMARY KEY,

    exercise_id BIGINT NOT NULL,

    equipment_id BIGINT NOT NULL,

    CONSTRAINT fk_exercise_equipment_exercise
        FOREIGN KEY (exercise_id)
        REFERENCES exercises(exercise_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_exercise_equipment_equipment
        FOREIGN KEY (equipment_id)
        REFERENCES equipment(equipment_id)
        ON DELETE CASCADE,

    CONSTRAINT uq_exercise_equipment
        UNIQUE (exercise_id, equipment_id)
);


-- ============================================================
-- 11. EXERCISE MEDIA
-- ============================================================

CREATE TABLE exercise_media (
    media_id BIGSERIAL PRIMARY KEY,

    exercise_id BIGINT NOT NULL,

    media_type VARCHAR(30) NOT NULL,

    storage_key TEXT NOT NULL,

    sort_order INTEGER NOT NULL DEFAULT 0,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_exercise_media_exercise
        FOREIGN KEY (exercise_id)
        REFERENCES exercises(exercise_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_exercise_media_type
        CHECK (media_type IN ('thumbnail', 'video', 'image')),

    CONSTRAINT chk_exercise_media_sort_order
        CHECK (sort_order >= 0)
);


-- ============================================================
-- 12. FITNESS GOALS
-- ============================================================

CREATE TABLE fitness_goals (
    goal_id BIGSERIAL PRIMARY KEY,

    goal_type VARCHAR(50) NOT NULL UNIQUE,

    name VARCHAR(100) NOT NULL,

    unit VARCHAR(30),

    description TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- 13. USER GOALS
-- ============================================================

CREATE TABLE user_goals (
    user_goal_id BIGSERIAL PRIMARY KEY,

    user_id BIGINT NOT NULL,

    goal_id BIGINT NOT NULL,

    target_value NUMERIC(10,2),

    current_value NUMERIC(10,2),

    start_date DATE NOT NULL DEFAULT CURRENT_DATE,

    target_date DATE,

    status VARCHAR(20) NOT NULL DEFAULT 'active',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_user_goals_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_user_goals_goal
        FOREIGN KEY (goal_id)
        REFERENCES fitness_goals(goal_id)
        ON DELETE RESTRICT,

    CONSTRAINT chk_user_goals_status
        CHECK (status IN ('active', 'completed', 'cancelled')),

    CONSTRAINT chk_user_goals_target_date
        CHECK (
            target_date IS NULL
            OR target_date >= start_date
        )
);


-- ============================================================
-- 14. WORKOUT TEMPLATES
-- ============================================================

CREATE TABLE workout_templates (
    template_id BIGSERIAL PRIMARY KEY,

    user_id BIGINT,

    name VARCHAR(150) NOT NULL,

    description TEXT,

    difficulty VARCHAR(30),

    estimated_minutes INTEGER,

    is_public BOOLEAN NOT NULL DEFAULT FALSE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_workout_templates_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_workout_templates_duration
        CHECK (
            estimated_minutes IS NULL
            OR estimated_minutes > 0
        )
);


-- ============================================================
-- 15. WORKOUT TEMPLATE EXERCISES
-- ============================================================

CREATE TABLE workout_template_exercises (
    template_exercise_id BIGSERIAL PRIMARY KEY,

    template_id BIGINT NOT NULL,

    exercise_id BIGINT NOT NULL,

    order_no INTEGER NOT NULL,

    target_sets INTEGER,

    target_reps VARCHAR(30),

    target_duration_seconds INTEGER,

    rest_seconds INTEGER,

    notes TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_template_exercises_template
        FOREIGN KEY (template_id)
        REFERENCES workout_templates(template_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_template_exercises_exercise
        FOREIGN KEY (exercise_id)
        REFERENCES exercises(exercise_id)
        ON DELETE RESTRICT,

    CONSTRAINT uq_template_exercise_order
        UNIQUE (template_id, order_no),

    CONSTRAINT chk_template_exercise_order
        CHECK (order_no > 0),

    CONSTRAINT chk_template_exercise_sets
        CHECK (
            target_sets IS NULL
            OR target_sets > 0
        ),

    CONSTRAINT chk_template_exercise_duration
        CHECK (
            target_duration_seconds IS NULL
            OR target_duration_seconds > 0
        ),

    CONSTRAINT chk_template_exercise_rest
        CHECK (
            rest_seconds IS NULL
            OR rest_seconds >= 0
        )
);


-- ============================================================
-- 16. WORKOUT SCHEDULES
-- ============================================================

CREATE TABLE workout_schedules (
    schedule_id BIGSERIAL PRIMARY KEY,

    user_id BIGINT NOT NULL,

    name VARCHAR(150) NOT NULL,

    start_date DATE NOT NULL,

    end_date DATE,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_workout_schedules_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_workout_schedule_dates
        CHECK (
            end_date IS NULL
            OR end_date >= start_date
        )
);


-- ============================================================
-- 17. WORKOUT SCHEDULE DAYS
-- ============================================================

CREATE TABLE workout_schedule_days (
    schedule_day_id BIGSERIAL PRIMARY KEY,

    schedule_id BIGINT NOT NULL,

    day_of_week SMALLINT NOT NULL,

    template_id BIGINT,

    start_time TIME,

    is_rest_day BOOLEAN NOT NULL DEFAULT FALSE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_schedule_days_schedule
        FOREIGN KEY (schedule_id)
        REFERENCES workout_schedules(schedule_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_schedule_days_template
        FOREIGN KEY (template_id)
        REFERENCES workout_templates(template_id)
        ON DELETE SET NULL,

    CONSTRAINT uq_schedule_day
        UNIQUE (schedule_id, day_of_week),

    CONSTRAINT chk_schedule_day_of_week
        CHECK (day_of_week BETWEEN 1 AND 7),

    CONSTRAINT chk_schedule_day_template
        CHECK (
            is_rest_day = TRUE
            OR template_id IS NOT NULL
        )
);


-- ============================================================
-- 18. WORKOUT SESSIONS
-- ============================================================

CREATE TABLE workout_sessions (
    session_id BIGSERIAL PRIMARY KEY,

    user_id BIGINT NOT NULL,

    template_id BIGINT,

    scheduled_date DATE,

    started_at TIMESTAMPTZ,

    completed_at TIMESTAMPTZ,

    status VARCHAR(20) NOT NULL DEFAULT 'planned',

    notes TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_workout_sessions_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_workout_sessions_template
        FOREIGN KEY (template_id)
        REFERENCES workout_templates(template_id)
        ON DELETE SET NULL,

    CONSTRAINT chk_workout_session_status
        CHECK (
            status IN (
                'planned',
                'in_progress',
                'completed',
                'skipped',
                'cancelled'
            )
        ),

    CONSTRAINT chk_workout_session_times
        CHECK (
            completed_at IS NULL
            OR started_at IS NULL
            OR completed_at >= started_at
        )
);


-- ============================================================
-- 19. SESSION EXERCISES
-- ============================================================

CREATE TABLE session_exercises (
    session_exercise_id BIGSERIAL PRIMARY KEY,

    session_id BIGINT NOT NULL,

    exercise_id BIGINT NOT NULL,

    order_no INTEGER NOT NULL,

    notes TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_session_exercises_session
        FOREIGN KEY (session_id)
        REFERENCES workout_sessions(session_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_session_exercises_exercise
        FOREIGN KEY (exercise_id)
        REFERENCES exercises(exercise_id)
        ON DELETE RESTRICT,

    CONSTRAINT uq_session_exercise_order
        UNIQUE (session_id, order_no),

    CONSTRAINT chk_session_exercise_order
        CHECK (order_no > 0)
);


-- ============================================================
-- 20. EXERCISE SETS
-- ============================================================

CREATE TABLE exercise_sets (
    set_id BIGSERIAL PRIMARY KEY,

    session_exercise_id BIGINT NOT NULL,

    set_no INTEGER NOT NULL,

    reps INTEGER,

    weight_kg NUMERIC(7,2),

    duration_seconds INTEGER,

    rest_seconds INTEGER,

    rpe NUMERIC(3,1),

    rir NUMERIC(3,1),

    completed BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_exercise_sets_session_exercise
        FOREIGN KEY (session_exercise_id)
        REFERENCES session_exercises(session_exercise_id)
        ON DELETE CASCADE,

    CONSTRAINT uq_exercise_set_no
        UNIQUE (session_exercise_id, set_no),

    CONSTRAINT chk_exercise_set_no
        CHECK (set_no > 0),

    CONSTRAINT chk_exercise_set_reps
        CHECK (
            reps IS NULL
            OR reps >= 0
        ),

    CONSTRAINT chk_exercise_set_weight
        CHECK (
            weight_kg IS NULL
            OR weight_kg >= 0
        ),

    CONSTRAINT chk_exercise_set_duration
        CHECK (
            duration_seconds IS NULL
            OR duration_seconds >= 0
        ),

    CONSTRAINT chk_exercise_set_rest
        CHECK (
            rest_seconds IS NULL
            OR rest_seconds >= 0
        ),

    CONSTRAINT chk_exercise_set_rpe
        CHECK (
            rpe IS NULL
            OR (rpe >= 0 AND rpe <= 10)
        ),

    CONSTRAINT chk_exercise_set_rir
        CHECK (
            rir IS NULL
            OR (rir >= 0 AND rir <= 10)
        )
);


-- ============================================================
-- 21. PERSONAL RECORDS
-- ============================================================

CREATE TABLE personal_records (
    pr_id BIGSERIAL PRIMARY KEY,

    user_id BIGINT NOT NULL,

    exercise_id BIGINT NOT NULL,

    record_type VARCHAR(30) NOT NULL,

    value NUMERIC(10,2) NOT NULL,

    unit VARCHAR(20) NOT NULL,

    achieved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    set_id BIGINT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_personal_records_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_personal_records_exercise
        FOREIGN KEY (exercise_id)
        REFERENCES exercises(exercise_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_personal_records_set
        FOREIGN KEY (set_id)
        REFERENCES exercise_sets(set_id)
        ON DELETE SET NULL,

    CONSTRAINT chk_personal_record_value
        CHECK (value >= 0)
);


-- ============================================================
-- 22. AI RECOMMENDATIONS
-- ============================================================

CREATE TABLE ai_recommendations (
    recommendation_id BIGSERIAL PRIMARY KEY,

    user_id BIGINT NOT NULL,

    recommendation_type VARCHAR(50) NOT NULL,

    title VARCHAR(255) NOT NULL,

    content TEXT NOT NULL,

    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    expires_at TIMESTAMPTZ,

    status VARCHAR(20) NOT NULL DEFAULT 'active',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_ai_recommendations_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_ai_recommendation_status
        CHECK (
            status IN (
                'active',
                'dismissed',
                'expired'
            )
        ),

    CONSTRAINT chk_ai_recommendation_expiry
        CHECK (
            expires_at IS NULL
            OR expires_at >= generated_at
        )
);


-- ============================================================
-- 23. AI FEEDBACK
-- ============================================================

CREATE TABLE ai_feedback (
    feedback_id BIGSERIAL PRIMARY KEY,

    recommendation_id BIGINT NOT NULL,

    user_id BIGINT NOT NULL,

    rating SMALLINT,

    feedback TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_ai_feedback_recommendation
        FOREIGN KEY (recommendation_id)
        REFERENCES ai_recommendations(recommendation_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_ai_feedback_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    CONSTRAINT chk_ai_feedback_rating
        CHECK (
            rating IS NULL
            OR rating BETWEEN 1 AND 5
        )
);


-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX idx_body_metrics_user_date
    ON body_metrics(user_id, recorded_at DESC);

CREATE INDEX idx_exercise_muscles_muscle
    ON exercise_muscles(muscle_id);

CREATE INDEX idx_exercise_equipment_equipment
    ON exercise_equipment(equipment_id);

CREATE INDEX idx_exercise_media_exercise
    ON exercise_media(exercise_id);

CREATE INDEX idx_user_goals_user_status
    ON user_goals(user_id, status);

CREATE INDEX idx_workout_templates_user
    ON workout_templates(user_id);

CREATE INDEX idx_workout_template_exercises_exercise
    ON workout_template_exercises(exercise_id);

CREATE INDEX idx_workout_schedules_user_active
    ON workout_schedules(user_id, is_active);

CREATE INDEX idx_workout_schedule_days_template
    ON workout_schedule_days(template_id);

CREATE INDEX idx_workout_sessions_user_date
    ON workout_sessions(user_id, scheduled_date DESC);

CREATE INDEX idx_workout_sessions_user_status
    ON workout_sessions(user_id, status);

CREATE INDEX idx_session_exercises_exercise
    ON session_exercises(exercise_id);

CREATE INDEX idx_exercise_sets_session_exercise
    ON exercise_sets(session_exercise_id);

CREATE INDEX idx_personal_records_user_exercise
    ON personal_records(user_id, exercise_id, achieved_at DESC);

CREATE INDEX idx_ai_recommendations_user_status
    ON ai_recommendations(user_id, status);

CREATE INDEX idx_ai_feedback_recommendation
    ON ai_feedback(recommendation_id);


-- ============================================================
-- UPDATED_AT TRIGGER
-- ============================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


CREATE TRIGGER trg_users_updated_at
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


CREATE TRIGGER trg_user_profiles_updated_at
BEFORE UPDATE ON user_profiles
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


CREATE TRIGGER trg_user_preferences_updated_at
BEFORE UPDATE ON user_preferences
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


CREATE TRIGGER trg_exercises_updated_at
BEFORE UPDATE ON exercises
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


CREATE TRIGGER trg_exercise_translations_updated_at
BEFORE UPDATE ON exercise_translations
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


CREATE TRIGGER trg_user_goals_updated_at
BEFORE UPDATE ON user_goals
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


CREATE TRIGGER trg_workout_templates_updated_at
BEFORE UPDATE ON workout_templates
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


CREATE TRIGGER trg_workout_schedules_updated_at
BEFORE UPDATE ON workout_schedules
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


CREATE TRIGGER trg_workout_sessions_updated_at
BEFORE UPDATE ON workout_sessions
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


-- ============================================================
-- FINISH
-- ============================================================

COMMIT;