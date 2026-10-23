-- ============================================================
-- BODIFY - ASSIGN THE squat_v1 CAMERA ALGORITHM
--
-- squat_v1 counts reps from the knee angle, filmed side-on
-- (lib/posecoach/squat_processor.dart in the app). It fits squats where both
-- legs bend together and nothing hides the hip, knee and ankle from a phone
-- beside the user.
--
-- In: the eight free-standing two-legged squats.
-- Out, deliberately:
--   * jump squats (523, 680, 682, 626) - the flight phase pulls the ankle off
--     the floor and the knee straightens mid-air, which reads as a rep top;
--   * split / single-leg squats (517, 522, 534, 539, 685, 431) - two knees
--     at different angles, and which one the camera sees is luck;
--   * machine, Smith and hack squats (287, 342, 370, 520, 531, 537, 542,
--     686) - the machine stands between the phone and the legs;
--   * plié and frog squats (521, 518) - a wide stance bends the knee across
--     the camera's line of sight, the same reason a front view counts nothing;
--   * Deep Squat Prying (48) - a mobility hold, not repetitions.
--
-- Matched on id AND English name, so a catalog whose ids drifted updates
-- nothing rather than the wrong exercise. Only fills an empty column, so it
-- never overwrites another algorithm and a second run is UPDATE 0.
--
-- Undo: UPDATE exercises SET ai_algorithm = NULL WHERE ai_algorithm = 'squat_v1';
-- ============================================================

UPDATE exercises e
   SET ai_algorithm = 'squat_v1',
       updated_at   = now()
  FROM exercise_translations t
 WHERE t.exercise_id = e.exercise_id
   AND t.language = 'en'
   AND (e.exercise_id, t.name) IN (
        (526, 'Prisoner Squat'),
        (506, 'Dumbbell Squat'),
        (510, 'Dumbbell Goblet Squat'),
        (511, 'Barbell Back Squat'),
        (519, 'Front Squat'),
        (675, 'Kettlebell Goblet Squat'),
        (528, 'Landmine Goblet Squat'),
        (643, 'Box Squat')
       )
   AND e.ai_algorithm IS NULL;


-- ============================================================
-- VERIFY
-- ============================================================
DO $$
DECLARE
    n BIGINT;
BEGIN
    SELECT count(*) INTO n FROM exercises WHERE ai_algorithm = 'squat_v1';
    RAISE NOTICE 'exercises counted by squat_v1: % (expected 8)', n;
    IF n <> 8 THEN
        RAISE WARNING 'Not 8 - check the ids above against this catalog.';
    END IF;
END $$;
