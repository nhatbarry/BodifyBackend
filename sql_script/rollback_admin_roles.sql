-- ============================================================
-- BODIFY - undo ddl_admin_roles.sql
--
-- Refuses while any superadmin exists: the role CHECK would no longer
-- admit them. Demote them first (to 'admin' if they should keep the
-- console under the previous backend).
-- Under the previous backend 'admin' means every admin route, so only
-- people holding "Toàn quyền" stay admin; anyone given a narrower role is
-- set back to 'user' rather than silently given everything.
-- ============================================================

BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM user_profiles WHERE role = 'superadmin') THEN
        RAISE EXCEPTION 'superadmins exist; demote them before rolling back';
    END IF;
END $$;

UPDATE user_profiles up SET role = 'user', updated_at = now()
WHERE up.role = 'admin'
  AND NOT EXISTS (
      SELECT 1 FROM user_admin_roles u
      JOIN admin_roles r ON r.admin_role_id = u.admin_role_id
      WHERE u.user_id = up.user_id AND lower(btrim(r.name)) = lower('Toàn quyền')
  );

DROP TABLE IF EXISTS user_admin_roles;
DROP TABLE IF EXISTS admin_role_permissions;
DROP TABLE IF EXISTS admin_roles;
DROP TABLE IF EXISTS permissions;

ALTER TABLE user_profiles DROP CONSTRAINT IF EXISTS chk_user_profiles_role;
ALTER TABLE user_profiles
    ADD CONSTRAINT chk_user_profiles_role CHECK (role IN ('user', 'admin'));

COMMIT;
