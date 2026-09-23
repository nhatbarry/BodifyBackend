-- ============================================================
-- BODIFY - PHASE 7 (đợt 1: danh mục thực phẩm)
-- THE FOOD CATALOG THE MEAL DIARY IS LOGGED AGAINST
--
-- Four tables, holding two very different kinds of row that the diary has to
-- treat alike:
--
--   * **Thực phẩm** - rice, pork, a mango. Values are per 100 g, the way every
--     food composition table in the world publishes them.
--   * **Món ăn** - a bowl of xôi xéo, a bát phở. Values are per *serving*,
--     because that is how the National Institute of Nutrition publishes them,
--     and the weight of that serving is not part of what they publish.
--
-- Pretending those are the same number is how a food app quietly reports
-- double someone's intake, so `basis` says which one each row is and the app
-- asks for grams or for servings accordingly.
--
-- Sources, and why more than one:
--   nin_food  853  Viện Dinh dưỡng, thực phẩm  (per 100 g)
--   nin_dish 1247  Viện Dinh dưỡng, món ăn     (per serving)
--   usda    ~11400 FoodData Central            (per 100 g, foreign foods)
--   user             whatever the user types in themselves
--
-- Data from viendinhduong.vn is the Institute's, used here for a student
-- project with attribution: every row keeps `source` and `source_code`, so
-- what came from them can be listed, refreshed or removed in one statement.
--
-- Assumptions from earlier phases: none. This stands on its own.
-- Idempotent: every statement is guarded, so re-running is a no-op.
-- Rollback: sql_script/rollback_phase_7.sql
-- ============================================================


-- ============================================================
-- 1. CATEGORIES
-- ============================================================
-- Their own grouping, kept rather than remapped onto one of ours: "Bánh canh,
-- bánh đa, bún, cháo, súp, hoành thánh, hủ tiếu, miến, mỳ, phở, lẩu" is not a
-- category any of us would invent, but it is the one their 1247 dishes are
-- filed under, and inventing a mapping would mean owning it forever.

CREATE TABLE IF NOT EXISTS food_categories (
    food_category_id BIGSERIAL PRIMARY KEY,

    source      VARCHAR(16)  NOT NULL,
    source_code VARCHAR(64)  NOT NULL,

    name_vi     VARCHAR(200) NOT NULL,
    name_en     VARCHAR(200),

    CONSTRAINT uq_food_categories_source UNIQUE (source, source_code)
);


-- ============================================================
-- 2. FOODS
-- ============================================================
-- One row per thing a user can log, whether it is an ingredient or a finished
-- dish.
--
-- `energy_kcal` is NOT NULL and the macros are not: the Institute's rows are
-- occasionally missing a fat or a carbohydrate figure (30 of 1247), and
-- dropping an otherwise usable dish over one blank column would lose real
-- food. A missing macro shows as "-" rather than as zero, because zero grams
-- of fat is a claim and a blank is an absence.
--
-- `nutrients` keeps everything else the source published - vitamins, amino
-- acids, fatty acids, up to 150 keys on the USDA rows. JSONB because it is
-- display detail, read whole or not at all, and never filtered on. The five
-- figures the app actually computes with are columns, and only those.

CREATE TABLE IF NOT EXISTS foods (
    food_id     BIGSERIAL PRIMARY KEY,

    source      VARCHAR(16) NOT NULL,
    -- The identifier the source itself uses: 'VPF-000403', '10001', an FDC id.
    -- Null only for rows a user made up, which have no source to point at.
    source_code VARCHAR(64),

    food_category_id BIGINT REFERENCES food_categories (food_category_id)
                     ON DELETE SET NULL,

    -- Which question the numbers answer. See the header.
    basis       VARCHAR(12) NOT NULL DEFAULT 'per_100g',

    -- What one serving weighs, when anybody knows. Null is the normal state
    -- for a dish: the Institute publishes "one bowl of xôi xéo is 425 kcal"
    -- without saying what the bowl weighs. Where the same item also exists as
    -- a per-100 g food, the seeder divides one by the other and fills this in,
    -- and `serving_source` says it did.
    serving_grams  NUMERIC(7,1),
    serving_source VARCHAR(16),

    energy_kcal NUMERIC(7,1) NOT NULL,
    protein_g   NUMERIC(7,2),
    fat_g       NUMERIC(7,2),
    carb_g      NUMERIC(7,2),
    fiber_g     NUMERIC(7,2),

    nutrients   JSONB,

    -- Where the source's own photo lives. Stored as their path, not fetched:
    -- serving their images from their servers to our users would be taking
    -- bandwidth we were never given. A copy of our own goes in image_key once
    -- the images are mirrored to R2.
    image_url   TEXT,
    image_key   TEXT,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_foods_basis CHECK (basis IN ('per_100g', 'per_serving')),
    CONSTRAINT chk_foods_energy CHECK (energy_kcal >= 0),
    CONSTRAINT chk_foods_macros CHECK (
        (protein_g IS NULL OR protein_g >= 0) AND
        (fat_g     IS NULL OR fat_g     >= 0) AND
        (carb_g    IS NULL OR carb_g    >= 0) AND
        (fiber_g   IS NULL OR fiber_g   >= 0)
    ),
    CONSTRAINT chk_foods_serving CHECK (serving_grams IS NULL OR serving_grams > 0)
);

-- Re-importing a source must update its rows, not duplicate them. Partial,
-- because user-created foods have no source_code and several may be null.
CREATE UNIQUE INDEX IF NOT EXISTS uq_foods_source_code
    ON foods (source, source_code)
    WHERE source_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_foods_category ON foods (food_category_id);
CREATE INDEX IF NOT EXISTS idx_foods_source ON foods (source);


-- ============================================================
-- 3. NAMES
-- ============================================================
-- Names live here and nowhere else, the same rule `exercise_translations`
-- follows: `foods` has no name column, so there is one place a name can be
-- wrong and one place to fix it.
--
-- `name_ascii` is the unaccented form, searched against so that "pho bo"
-- finds "Phở bò" - typing Vietnamese diacritics on a phone while holding a
-- bowl of it is not something to demand. The Institute ships this field
-- already computed for its own search; for USDA rows it is just the name.

CREATE TABLE IF NOT EXISTS food_translations (
    food_translation_id BIGSERIAL PRIMARY KEY,

    food_id       BIGINT      NOT NULL REFERENCES foods (food_id) ON DELETE CASCADE,
    language_code VARCHAR(5)  NOT NULL,

    name          VARCHAR(300) NOT NULL,
    name_ascii    VARCHAR(300),

    CONSTRAINT uq_food_translations UNIQUE (food_id, language_code)
);

CREATE INDEX IF NOT EXISTS idx_food_translations_ascii
    ON food_translations (language_code, name_ascii);


-- ============================================================
-- 4. PORTIONS
-- ============================================================
-- "1 bát", "1 quả", "1 cup" and what it weighs. Only for per-100 g rows: a
-- row that is already a serving does not need to be told what a serving is.
--
-- USDA ships 32941 of these for the foods kept; the Vietnamese ones are added
-- by hand as they are needed, because "một bát" is a real measurement here and
-- grams are not how anybody thinks about rice.

CREATE TABLE IF NOT EXISTS food_portions (
    food_portion_id BIGSERIAL PRIMARY KEY,

    food_id    BIGINT       NOT NULL REFERENCES foods (food_id) ON DELETE CASCADE,

    label_vi   VARCHAR(120) NOT NULL,
    label_en   VARCHAR(120),
    grams      NUMERIC(7,1) NOT NULL,

    source     VARCHAR(16),
    sort_order INT          NOT NULL DEFAULT 0,

    CONSTRAINT chk_food_portions_grams CHECK (grams > 0)
);

CREATE INDEX IF NOT EXISTS idx_food_portions_food
    ON food_portions (food_id, sort_order);


-- ============================================================
-- 5. WHAT A SERVING IS MADE OF
-- ============================================================
-- The Institute's own numbers are per serving, and their API returns
-- `dish_components: []` for every dish - but the photo attached to each one is
-- not a photograph, it is an information card with the recipe printed on it:
-- "Thành phần - Quýt: 138g", "Cá trích hộp sốt cà chua: 155g". That is the
-- serving weight the rest of this schema needs, and the ingredient breakdown
-- underneath it.
--
-- Read off the card rather than published as data, so every row says so in
-- `source`, and nothing is written unless the energy printed on the same card
-- matches the energy the API gave for that dish - a card that disagrees with
-- its own dish was misread, and a misread gram figure is worse than none.

CREATE TABLE IF NOT EXISTS food_components (
    food_component_id BIGSERIAL PRIMARY KEY,

    food_id    BIGINT       NOT NULL REFERENCES foods (food_id) ON DELETE CASCADE,

    name_vi    VARCHAR(200) NOT NULL,
    grams      NUMERIC(7,1),

    -- 'nin_label' = transcribed from the Institute's own card.
    source     VARCHAR(16)  NOT NULL DEFAULT 'nin_label',
    sort_order INT          NOT NULL DEFAULT 0,

    -- Zero is allowed: the cards print "Muối: 0g" for a pinch too small to
    -- weigh. It is still an ingredient of the dish, and rejecting the row would
    -- drop the whole card over a seasoning.
    CONSTRAINT chk_food_components_grams CHECK (grams IS NULL OR grams >= 0)
);

CREATE INDEX IF NOT EXISTS idx_food_components_food
    ON food_components (food_id, sort_order);


DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_food_components_grams') THEN
        ALTER TABLE food_components DROP CONSTRAINT chk_food_components_grams;
        ALTER TABLE food_components ADD CONSTRAINT chk_food_components_grams
            CHECK (grams IS NULL OR grams >= 0);
    END IF;
END $$;


COMMENT ON TABLE food_components IS
    'Ingredients of one serving, transcribed from the source''s own label '
    'image. Only written when the label''s energy agrees with the food row.';


COMMENT ON TABLE foods IS
    'Everything loggable, ingredient or dish. basis says whether the numbers '
    'are per 100g or per serving - the two must never be added together '
    'without going through serving_grams.';
COMMENT ON COLUMN foods.nutrients IS
    'Everything the source published beyond the five columns. Display only: '
    'never filtered, sorted or computed on.';
COMMENT ON COLUMN foods.serving_grams IS
    'Weight of one serving where known. Null is normal for nin_dish rows - '
    'the Institute does not publish it.';


-- ============================================================
-- 5. VERIFY
-- ============================================================
DO $$
DECLARE
    n_foods      BIGINT;
    n_names      BIGINT;
    n_categories BIGINT;
    n_portions   BIGINT;
    n_components BIGINT;
BEGIN
    SELECT count(*) INTO n_foods FROM foods;
    SELECT count(*) INTO n_names FROM food_translations;
    SELECT count(*) INTO n_categories FROM food_categories;
    SELECT count(*) INTO n_portions FROM food_portions;
    SELECT count(*) INTO n_components FROM food_components;

    RAISE NOTICE 'foods: % row(s)', n_foods;
    RAISE NOTICE 'food_translations: % row(s)', n_names;
    RAISE NOTICE 'food_categories: % row(s)', n_categories;
    RAISE NOTICE 'food_portions: % row(s)', n_portions;
    RAISE NOTICE 'food_components: % row(s)', n_components;
END $$;
