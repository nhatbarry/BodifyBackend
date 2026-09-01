-- ============================================================
-- ALTER: add role column to user_profiles (admin dashboard support)
-- ============================================================
-- Role lives on user_profiles, not a local "users" table -
-- auth is handled by Supabase Auth (auth.users, UUID), which this
-- backend does not own and never writes to.

BEGIN;

ALTER TABLE user_profiles
    ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'user';

ALTER TABLE user_profiles
    DROP CONSTRAINT IF EXISTS chk_user_profiles_role;

ALTER TABLE user_profiles
    ADD CONSTRAINT chk_user_profiles_role
        CHECK (role IN ('user', 'admin'));

COMMIT;
