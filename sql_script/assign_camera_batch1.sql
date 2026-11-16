-- ============================================================
-- BODIFY - ASSIGN CAMERA ALGORITHMS, BATCH 1
--
-- Names the pose-detection algorithm that can count and score 37 more
-- exercises. Four of the algorithms are new in the app
-- (lib/posecoach/ in BodifyUI: lunge, curl, leg_raise and situp processors);
-- pushup_v1 already existed and is given to five more push-ups it fits.
--
-- RUN THIS AFTER THE APP BUILD THAT KNOWS THESE NAMES IS OUT, not before.
-- An older build treats an unknown name as "no camera support" and shows no
-- button, so nothing breaks - but the AI coach reads this column to decide
-- what is `camera_countable`, and would tell a user on an older build that
-- the camera can count an exercise their app cannot.
--
-- Every row was checked against the exercise's own instruction text, the
-- way the Phase 4 tracking_type sweep was. What decides whether a row fits
-- is in the app's processor files; what is left out, and why:
--
-- lunge_v1 (11) - front knee, filmed side-on. Stepping lunges, split
--   squats and the Bulgarian split squat.
--   Out: walking lunges (527, 641, 681, 396) - three steps and the user is
--   out of frame; lateral and curtsy lunges (536, 678, 47, 307) - the knee
--   bends across the camera's line of sight; jump (626), Smith (644),
--   landmine (642) and step-ups (679, 538, 515, 543) - not looked at yet.
--
-- curl_v1 (11) - elbow, filmed side-on. Dumbbell curls with the arm hanging
--   at the side, both arms together or one arm.
--   Out: alternating curls (62, 68, 64, 612) - a side-on camera sees one
--   arm, so the count would be per arm; barbell and EZ-bar curls (49, 57,
--   79, 84, 196, 368, 246, 248, 364, 198) - the plate sits between the phone
--   and the wrist, untested; cable curls (55, 60, 76, 245, 263, 272) -
--   untested; preacher, spider, incline, prone, concentration, lying, drag
--   and cross-body curls - the arm does not hang at the side, so the rest
--   angle differs or the arm is hidden.
--
-- leg_raise_v1 (5), knee_raise_v1 (3) - hip, filmed side-on, lying or
--   hanging.
--   Out: roman chair raises (25, 33, 606) - the frame and arm pads stand
--   between the phone and the hip; Seated Knee Tucks (37) - legs extended
--   reads about 140, under the angle that counts as "down"; alternating
--   raises (649, 652, 29) - one leg at a time; with a twist (30), on a
--   pull-up (424, 429), dragon flag (41), jackknife (516).
--
-- situp_v1 (2) - hip, filmed side-on.
--   Out: every crunch (11, 15, 14, 3, 360 and the cable ones) - the angle
--   moves about twenty degrees, too little to set thresholds for without
--   measuring a real set first.
--
-- pushup_v1 (+5) - the push-ups done with the elbows travelling back
--   rather than out, feet on the floor or raised.
--   Out: Knee Push-Up (224) and Wall Push Up (378) - the body line is
--   measured to the ankle and does not fit either; wide grip (660, 236) -
--   the elbows flare towards the camera; knee drive, mountain climber,
--   shoulder tap, T, jacks, plus, yoga and plyometric versions (622, 623,
--   209, 659, 376, 375, 242, 239, 655) - other limbs move and would be read
--   as faults; eccentric only (661); sphinx (599).
--
-- Matched on id AND English name, so a catalog whose ids drifted updates
-- nothing rather than the wrong exercise. Only fills an empty column, so it
-- never overwrites another algorithm and a second run is UPDATE 0.
--
-- Undo:
--   UPDATE exercises SET ai_algorithm = NULL
--    WHERE ai_algorithm IN ('lunge_v1', 'curl_v1', 'leg_raise_v1',
--                           'knee_raise_v1', 'situp_v1');
--   UPDATE exercises SET ai_algorithm = NULL
--    WHERE ai_algorithm = 'pushup_v1'
--      AND exercise_id IN (171, 614, 373, 219, 374);
-- ============================================================

UPDATE exercises e
   SET ai_algorithm = v.algorithm,
       updated_at   = now()
  FROM (VALUES
        -- lunge_v1
        (535, 'Bodyweight Lunge',                         'lunge_v1'),
        (530, 'Bodyweight Reverse Lunge',                 'lunge_v1'),
        (513, 'Dumbbell Lunge',                           'lunge_v1'),
        (514, 'Dumbbell Rear Lunge',                      'lunge_v1'),
        (529, 'Barbell Lunge',                            'lunge_v1'),
        (524, 'Barbell Reverse Lunge',                    'lunge_v1'),
        (532, 'Kettlebell Reverse Lunge',                 'lunge_v1'),
        (685, 'Split Squat',                              'lunge_v1'),
        (522, 'Dumbbell Split Squat',                     'lunge_v1'),
        (539, 'Front Foot Elevated Dumbbell Split Squat', 'lunge_v1'),
        (517, 'One Leg Dumbbell Squat (AKA Dumbbell Bulgarian Split Squat)',
                                                          'lunge_v1'),
        -- curl_v1
        (51,  'Standing Dumbbell Curl',                   'curl_v1'),
        (50,  'Standing Hammer Curl',                     'curl_v1'),
        (63,  'Seated Dumbbell Curl',                     'curl_v1'),
        (194, 'Seated Hammer Curl',                       'curl_v1'),
        (195, 'One-Arm Seated Dumbbell Curl',             'curl_v1'),
        (197, 'One-Arm Standing Hammer Curl',             'curl_v1'),
        (67,  'Standing Dumbbell Reverse Curl',           'curl_v1'),
        (613, 'Seated Dumbbell Reverse Curl',             'curl_v1'),
        (58,  'Zottman Curl',                             'curl_v1'),
        (615, 'Seated Zottman Curl',                      'curl_v1'),
        (382, 'One-Arm Dumbbell Reverse Grip Curl',       'curl_v1'),
        -- leg_raise_v1
        (4,   'Lying Floor Leg Raise',                    'leg_raise_v1'),
        (28,  'Lying Leg Raise (On Bench)',               'leg_raise_v1'),
        (42,  'Decline Bench Leg Raise',                  'leg_raise_v1'),
        (31,  'Lying Leg Raise With Hip Thrust',          'leg_raise_v1'),
        (7,   'Hanging Leg Raise',                        'leg_raise_v1'),
        -- knee_raise_v1
        (17,  'Hanging Knee Raise',                       'knee_raise_v1'),
        (605, 'Weighted Hanging Knee Raise',              'knee_raise_v1'),
        (20,  'Lying Floor Knee Tuck',                    'knee_raise_v1'),
        -- situp_v1
        (12,  'Sit Up',                                   'situp_v1'),
        (16,  'Decline Bench Sit Up',                     'situp_v1'),
        -- pushup_v1
        (171, 'Close Grip Push Up',                       'pushup_v1'),
        (614, 'Paused Push Up',                           'pushup_v1'),
        (373, 'Decline Push Up (Feet on Bench)',          'pushup_v1'),
        (219, 'Reverse Grip Incline Push Up',             'pushup_v1'),
        (374, 'Kettlebell Push Up',                       'pushup_v1')
       ) AS v(exercise_id, name, algorithm)
  JOIN exercise_translations t
    ON t.exercise_id = v.exercise_id
   AND t.language = 'en'
   AND t.name = v.name
 WHERE e.exercise_id = v.exercise_id
   AND e.ai_algorithm IS NULL;


-- ============================================================
-- VERIFY
-- ============================================================
DO $$
DECLARE
    expected CONSTANT JSONB := '{"lunge_v1": 11, "curl_v1": 11,
                                 "leg_raise_v1": 5, "knee_raise_v1": 3,
                                 "situp_v1": 2}';
    name     TEXT;
    wanted   BIGINT;
    found    BIGINT;
BEGIN
    FOR name, wanted IN SELECT key, value::BIGINT FROM jsonb_each_text(expected)
    LOOP
        SELECT count(*) INTO found FROM exercises WHERE ai_algorithm = name;
        RAISE NOTICE 'exercises counted by %: % (expected %)', name, found, wanted;
        IF found <> wanted THEN
            RAISE WARNING '% is not % - check the ids above against this catalog.',
                name, wanted;
        END IF;
    END LOOP;

    -- pushup_v1 is counted over the five added here: the catalog may or may
    -- not already carry it on Push Up itself.
    SELECT count(*) INTO found
      FROM exercises
     WHERE ai_algorithm = 'pushup_v1'
       AND exercise_id IN (171, 614, 373, 219, 374);
    RAISE NOTICE 'push-ups added to pushup_v1: % (expected 5)', found;
    IF found <> 5 THEN
        RAISE WARNING 'Not 5 - check the ids above against this catalog.';
    END IF;
END $$;
