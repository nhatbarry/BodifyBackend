-- ============================================================
-- BODIFY - user_profiles.avatar_url now holds a storage key
--
-- The column used to hold whatever URL the user typed into a text field. It
-- now holds an R2 object key (`avatar/<user_id>/<uuid>.webp`), signed into a
-- URL on the way out.
--
-- Two reasons the old values cannot stay:
--
--   * A signed URL written into a database sits there expiring. Storing the
--     key and signing on read is the only version that keeps working.
--   * A free-text URL let anyone point their avatar at any address, and every
--     viewer of that profile would then fetch it - handing their IP to a
--     server the profile owner chose.
--
-- Idempotent.
-- ============================================================

BEGIN;

UPDATE user_profiles
   SET avatar_url = NULL
 WHERE avatar_url IS NOT NULL
   AND avatar_url NOT LIKE 'avatar/%';

COMMIT;
