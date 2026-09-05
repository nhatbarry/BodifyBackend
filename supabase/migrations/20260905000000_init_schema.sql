--
-- PostgreSQL database dump
--


-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

-- Supabase already provisions "public" - creating it again errors on apply.
CREATE SCHEMA IF NOT EXISTS public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: achievements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.achievements (
    achievement_id bigint NOT NULL,
    code character varying(50) NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    icon_url text,
    achievement_type character varying(30) NOT NULL,
    requirement_value numeric(10,2) NOT NULL,
    requirement_unit character varying(20) NOT NULL,
    exercise_id bigint,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_achievement_requirement CHECK ((requirement_value > (0)::numeric)),
    CONSTRAINT chk_achievement_type CHECK (((achievement_type)::text = ANY ((ARRAY['workout_count'::character varying, 'workout_streak'::character varying, 'total_volume'::character varying, 'exercise_duration'::character varying, 'personal_record'::character varying, 'plan_completion'::character varying])::text[])))
);


--
-- Name: achievements_achievement_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.achievements_achievement_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: achievements_achievement_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.achievements_achievement_id_seq OWNED BY public.achievements.achievement_id;


--
-- Name: ai_feedback; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_feedback (
    feedback_id bigint NOT NULL,
    recommendation_id bigint NOT NULL,
    user_id uuid NOT NULL,
    rating smallint,
    feedback text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_ai_feedback_rating CHECK (((rating IS NULL) OR ((rating >= 1) AND (rating <= 5))))
);


--
-- Name: ai_feedback_feedback_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ai_feedback_feedback_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ai_feedback_feedback_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ai_feedback_feedback_id_seq OWNED BY public.ai_feedback.feedback_id;


--
-- Name: ai_recommendations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_recommendations (
    recommendation_id bigint NOT NULL,
    user_id uuid NOT NULL,
    recommendation_type character varying(50) NOT NULL,
    title character varying(255) NOT NULL,
    content text NOT NULL,
    generated_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_ai_recommendation_expiry CHECK (((expires_at IS NULL) OR (expires_at >= generated_at))),
    CONSTRAINT chk_ai_recommendation_status CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'dismissed'::character varying, 'expired'::character varying])::text[])))
);


--
-- Name: ai_recommendations_recommendation_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ai_recommendations_recommendation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ai_recommendations_recommendation_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ai_recommendations_recommendation_id_seq OWNED BY public.ai_recommendations.recommendation_id;


--
-- Name: body_metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.body_metrics (
    metric_id bigint NOT NULL,
    user_id uuid NOT NULL,
    height_cm numeric(5,2),
    weight_kg numeric(6,2),
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_body_metrics_height CHECK (((height_cm IS NULL) OR (height_cm > (0)::numeric))),
    CONSTRAINT chk_body_metrics_weight CHECK (((weight_kg IS NULL) OR (weight_kg > (0)::numeric)))
);


--
-- Name: body_metrics_metric_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.body_metrics_metric_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: body_metrics_metric_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.body_metrics_metric_id_seq OWNED BY public.body_metrics.metric_id;


--
-- Name: equipment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.equipment (
    equipment_id bigint NOT NULL,
    name character varying(100) NOT NULL,
    category character varying(50),
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: equipment_equipment_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.equipment_equipment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: equipment_equipment_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.equipment_equipment_id_seq OWNED BY public.equipment.equipment_id;


--
-- Name: exercise_equipment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exercise_equipment (
    exercise_equipment_id bigint NOT NULL,
    exercise_id bigint NOT NULL,
    equipment_id bigint NOT NULL
);


--
-- Name: exercise_equipment_exercise_equipment_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.exercise_equipment_exercise_equipment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: exercise_equipment_exercise_equipment_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.exercise_equipment_exercise_equipment_id_seq OWNED BY public.exercise_equipment.exercise_equipment_id;


--
-- Name: exercise_media; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exercise_media (
    media_id bigint NOT NULL,
    exercise_id bigint NOT NULL,
    media_type character varying(30) NOT NULL,
    storage_key text NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_exercise_media_sort_order CHECK ((sort_order >= 0)),
    CONSTRAINT chk_exercise_media_type CHECK (((media_type)::text = ANY ((ARRAY['thumbnail'::character varying, 'video'::character varying, 'image'::character varying, 'icon'::character varying])::text[])))
);


--
-- Name: exercise_media_media_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.exercise_media_media_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: exercise_media_media_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.exercise_media_media_id_seq OWNED BY public.exercise_media.media_id;


--
-- Name: exercise_muscles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exercise_muscles (
    exercise_muscle_id bigint NOT NULL,
    exercise_id bigint NOT NULL,
    muscle_id bigint NOT NULL,
    role character varying(20) DEFAULT 'primary'::character varying NOT NULL,
    CONSTRAINT chk_exercise_muscle_role CHECK (((role)::text = ANY ((ARRAY['primary'::character varying, 'secondary'::character varying])::text[])))
);


--
-- Name: exercise_muscles_exercise_muscle_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.exercise_muscles_exercise_muscle_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: exercise_muscles_exercise_muscle_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.exercise_muscles_exercise_muscle_id_seq OWNED BY public.exercise_muscles.exercise_muscle_id;


--
-- Name: exercise_translations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exercise_translations (
    translation_id bigint NOT NULL,
    exercise_id bigint NOT NULL,
    language character varying(10) NOT NULL,
    name character varying(255) NOT NULL,
    overview text,
    instructions text,
    tips text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: exercise_translations_translation_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.exercise_translations_translation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: exercise_translations_translation_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.exercise_translations_translation_id_seq OWNED BY public.exercise_translations.translation_id;


--
-- Name: exercises; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exercises (
    exercise_id bigint NOT NULL,
    exercise_type character varying(50),
    mechanics character varying(50),
    force_type character varying(50),
    experience_level character varying(30),
    overview text,
    instructions text,
    tips text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    source_url text,
    tracking_type character varying(20) DEFAULT 'reps'::character varying NOT NULL,
    CONSTRAINT chk_exercises_tracking_type CHECK (((tracking_type)::text = ANY ((ARRAY['reps'::character varying, 'duration'::character varying, 'distance'::character varying, 'reps_duration'::character varying])::text[])))
);


--
-- Name: COLUMN exercises.tracking_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.exercises.tracking_type IS 'What the effort is counted in: reps, duration (seconds), distance (metres), or reps_duration (a repeated movement with a hold in it). Weight is orthogonal and applies to all four.';


--
-- Name: exercises_exercise_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.exercises_exercise_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: exercises_exercise_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.exercises_exercise_id_seq OWNED BY public.exercises.exercise_id;


--
-- Name: fitness_goals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fitness_goals (
    goal_id bigint NOT NULL,
    goal_type character varying(50) NOT NULL,
    name character varying(100) NOT NULL,
    unit character varying(30),
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: fitness_goals_goal_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fitness_goals_goal_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: fitness_goals_goal_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fitness_goals_goal_id_seq OWNED BY public.fitness_goals.goal_id;


--
-- Name: muscle_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.muscle_groups (
    muscle_id bigint NOT NULL,
    name character varying(100) NOT NULL,
    parent_id bigint,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: muscle_groups_muscle_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.muscle_groups_muscle_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: muscle_groups_muscle_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.muscle_groups_muscle_id_seq OWNED BY public.muscle_groups.muscle_id;


--
-- Name: personal_bests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personal_bests (
    personal_best_id bigint NOT NULL,
    user_id uuid NOT NULL,
    exercise_id bigint NOT NULL,
    record_type character varying(30) NOT NULL,
    value numeric(10,2) NOT NULL,
    unit character varying(20) NOT NULL,
    achieved_at timestamp with time zone DEFAULT now() NOT NULL,
    session_set_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_personal_best_type CHECK (((record_type)::text = ANY ((ARRAY['max_weight'::character varying, 'max_reps'::character varying, 'max_volume'::character varying, 'max_duration'::character varying, 'max_distance'::character varying, 'estimated_1rm'::character varying])::text[]))),
    CONSTRAINT chk_personal_best_value CHECK ((value >= (0)::numeric))
);


--
-- Name: personal_bests_personal_best_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.personal_bests_personal_best_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: personal_bests_personal_best_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.personal_bests_personal_best_id_seq OWNED BY public.personal_bests.personal_best_id;


--
-- Name: personal_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personal_records (
    pr_id bigint NOT NULL,
    user_id uuid NOT NULL,
    exercise_id bigint NOT NULL,
    record_type character varying(30) NOT NULL,
    value numeric(10,2) NOT NULL,
    unit character varying(20) NOT NULL,
    achieved_at timestamp with time zone DEFAULT now() NOT NULL,
    set_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_personal_record_type CHECK (((record_type)::text = ANY ((ARRAY['max_weight'::character varying, 'max_reps'::character varying, 'max_volume'::character varying, 'max_duration'::character varying, 'max_distance'::character varying, 'estimated_1rm'::character varying])::text[]))),
    CONSTRAINT chk_personal_record_value CHECK ((value >= (0)::numeric))
);


--
-- Name: personal_records_pr_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.personal_records_pr_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: personal_records_pr_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.personal_records_pr_id_seq OWNED BY public.personal_records.pr_id;


--
-- Name: schedules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedules (
    schedule_id bigint NOT NULL,
    user_id uuid NOT NULL,
    workout_day_id bigint NOT NULL,
    scheduled_date date,
    day_of_week smallint,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT chk_schedule_day_of_week CHECK (((day_of_week IS NULL) OR ((day_of_week >= 1) AND (day_of_week <= 7)))),
    CONSTRAINT chk_schedule_target CHECK ((num_nonnulls(scheduled_date, day_of_week) = 1))
);


--
-- Name: schedules_schedule_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.schedules_schedule_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: schedules_schedule_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.schedules_schedule_id_seq OWNED BY public.schedules.schedule_id;


--
-- Name: session_exercises; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.session_exercises (
    session_exercise_id bigint NOT NULL,
    session_id bigint NOT NULL,
    exercise_id bigint NOT NULL,
    exercise_name character varying(200),
    exercise_order integer NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT chk_session_exercise_order CHECK ((exercise_order > 0))
);


--
-- Name: session_exercises_session_exercise_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.session_exercises_session_exercise_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: session_exercises_session_exercise_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.session_exercises_session_exercise_id_seq OWNED BY public.session_exercises.session_exercise_id;


--
-- Name: session_sets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.session_sets (
    session_set_id bigint NOT NULL,
    session_exercise_id bigint NOT NULL,
    set_order integer NOT NULL,
    reps integer,
    weight_kg numeric(7,2),
    duration_seconds integer,
    rest_seconds integer,
    completed boolean DEFAULT true NOT NULL,
    target_reps integer,
    target_weight_kg numeric(7,2),
    workout_exercise_set_id bigint,
    notes text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    distance_m numeric(8,2),
    target_duration_seconds integer,
    target_distance_m numeric(8,2),
    CONSTRAINT chk_session_set_distance CHECK (((distance_m IS NULL) OR (distance_m >= (0)::numeric))),
    CONSTRAINT chk_session_set_duration CHECK (((duration_seconds IS NULL) OR (duration_seconds >= 0))),
    CONSTRAINT chk_session_set_order CHECK ((set_order > 0)),
    CONSTRAINT chk_session_set_reps CHECK (((reps IS NULL) OR (reps >= 0))),
    CONSTRAINT chk_session_set_rest CHECK (((rest_seconds IS NULL) OR (rest_seconds >= 0))),
    CONSTRAINT chk_session_set_target_distance CHECK (((target_distance_m IS NULL) OR (target_distance_m > (0)::numeric))),
    CONSTRAINT chk_session_set_target_duration CHECK (((target_duration_seconds IS NULL) OR (target_duration_seconds > 0))),
    CONSTRAINT chk_session_set_target_reps CHECK (((target_reps IS NULL) OR (target_reps > 0))),
    CONSTRAINT chk_session_set_target_weight CHECK (((target_weight_kg IS NULL) OR (target_weight_kg >= (0)::numeric))),
    CONSTRAINT chk_session_set_weight CHECK (((weight_kg IS NULL) OR (weight_kg >= (0)::numeric)))
);


--
-- Name: session_sets_session_set_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.session_sets_session_set_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: session_sets_session_set_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.session_sets_session_set_id_seq OWNED BY public.session_sets.session_set_id;


--
-- Name: term_translations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.term_translations (
    translation_id bigint NOT NULL,
    muscle_id bigint,
    equipment_id bigint,
    language character varying(10) NOT NULL,
    name character varying(150) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_term_translations_target CHECK ((num_nonnulls(muscle_id, equipment_id) = 1))
);


--
-- Name: term_translations_translation_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.term_translations_translation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: term_translations_translation_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.term_translations_translation_id_seq OWNED BY public.term_translations.translation_id;


--
-- Name: user_achievements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_achievements (
    user_achievement_id bigint NOT NULL,
    user_id uuid NOT NULL,
    achievement_id bigint NOT NULL,
    achieved_at timestamp with time zone DEFAULT now() NOT NULL,
    progress_value numeric(10,2)
);


--
-- Name: user_achievements_user_achievement_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_achievements_user_achievement_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_achievements_user_achievement_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_achievements_user_achievement_id_seq OWNED BY public.user_achievements.user_achievement_id;


--
-- Name: user_goals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_goals (
    user_goal_id bigint NOT NULL,
    user_id uuid NOT NULL,
    goal_id bigint NOT NULL,
    target_value numeric(10,2),
    current_value numeric(10,2),
    start_date date DEFAULT CURRENT_DATE NOT NULL,
    target_date date,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_user_goals_status CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'completed'::character varying, 'cancelled'::character varying])::text[]))),
    CONSTRAINT chk_user_goals_target_date CHECK (((target_date IS NULL) OR (target_date >= start_date)))
);


--
-- Name: user_goals_user_goal_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_goals_user_goal_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_goals_user_goal_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_goals_user_goal_id_seq OWNED BY public.user_goals.user_goal_id;


--
-- Name: user_preferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_preferences (
    preference_id bigint NOT NULL,
    user_id uuid NOT NULL,
    language character varying(10) DEFAULT 'vi'::character varying NOT NULL,
    units character varying(20) DEFAULT 'metric'::character varying NOT NULL,
    notifications_enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_user_preferences_units CHECK (((units)::text = ANY ((ARRAY['metric'::character varying, 'imperial'::character varying])::text[])))
);


--
-- Name: user_preferences_preference_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_preferences_preference_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_preferences_preference_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_preferences_preference_id_seq OWNED BY public.user_preferences.preference_id;


--
-- Name: user_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_profiles (
    profile_id bigint NOT NULL,
    user_id uuid NOT NULL,
    name character varying(150) NOT NULL,
    avatar_url text,
    date_of_birth date,
    gender character varying(30),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    role character varying(20) DEFAULT 'user'::character varying NOT NULL,
    CONSTRAINT chk_user_profiles_role CHECK (((role)::text = ANY ((ARRAY['user'::character varying, 'admin'::character varying])::text[])))
);


--
-- Name: user_profiles_profile_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_profiles_profile_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_profiles_profile_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_profiles_profile_id_seq OWNED BY public.user_profiles.profile_id;


--
-- Name: workout_days; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workout_days (
    workout_day_id bigint NOT NULL,
    plan_id bigint NOT NULL,
    name character varying(150) NOT NULL,
    day_order integer NOT NULL,
    is_rest_day boolean DEFAULT false NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT chk_workout_day_order CHECK ((day_order > 0))
);


--
-- Name: workout_days_workout_day_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.workout_days_workout_day_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: workout_days_workout_day_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.workout_days_workout_day_id_seq OWNED BY public.workout_days.workout_day_id;


--
-- Name: workout_exercise_sets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workout_exercise_sets (
    workout_exercise_set_id bigint NOT NULL,
    workout_exercise_id bigint NOT NULL,
    set_order integer NOT NULL,
    target_reps integer,
    target_weight_kg numeric(7,2),
    rest_seconds integer,
    notes text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    target_duration_seconds integer,
    target_distance_m numeric(8,2),
    CONSTRAINT chk_rest_seconds CHECK (((rest_seconds IS NULL) OR (rest_seconds >= 0))),
    CONSTRAINT chk_target_reps CHECK (((target_reps IS NULL) OR (target_reps > 0))),
    CONSTRAINT chk_target_weight CHECK (((target_weight_kg IS NULL) OR (target_weight_kg >= (0)::numeric))),
    CONSTRAINT chk_workout_set_order CHECK ((set_order > 0)),
    CONSTRAINT chk_workout_set_target_distance CHECK (((target_distance_m IS NULL) OR (target_distance_m > (0)::numeric))),
    CONSTRAINT chk_workout_set_target_duration CHECK (((target_duration_seconds IS NULL) OR (target_duration_seconds > 0)))
);


--
-- Name: workout_exercise_sets_workout_exercise_set_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.workout_exercise_sets_workout_exercise_set_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: workout_exercise_sets_workout_exercise_set_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.workout_exercise_sets_workout_exercise_set_id_seq OWNED BY public.workout_exercise_sets.workout_exercise_set_id;


--
-- Name: workout_exercises; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workout_exercises (
    workout_exercise_id bigint NOT NULL,
    workout_day_id bigint NOT NULL,
    exercise_id bigint NOT NULL,
    exercise_order integer NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT chk_workout_exercise_order CHECK ((exercise_order > 0))
);


--
-- Name: workout_exercises_workout_exercise_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.workout_exercises_workout_exercise_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: workout_exercises_workout_exercise_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.workout_exercises_workout_exercise_id_seq OWNED BY public.workout_exercises.workout_exercise_id;


--
-- Name: workout_plans; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workout_plans (
    plan_id bigint NOT NULL,
    user_id uuid NOT NULL,
    name character varying(150) NOT NULL,
    description text,
    goal character varying(50),
    duration_weeks integer,
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    is_active boolean DEFAULT false NOT NULL,
    start_date date,
    CONSTRAINT chk_workout_plan_duration CHECK (((duration_weeks IS NULL) OR (duration_weeks > 0))),
    CONSTRAINT chk_workout_plan_status CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'archived'::character varying])::text[])))
);


--
-- Name: COLUMN workout_plans.start_date; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.workout_plans.start_date IS 'First day the plan is followed. With duration_weeks it gives the window plan-completion is measured over. Nullable: an open-ended plan has no end.';


--
-- Name: workout_plans_plan_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.workout_plans_plan_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: workout_plans_plan_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.workout_plans_plan_id_seq OWNED BY public.workout_plans.plan_id;


--
-- Name: workout_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workout_sessions (
    session_id bigint NOT NULL,
    user_id uuid NOT NULL,
    workout_day_id bigint,
    plan_name character varying(150),
    day_name character varying(150),
    scheduled_date date,
    started_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    completed_at timestamp with time zone,
    status character varying(20) DEFAULT 'in_progress'::character varying NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    paused_at timestamp with time zone,
    paused_seconds integer DEFAULT 0 NOT NULL,
    CONSTRAINT chk_workout_session_completion CHECK ((((status)::text = 'completed'::text) = (completed_at IS NOT NULL))),
    CONSTRAINT chk_workout_session_pause_state CHECK (((paused_at IS NOT NULL) = ((status)::text = 'paused'::text))),
    CONSTRAINT chk_workout_session_paused CHECK ((paused_seconds >= 0)),
    CONSTRAINT chk_workout_session_status CHECK (((status)::text = ANY ((ARRAY['in_progress'::character varying, 'paused'::character varying, 'completed'::character varying, 'abandoned'::character varying])::text[]))),
    CONSTRAINT chk_workout_session_times CHECK (((completed_at IS NULL) OR (completed_at >= started_at)))
);


--
-- Name: workout_sessions_session_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.workout_sessions_session_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: workout_sessions_session_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.workout_sessions_session_id_seq OWNED BY public.workout_sessions.session_id;


--
-- Name: achievements achievement_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.achievements ALTER COLUMN achievement_id SET DEFAULT nextval('public.achievements_achievement_id_seq'::regclass);


--
-- Name: ai_feedback feedback_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_feedback ALTER COLUMN feedback_id SET DEFAULT nextval('public.ai_feedback_feedback_id_seq'::regclass);


--
-- Name: ai_recommendations recommendation_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_recommendations ALTER COLUMN recommendation_id SET DEFAULT nextval('public.ai_recommendations_recommendation_id_seq'::regclass);


--
-- Name: body_metrics metric_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.body_metrics ALTER COLUMN metric_id SET DEFAULT nextval('public.body_metrics_metric_id_seq'::regclass);


--
-- Name: equipment equipment_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equipment ALTER COLUMN equipment_id SET DEFAULT nextval('public.equipment_equipment_id_seq'::regclass);


--
-- Name: exercise_equipment exercise_equipment_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_equipment ALTER COLUMN exercise_equipment_id SET DEFAULT nextval('public.exercise_equipment_exercise_equipment_id_seq'::regclass);


--
-- Name: exercise_media media_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_media ALTER COLUMN media_id SET DEFAULT nextval('public.exercise_media_media_id_seq'::regclass);


--
-- Name: exercise_muscles exercise_muscle_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_muscles ALTER COLUMN exercise_muscle_id SET DEFAULT nextval('public.exercise_muscles_exercise_muscle_id_seq'::regclass);


--
-- Name: exercise_translations translation_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_translations ALTER COLUMN translation_id SET DEFAULT nextval('public.exercise_translations_translation_id_seq'::regclass);


--
-- Name: exercises exercise_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercises ALTER COLUMN exercise_id SET DEFAULT nextval('public.exercises_exercise_id_seq'::regclass);


--
-- Name: fitness_goals goal_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fitness_goals ALTER COLUMN goal_id SET DEFAULT nextval('public.fitness_goals_goal_id_seq'::regclass);


--
-- Name: muscle_groups muscle_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.muscle_groups ALTER COLUMN muscle_id SET DEFAULT nextval('public.muscle_groups_muscle_id_seq'::regclass);


--
-- Name: personal_bests personal_best_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_bests ALTER COLUMN personal_best_id SET DEFAULT nextval('public.personal_bests_personal_best_id_seq'::regclass);


--
-- Name: personal_records pr_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_records ALTER COLUMN pr_id SET DEFAULT nextval('public.personal_records_pr_id_seq'::regclass);


--
-- Name: schedules schedule_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedules ALTER COLUMN schedule_id SET DEFAULT nextval('public.schedules_schedule_id_seq'::regclass);


--
-- Name: session_exercises session_exercise_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_exercises ALTER COLUMN session_exercise_id SET DEFAULT nextval('public.session_exercises_session_exercise_id_seq'::regclass);


--
-- Name: session_sets session_set_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_sets ALTER COLUMN session_set_id SET DEFAULT nextval('public.session_sets_session_set_id_seq'::regclass);


--
-- Name: term_translations translation_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_translations ALTER COLUMN translation_id SET DEFAULT nextval('public.term_translations_translation_id_seq'::regclass);


--
-- Name: user_achievements user_achievement_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_achievements ALTER COLUMN user_achievement_id SET DEFAULT nextval('public.user_achievements_user_achievement_id_seq'::regclass);


--
-- Name: user_goals user_goal_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_goals ALTER COLUMN user_goal_id SET DEFAULT nextval('public.user_goals_user_goal_id_seq'::regclass);


--
-- Name: user_preferences preference_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_preferences ALTER COLUMN preference_id SET DEFAULT nextval('public.user_preferences_preference_id_seq'::regclass);


--
-- Name: user_profiles profile_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_profiles ALTER COLUMN profile_id SET DEFAULT nextval('public.user_profiles_profile_id_seq'::regclass);


--
-- Name: workout_days workout_day_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_days ALTER COLUMN workout_day_id SET DEFAULT nextval('public.workout_days_workout_day_id_seq'::regclass);


--
-- Name: workout_exercise_sets workout_exercise_set_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_exercise_sets ALTER COLUMN workout_exercise_set_id SET DEFAULT nextval('public.workout_exercise_sets_workout_exercise_set_id_seq'::regclass);


--
-- Name: workout_exercises workout_exercise_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_exercises ALTER COLUMN workout_exercise_id SET DEFAULT nextval('public.workout_exercises_workout_exercise_id_seq'::regclass);


--
-- Name: workout_plans plan_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_plans ALTER COLUMN plan_id SET DEFAULT nextval('public.workout_plans_plan_id_seq'::regclass);


--
-- Name: workout_sessions session_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_sessions ALTER COLUMN session_id SET DEFAULT nextval('public.workout_sessions_session_id_seq'::regclass);


--
-- Name: achievements achievements_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.achievements
    ADD CONSTRAINT achievements_code_key UNIQUE (code);


--
-- Name: achievements achievements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.achievements
    ADD CONSTRAINT achievements_pkey PRIMARY KEY (achievement_id);


--
-- Name: ai_feedback ai_feedback_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_feedback
    ADD CONSTRAINT ai_feedback_pkey PRIMARY KEY (feedback_id);


--
-- Name: ai_recommendations ai_recommendations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_recommendations
    ADD CONSTRAINT ai_recommendations_pkey PRIMARY KEY (recommendation_id);


--
-- Name: body_metrics body_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.body_metrics
    ADD CONSTRAINT body_metrics_pkey PRIMARY KEY (metric_id);


--
-- Name: equipment equipment_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equipment
    ADD CONSTRAINT equipment_name_key UNIQUE (name);


--
-- Name: equipment equipment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.equipment
    ADD CONSTRAINT equipment_pkey PRIMARY KEY (equipment_id);


--
-- Name: exercise_equipment exercise_equipment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_equipment
    ADD CONSTRAINT exercise_equipment_pkey PRIMARY KEY (exercise_equipment_id);


--
-- Name: exercise_media exercise_media_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_media
    ADD CONSTRAINT exercise_media_pkey PRIMARY KEY (media_id);


--
-- Name: exercise_muscles exercise_muscles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_muscles
    ADD CONSTRAINT exercise_muscles_pkey PRIMARY KEY (exercise_muscle_id);


--
-- Name: exercise_translations exercise_translations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_translations
    ADD CONSTRAINT exercise_translations_pkey PRIMARY KEY (translation_id);


--
-- Name: exercises exercises_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercises
    ADD CONSTRAINT exercises_pkey PRIMARY KEY (exercise_id);


--
-- Name: fitness_goals fitness_goals_goal_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fitness_goals
    ADD CONSTRAINT fitness_goals_goal_type_key UNIQUE (goal_type);


--
-- Name: fitness_goals fitness_goals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fitness_goals
    ADD CONSTRAINT fitness_goals_pkey PRIMARY KEY (goal_id);


--
-- Name: muscle_groups muscle_groups_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.muscle_groups
    ADD CONSTRAINT muscle_groups_name_key UNIQUE (name);


--
-- Name: muscle_groups muscle_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.muscle_groups
    ADD CONSTRAINT muscle_groups_pkey PRIMARY KEY (muscle_id);


--
-- Name: personal_bests personal_bests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_bests
    ADD CONSTRAINT personal_bests_pkey PRIMARY KEY (personal_best_id);


--
-- Name: personal_records personal_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_records
    ADD CONSTRAINT personal_records_pkey PRIMARY KEY (pr_id);


--
-- Name: schedules schedules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedules
    ADD CONSTRAINT schedules_pkey PRIMARY KEY (schedule_id);


--
-- Name: session_exercises session_exercises_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_exercises
    ADD CONSTRAINT session_exercises_pkey PRIMARY KEY (session_exercise_id);


--
-- Name: session_sets session_sets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_sets
    ADD CONSTRAINT session_sets_pkey PRIMARY KEY (session_set_id);


--
-- Name: term_translations term_translations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_translations
    ADD CONSTRAINT term_translations_pkey PRIMARY KEY (translation_id);


--
-- Name: exercise_equipment uq_exercise_equipment; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_equipment
    ADD CONSTRAINT uq_exercise_equipment UNIQUE (exercise_id, equipment_id);


--
-- Name: exercise_muscles uq_exercise_muscle; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_muscles
    ADD CONSTRAINT uq_exercise_muscle UNIQUE (exercise_id, muscle_id);


--
-- Name: exercise_translations uq_exercise_translation_language; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_translations
    ADD CONSTRAINT uq_exercise_translation_language UNIQUE (exercise_id, language);


--
-- Name: personal_bests uq_personal_bests_user_exercise_type; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_bests
    ADD CONSTRAINT uq_personal_bests_user_exercise_type UNIQUE (user_id, exercise_id, record_type);


--
-- Name: session_exercises uq_session_exercise_order; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_exercises
    ADD CONSTRAINT uq_session_exercise_order UNIQUE (session_id, exercise_order) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: session_sets uq_session_set_order; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_sets
    ADD CONSTRAINT uq_session_set_order UNIQUE (session_exercise_id, set_order) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: term_translations uq_term_translations_equipment; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_translations
    ADD CONSTRAINT uq_term_translations_equipment UNIQUE (equipment_id, language);


--
-- Name: term_translations uq_term_translations_muscle; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_translations
    ADD CONSTRAINT uq_term_translations_muscle UNIQUE (muscle_id, language);


--
-- Name: user_achievements uq_user_achievements; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_achievements
    ADD CONSTRAINT uq_user_achievements UNIQUE (user_id, achievement_id);


--
-- Name: workout_days uq_workout_day_order; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_days
    ADD CONSTRAINT uq_workout_day_order UNIQUE (plan_id, day_order) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: workout_exercises uq_workout_exercise_order; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_exercises
    ADD CONSTRAINT uq_workout_exercise_order UNIQUE (workout_day_id, exercise_order) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: workout_exercise_sets uq_workout_set_order; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_exercise_sets
    ADD CONSTRAINT uq_workout_set_order UNIQUE (workout_exercise_id, set_order) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: user_achievements user_achievements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_achievements
    ADD CONSTRAINT user_achievements_pkey PRIMARY KEY (user_achievement_id);


--
-- Name: user_goals user_goals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_goals
    ADD CONSTRAINT user_goals_pkey PRIMARY KEY (user_goal_id);


--
-- Name: user_preferences user_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_preferences
    ADD CONSTRAINT user_preferences_pkey PRIMARY KEY (preference_id);


--
-- Name: user_preferences user_preferences_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_preferences
    ADD CONSTRAINT user_preferences_user_id_key UNIQUE (user_id);


--
-- Name: user_profiles user_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_pkey PRIMARY KEY (profile_id);


--
-- Name: user_profiles user_profiles_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT user_profiles_user_id_key UNIQUE (user_id);


--
-- Name: workout_days workout_days_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_days
    ADD CONSTRAINT workout_days_pkey PRIMARY KEY (workout_day_id);


--
-- Name: workout_exercise_sets workout_exercise_sets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_exercise_sets
    ADD CONSTRAINT workout_exercise_sets_pkey PRIMARY KEY (workout_exercise_set_id);


--
-- Name: workout_exercises workout_exercises_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_exercises
    ADD CONSTRAINT workout_exercises_pkey PRIMARY KEY (workout_exercise_id);


--
-- Name: workout_plans workout_plans_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_plans
    ADD CONSTRAINT workout_plans_pkey PRIMARY KEY (plan_id);


--
-- Name: workout_sessions workout_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_sessions
    ADD CONSTRAINT workout_sessions_pkey PRIMARY KEY (session_id);


--
-- Name: idx_achievements_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_achievements_active ON public.achievements USING btree (achievement_type) WHERE is_active;


--
-- Name: idx_ai_feedback_recommendation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ai_feedback_recommendation ON public.ai_feedback USING btree (recommendation_id);


--
-- Name: idx_ai_recommendations_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ai_recommendations_user_status ON public.ai_recommendations USING btree (user_id, status);


--
-- Name: idx_body_metrics_user_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_body_metrics_user_date ON public.body_metrics USING btree (user_id, recorded_at DESC);


--
-- Name: idx_exercise_equipment_equipment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exercise_equipment_equipment ON public.exercise_equipment USING btree (equipment_id);


--
-- Name: idx_exercise_media_exercise; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exercise_media_exercise ON public.exercise_media USING btree (exercise_id);


--
-- Name: idx_exercise_muscles_muscle; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_exercise_muscles_muscle ON public.exercise_muscles USING btree (muscle_id);


--
-- Name: idx_muscle_groups_parent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_muscle_groups_parent ON public.muscle_groups USING btree (parent_id);


--
-- Name: idx_personal_bests_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_personal_bests_user ON public.personal_bests USING btree (user_id, exercise_id);


--
-- Name: idx_personal_records_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_personal_records_type ON public.personal_records USING btree (user_id, exercise_id, record_type, achieved_at DESC);


--
-- Name: idx_personal_records_user_exercise; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_personal_records_user_exercise ON public.personal_records USING btree (user_id, exercise_id, achieved_at DESC);


--
-- Name: idx_schedules_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_schedules_date ON public.schedules USING btree (scheduled_date);


--
-- Name: idx_schedules_day_of_week; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_schedules_day_of_week ON public.schedules USING btree (day_of_week);


--
-- Name: idx_schedules_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_schedules_user ON public.schedules USING btree (user_id);


--
-- Name: idx_schedules_workout_day; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_schedules_workout_day ON public.schedules USING btree (workout_day_id);


--
-- Name: idx_session_exercises_exercise; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_session_exercises_exercise ON public.session_exercises USING btree (exercise_id);


--
-- Name: idx_session_exercises_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_session_exercises_session ON public.session_exercises USING btree (session_id);


--
-- Name: idx_session_sets_exercise; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_session_sets_exercise ON public.session_sets USING btree (session_exercise_id);


--
-- Name: idx_term_translations_language; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_term_translations_language ON public.term_translations USING btree (language);


--
-- Name: idx_user_achievements_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_achievements_user ON public.user_achievements USING btree (user_id, achieved_at DESC);


--
-- Name: idx_user_goals_user_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_goals_user_status ON public.user_goals USING btree (user_id, status);


--
-- Name: idx_workout_days_plan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workout_days_plan ON public.workout_days USING btree (plan_id);


--
-- Name: idx_workout_exercises_day; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workout_exercises_day ON public.workout_exercises USING btree (workout_day_id);


--
-- Name: idx_workout_exercises_exercise; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workout_exercises_exercise ON public.workout_exercises USING btree (exercise_id);


--
-- Name: idx_workout_plans_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workout_plans_user ON public.workout_plans USING btree (user_id);


--
-- Name: idx_workout_sessions_day; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workout_sessions_day ON public.workout_sessions USING btree (workout_day_id);


--
-- Name: idx_workout_sessions_user_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workout_sessions_user_date ON public.workout_sessions USING btree (user_id, scheduled_date DESC, started_at DESC);


--
-- Name: idx_workout_sets_exercise; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_workout_sets_exercise ON public.workout_exercise_sets USING btree (workout_exercise_id);


--
-- Name: uq_exercises_source_url; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_exercises_source_url ON public.exercises USING btree (source_url) WHERE (source_url IS NOT NULL);


--
-- Name: uq_schedules_date; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_schedules_date ON public.schedules USING btree (user_id, scheduled_date, workout_day_id) WHERE (scheduled_date IS NOT NULL);


--
-- Name: uq_schedules_day_of_week; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_schedules_day_of_week ON public.schedules USING btree (user_id, day_of_week, workout_day_id) WHERE (day_of_week IS NOT NULL);


--
-- Name: uq_workout_plans_one_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_workout_plans_one_active ON public.workout_plans USING btree (user_id) WHERE is_active;


--
-- Name: uq_workout_sessions_one_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_workout_sessions_one_active ON public.workout_sessions USING btree (user_id) WHERE ((status)::text = ANY ((ARRAY['in_progress'::character varying, 'paused'::character varying])::text[]));


--
-- Name: exercise_translations trg_exercise_translations_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_exercise_translations_updated_at BEFORE UPDATE ON public.exercise_translations FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: exercises trg_exercises_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_exercises_updated_at BEFORE UPDATE ON public.exercises FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: schedules trg_schedules_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_schedules_updated_at BEFORE UPDATE ON public.schedules FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: session_exercises trg_session_exercises_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_session_exercises_updated_at BEFORE UPDATE ON public.session_exercises FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: session_sets trg_session_sets_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_session_sets_updated_at BEFORE UPDATE ON public.session_sets FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: term_translations trg_term_translations_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_term_translations_updated_at BEFORE UPDATE ON public.term_translations FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: user_goals trg_user_goals_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_user_goals_updated_at BEFORE UPDATE ON public.user_goals FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: user_preferences trg_user_preferences_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_user_preferences_updated_at BEFORE UPDATE ON public.user_preferences FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: user_profiles trg_user_profiles_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_user_profiles_updated_at BEFORE UPDATE ON public.user_profiles FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: workout_days trg_workout_days_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_workout_days_updated_at BEFORE UPDATE ON public.workout_days FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: workout_exercise_sets trg_workout_exercise_sets_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_workout_exercise_sets_updated_at BEFORE UPDATE ON public.workout_exercise_sets FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: workout_exercises trg_workout_exercises_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_workout_exercises_updated_at BEFORE UPDATE ON public.workout_exercises FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: workout_plans trg_workout_plans_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_workout_plans_updated_at BEFORE UPDATE ON public.workout_plans FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: workout_sessions trg_workout_sessions_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_workout_sessions_updated_at BEFORE UPDATE ON public.workout_sessions FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: achievements achievements_exercise_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.achievements
    ADD CONSTRAINT achievements_exercise_id_fkey FOREIGN KEY (exercise_id) REFERENCES public.exercises(exercise_id) ON DELETE CASCADE;


--
-- Name: ai_feedback fk_ai_feedback_recommendation; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_feedback
    ADD CONSTRAINT fk_ai_feedback_recommendation FOREIGN KEY (recommendation_id) REFERENCES public.ai_recommendations(recommendation_id) ON DELETE CASCADE;


--
-- Name: ai_feedback fk_ai_feedback_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_feedback
    ADD CONSTRAINT fk_ai_feedback_user FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: ai_recommendations fk_ai_recommendations_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_recommendations
    ADD CONSTRAINT fk_ai_recommendations_user FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: body_metrics fk_body_metrics_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.body_metrics
    ADD CONSTRAINT fk_body_metrics_user FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: exercise_equipment fk_exercise_equipment_equipment; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_equipment
    ADD CONSTRAINT fk_exercise_equipment_equipment FOREIGN KEY (equipment_id) REFERENCES public.equipment(equipment_id) ON DELETE CASCADE;


--
-- Name: exercise_equipment fk_exercise_equipment_exercise; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_equipment
    ADD CONSTRAINT fk_exercise_equipment_exercise FOREIGN KEY (exercise_id) REFERENCES public.exercises(exercise_id) ON DELETE CASCADE;


--
-- Name: exercise_media fk_exercise_media_exercise; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_media
    ADD CONSTRAINT fk_exercise_media_exercise FOREIGN KEY (exercise_id) REFERENCES public.exercises(exercise_id) ON DELETE CASCADE;


--
-- Name: exercise_muscles fk_exercise_muscles_exercise; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_muscles
    ADD CONSTRAINT fk_exercise_muscles_exercise FOREIGN KEY (exercise_id) REFERENCES public.exercises(exercise_id) ON DELETE CASCADE;


--
-- Name: exercise_muscles fk_exercise_muscles_muscle; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_muscles
    ADD CONSTRAINT fk_exercise_muscles_muscle FOREIGN KEY (muscle_id) REFERENCES public.muscle_groups(muscle_id) ON DELETE CASCADE;


--
-- Name: exercise_translations fk_exercise_translations_exercise; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exercise_translations
    ADD CONSTRAINT fk_exercise_translations_exercise FOREIGN KEY (exercise_id) REFERENCES public.exercises(exercise_id) ON DELETE CASCADE;


--
-- Name: muscle_groups fk_muscle_groups_parent; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.muscle_groups
    ADD CONSTRAINT fk_muscle_groups_parent FOREIGN KEY (parent_id) REFERENCES public.muscle_groups(muscle_id) ON DELETE SET NULL;


--
-- Name: personal_records fk_personal_records_exercise; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_records
    ADD CONSTRAINT fk_personal_records_exercise FOREIGN KEY (exercise_id) REFERENCES public.exercises(exercise_id) ON DELETE CASCADE;


--
-- Name: personal_records fk_personal_records_set; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_records
    ADD CONSTRAINT fk_personal_records_set FOREIGN KEY (set_id) REFERENCES public.session_sets(session_set_id) ON DELETE SET NULL;


--
-- Name: personal_records fk_personal_records_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_records
    ADD CONSTRAINT fk_personal_records_user FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: schedules fk_schedule_workout_day; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedules
    ADD CONSTRAINT fk_schedule_workout_day FOREIGN KEY (workout_day_id) REFERENCES public.workout_days(workout_day_id) ON DELETE CASCADE;


--
-- Name: session_exercises fk_session_exercise_exercise; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_exercises
    ADD CONSTRAINT fk_session_exercise_exercise FOREIGN KEY (exercise_id) REFERENCES public.exercises(exercise_id) ON DELETE RESTRICT;


--
-- Name: session_exercises fk_session_exercise_session; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_exercises
    ADD CONSTRAINT fk_session_exercise_session FOREIGN KEY (session_id) REFERENCES public.workout_sessions(session_id) ON DELETE CASCADE;


--
-- Name: session_sets fk_session_set_exercise; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_sets
    ADD CONSTRAINT fk_session_set_exercise FOREIGN KEY (session_exercise_id) REFERENCES public.session_exercises(session_exercise_id) ON DELETE CASCADE;


--
-- Name: session_sets fk_session_set_planned; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_sets
    ADD CONSTRAINT fk_session_set_planned FOREIGN KEY (workout_exercise_set_id) REFERENCES public.workout_exercise_sets(workout_exercise_set_id) ON DELETE SET NULL;


--
-- Name: term_translations fk_term_translations_equipment; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_translations
    ADD CONSTRAINT fk_term_translations_equipment FOREIGN KEY (equipment_id) REFERENCES public.equipment(equipment_id) ON DELETE CASCADE;


--
-- Name: term_translations fk_term_translations_muscle; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.term_translations
    ADD CONSTRAINT fk_term_translations_muscle FOREIGN KEY (muscle_id) REFERENCES public.muscle_groups(muscle_id) ON DELETE CASCADE;


--
-- Name: user_goals fk_user_goals_goal; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_goals
    ADD CONSTRAINT fk_user_goals_goal FOREIGN KEY (goal_id) REFERENCES public.fitness_goals(goal_id) ON DELETE RESTRICT;


--
-- Name: user_goals fk_user_goals_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_goals
    ADD CONSTRAINT fk_user_goals_user FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: user_preferences fk_user_preferences_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_preferences
    ADD CONSTRAINT fk_user_preferences_user FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: user_profiles fk_user_profiles_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_profiles
    ADD CONSTRAINT fk_user_profiles_user FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: workout_days fk_workout_day_plan; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_days
    ADD CONSTRAINT fk_workout_day_plan FOREIGN KEY (plan_id) REFERENCES public.workout_plans(plan_id) ON DELETE CASCADE;


--
-- Name: workout_exercises fk_workout_exercise_day; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_exercises
    ADD CONSTRAINT fk_workout_exercise_day FOREIGN KEY (workout_day_id) REFERENCES public.workout_days(workout_day_id) ON DELETE CASCADE;


--
-- Name: workout_exercises fk_workout_exercise_exercise; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_exercises
    ADD CONSTRAINT fk_workout_exercise_exercise FOREIGN KEY (exercise_id) REFERENCES public.exercises(exercise_id) ON DELETE RESTRICT;


--
-- Name: workout_sessions fk_workout_session_day; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_sessions
    ADD CONSTRAINT fk_workout_session_day FOREIGN KEY (workout_day_id) REFERENCES public.workout_days(workout_day_id) ON DELETE SET NULL;


--
-- Name: workout_exercise_sets fk_workout_set_exercise; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workout_exercise_sets
    ADD CONSTRAINT fk_workout_set_exercise FOREIGN KEY (workout_exercise_id) REFERENCES public.workout_exercises(workout_exercise_id) ON DELETE CASCADE;


--
-- Name: personal_bests personal_bests_exercise_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_bests
    ADD CONSTRAINT personal_bests_exercise_id_fkey FOREIGN KEY (exercise_id) REFERENCES public.exercises(exercise_id) ON DELETE CASCADE;


--
-- Name: personal_bests personal_bests_session_set_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_bests
    ADD CONSTRAINT personal_bests_session_set_id_fkey FOREIGN KEY (session_set_id) REFERENCES public.session_sets(session_set_id) ON DELETE SET NULL;


--
-- Name: personal_bests personal_bests_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_bests
    ADD CONSTRAINT personal_bests_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- Name: user_achievements user_achievements_achievement_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_achievements
    ADD CONSTRAINT user_achievements_achievement_id_fkey FOREIGN KEY (achievement_id) REFERENCES public.achievements(achievement_id) ON DELETE CASCADE;


--
-- Name: user_achievements user_achievements_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_achievements
    ADD CONSTRAINT user_achievements_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--


