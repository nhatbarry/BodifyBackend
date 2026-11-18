-- Undo ddl_pii_masking.sql. Push a backend that no longer switches role
-- FIRST: the current one runs every non-superadmin admin request under
-- bodify_pii_masked and reads masked.accounts, so both must outlive it.
BEGIN;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['permissions', 'admin_roles', 'admin_role_permissions', 'user_admin_roles'] LOOP
        IF to_regclass('public.' || t) IS NOT NULL THEN
            EXECUTE format('DROP POLICY IF EXISTS bodify_pii_masked_read ON public.%I', t);
        END IF;
    END LOOP;
END $$;

DROP SCHEMA IF EXISTS masked CASCADE;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bodify_pii_masked') THEN
        REVOKE ALL ON ALL TABLES IN SCHEMA public FROM bodify_pii_masked;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM bodify_pii_masked;
        REVOKE ALL ON SCHEMA public, extensions FROM bodify_pii_masked;
        DROP ROLE bodify_pii_masked;
    END IF;
END $$;

COMMIT;
