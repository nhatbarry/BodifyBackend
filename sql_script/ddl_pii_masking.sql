-- ============================================================
-- BODIFY - personal data masked inside the database
--
-- Who reads what:
--   superadmin   the real values (the API keeps connecting as `postgres`).
--   every other  admin request runs under the role `bodify_pii_masked`
--                (`SET LOCAL ROLE`, app/db/pii.py), which can read
--                neither auth.* nor user_profiles nor free text people
--                typed. It reads accounts through `masked.accounts`, which
--                masks email and name, keeps only the year of birth and
--                drops the avatar.
--
-- The rule lives here, not in the API: a query a developer writes later
-- against auth.users or user_profiles, under the masked role, is refused by
-- Postgres ("permission denied") instead of leaking. Training, diary, body
-- and health figures stay readable - the reports need them.
--
-- `masked.accounts` decides per caller: it shows the real values only to a
-- role that could read the raw tables anyway (`masked.can_read_raw()`), so
-- one view serves both and no role gains anything by using it.
--
-- What this does not do: it protects against code that forgets to mask,
-- not against SQL injection - a statement injected into a masked request
-- could `RESET ROLE`, since the connection itself is `postgres`. And
-- GoTrue, the Supabase dashboard and the SQL Editor still see everything.
--
-- Free text people typed is hidden column by column (`hidden` below): the
-- coach conversation, meal notes and photos, workout notes, plan
-- descriptions. A new table is not readable by the masked role until this
-- script runs again - fail closed; tests/test_pii_masking.py calls every
-- admin read route under that role, so a missing grant shows up there.
--
-- Idempotent. Re-run after adding a table the admin console reads.
-- Deploy order: this script, then push the backend (the old backend never
-- switches role, so it keeps working against this).
-- Undo: rollback_pii_masking.sql
-- ============================================================

BEGIN;

-- ---------- 1. the role ----------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bodify_pii_masked') THEN
        CREATE ROLE bodify_pii_masked NOLOGIN NOINHERIT;
    END IF;
END $$;

-- Postgres 16+ does not let a role's creator switch to it by default.
GRANT bodify_pii_masked TO postgres WITH INHERIT FALSE, SET TRUE;

-- ---------- 2. masking functions ----------
CREATE SCHEMA IF NOT EXISTS masked;
REVOKE ALL ON SCHEMA masked FROM PUBLIC;

-- True for whoever could read the raw tables anyway (`postgres`), false for
-- the masked role. SECURITY INVOKER on purpose: it must see the caller, not
-- the view's owner.
CREATE OR REPLACE FUNCTION masked.can_read_raw() RETURNS boolean
LANGUAGE sql STABLE AS $$
    -- Naming auth.users at all needs USAGE on the schema, which the masked
    -- role lacks: CASE keeps the lookup from raising for it.
    SELECT CASE WHEN has_schema_privilege('auth', 'USAGE')
                THEN has_column_privilege('auth.users', 'email', 'SELECT')
                     AND has_table_privilege('public.user_profiles', 'SELECT')
                ELSE false END
$$;

-- nguyenvan@gmail.com -> ng***@gmail.com; a local part of 3 or fewer
-- characters keeps only its first.
CREATE OR REPLACE FUNCTION masked.email(e text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN e IS NULL THEN NULL
        WHEN strpos(e, '@') = 0 THEN left(e, 1) || '***'
        ELSE left(split_part(e, '@', 1),
                  CASE WHEN length(split_part(e, '@', 1)) <= 3 THEN 1 ELSE 2 END)
             || '***@' || substr(e, strpos(e, '@') + 1)
    END
$$;

-- "Nguyễn Văn An" -> "Nguyễn V***"; a single word -> its first letter.
CREATE OR REPLACE FUNCTION masked.name(n text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN n IS NULL OR btrim(n) = '' THEN n
        WHEN btrim(n) !~ '\s' THEN left(btrim(n), 1) || '***'
        ELSE (regexp_split_to_array(btrim(n), '\s+'))[1] || ' '
             || left((regexp_split_to_array(btrim(n), '\s+'))[2], 1) || '***'
    END
$$;

-- ---------- 3. the view ----------
-- Owned by postgres, so it reads auth.* for the masked role, which cannot.
DROP VIEW IF EXISTS masked.accounts;
CREATE VIEW masked.accounts AS
SELECT
    u.id                                                        AS user_id,
    CASE WHEN masked.can_read_raw() THEN u.email
         ELSE masked.email(u.email::text) END                   AS email,
    CASE WHEN masked.can_read_raw() THEN p.name
         ELSE masked.name(p.name) END                           AS name,
    coalesce(p.role, 'user')                                    AS role,
    u.created_at,
    u.deleted_at,
    u.last_sign_in_at,
    u.banned_until,
    coalesce(u.banned_until > now(), false)                     AS is_banned,
    coalesce(u.email, '') LIKE '%@seed.bodify.example'          AS is_seeded,
    EXISTS (SELECT 1 FROM auth.mfa_factors f
             WHERE f.user_id = u.id AND f.status = 'verified')  AS mfa_enabled,
    ARRAY(SELECT DISTINCT i.provider FROM auth.identities i
           WHERE i.user_id = u.id ORDER BY 1)                   AS providers,
    p.user_id IS NOT NULL                                       AS has_profile,
    CASE WHEN masked.can_read_raw() THEN p.avatar_url END       AS avatar_key,
    CASE WHEN masked.can_read_raw() THEN p.date_of_birth END    AS date_of_birth,
    extract(year FROM p.date_of_birth)::int                     AS birth_year,
    p.gender,
    NOT masked.can_read_raw()                                   AS pii_masked
FROM auth.users u
LEFT JOIN public.user_profiles p ON p.user_id = u.id;

-- Search by a whole address without reading any: ids only, exact match.
CREATE OR REPLACE FUNCTION masked.account_ids_by_email(term text) RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
    SELECT u.id FROM auth.users u
     WHERE lower(u.email) = lower(btrim(term)) AND btrim(term) <> ''
$$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA masked FROM PUBLIC;
REVOKE ALL ON masked.accounts FROM PUBLIC;
DO $$
DECLARE r text;
BEGIN
    -- Supabase's API roles get nothing in this schema.
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA masked FROM %I', r);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA masked FROM %I', r);
            EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA masked FROM %I', r);
        END IF;
    END LOOP;
END $$;

GRANT USAGE ON SCHEMA masked TO bodify_pii_masked;
GRANT SELECT ON masked.accounts TO bodify_pii_masked;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA masked TO bodify_pii_masked;

-- ---------- 4. what the masked role may read and write ----------
GRANT USAGE ON SCHEMA public, extensions TO bodify_pii_masked;

DO $$
DECLARE
    -- Free text and pictures people put in. Everything else in a table is
    -- a figure or a date the reports are made of.
    hidden constant text[] := ARRAY[
        'chat_messages.content', 'chat_messages.attachments',
        'chat_sessions.title',
        'ai_recommendations.title', 'ai_recommendations.content',
        'meal_photo_analyses.photo_key', 'meal_photo_analyses.result',
        'meal_photo_analyses.error',
        'meals.note', 'meals.photo_key',
        'workout_sessions.notes',
        'workout_plans.description'
    ];
    t record;
    cols text;
BEGIN
    -- Start from nothing, so a column moved onto the list loses its grant.
    EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM bodify_pii_masked';
    FOR t IN
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v', 'm', 'p')
           AND c.relname <> 'user_profiles'
    LOOP
        SELECT string_agg(quote_ident(a.attname), ', ' ORDER BY a.attnum) INTO cols
          FROM pg_attribute a
         WHERE a.attrelid = format('public.%I', t.relname)::regclass
           AND a.attnum > 0 AND NOT a.attisdropped
           AND NOT (t.relname || '.' || a.attname) = ANY (hidden);
        IF cols IS NULL THEN
            CONTINUE;
        ELSIF EXISTS (SELECT 1 FROM unnest(hidden) h WHERE h LIKE t.relname || '.%') THEN
            EXECUTE format('GRANT SELECT (%s) ON public.%I TO bodify_pii_masked', cols, t.relname);
        ELSE
            EXECUTE format('GRANT SELECT ON public.%I TO bodify_pii_masked', t.relname);
        END IF;
    END LOOP;
END $$;

-- The catalog is what the console edits.
GRANT INSERT, UPDATE, DELETE ON
    public.exercises, public.exercise_translations, public.exercise_media,
    public.exercise_muscles, public.exercise_equipment,
    public.muscle_groups, public.equipment, public.term_translations,
    public.foods, public.food_translations, public.food_components, public.food_categories
TO bodify_pii_masked;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO bodify_pii_masked;

-- The admin-role tables have row level security and no policies; the
-- masked role is not their owner, so it needs one to read them.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['permissions', 'admin_roles', 'admin_role_permissions', 'user_admin_roles'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_policies
                        WHERE schemaname = 'public' AND tablename = t
                          AND policyname = 'bodify_pii_masked_read') THEN
            EXECUTE format(
                'CREATE POLICY bodify_pii_masked_read ON public.%I FOR SELECT TO bodify_pii_masked USING (true)', t);
        END IF;
    END LOOP;
END $$;

COMMIT;

-- Check (expect: f, then a masked address):
--   BEGIN; SET LOCAL ROLE bodify_pii_masked;
--   SELECT masked.can_read_raw(); SELECT email FROM masked.accounts LIMIT 1;
--   ROLLBACK;
