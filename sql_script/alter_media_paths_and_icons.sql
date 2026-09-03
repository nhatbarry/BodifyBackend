-- ============================================================
-- BODIFY - drop the "media/" prefix from storage keys, register icons
--
-- The bucket was reorganised: objects that lived at
--   media/abductors/cable-hip-abduction/cable-hip-abduction.jpg
-- now live at
--   abductors/cable-hip-abduction/cable-hip-abduction.jpg
-- and each thumbnail has gained a 256 px sibling
--   abductors/cable-hip-abduction/cable-hip-abduction_icon.webp
--
-- Run this only once the bucket actually looks like that - a storage_key the
-- bucket does not have becomes a signed URL that 404s, and nothing in the app
-- would report why.
--
-- Idempotent: re-running strips nothing further and inserts no duplicates.
-- ============================================================

BEGIN;

-- 1. 'icon' is a new media_type; the original constraint allowed only
--    thumbnail/video/image.
ALTER TABLE exercise_media
    DROP CONSTRAINT IF EXISTS chk_exercise_media_type;

ALTER TABLE exercise_media
    ADD CONSTRAINT chk_exercise_media_type
    CHECK (media_type IN ('thumbnail', 'video', 'image', 'icon'));


-- 2. Strip the prefix. Anchored to the start so a path that legitimately
--    contains "media/" deeper down is left alone.
UPDATE exercise_media
   SET storage_key = regexp_replace(storage_key, '^media/', '')
 WHERE storage_key LIKE 'media/%';


-- 3. Register one icon per thumbnail, derived from the thumbnail's own key.
--    Deriving it rather than listing 688 literals means an icon can never be
--    attached to the wrong exercise, and it stays correct if a thumbnail key
--    is ever corrected.
INSERT INTO exercise_media (exercise_id, media_type, storage_key, sort_order)
SELECT t.exercise_id,
       'icon',
       regexp_replace(t.storage_key, '\.jpg$', '_icon.webp'),
       0
  FROM exercise_media t
 WHERE t.media_type = 'thumbnail'
   AND t.storage_key LIKE '%.jpg'
   AND NOT EXISTS (
         SELECT 1
           FROM exercise_media e
          WHERE e.exercise_id = t.exercise_id
            AND e.media_type = 'icon'
       );

COMMIT;
