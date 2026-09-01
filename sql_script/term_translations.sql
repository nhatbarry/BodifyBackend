-- ============================================================
-- TERM TRANSLATIONS - muscle groups & equipment
-- ============================================================
-- One table for both, as requested. Rather than a generic
-- (domain, ref_id) pair - which cannot carry a foreign key - this
-- uses an exclusive arc: two nullable FK columns with a CHECK that
-- exactly one is filled. That keeps ON DELETE CASCADE working, so a
-- deleted muscle group cannot leave orphaned translations behind.

BEGIN;

CREATE TABLE IF NOT EXISTS term_translations (
    translation_id BIGSERIAL PRIMARY KEY,

    muscle_id BIGINT,
    equipment_id BIGINT,

    language VARCHAR(10) NOT NULL,

    name VARCHAR(150) NOT NULL,
    description TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_term_translations_muscle
        FOREIGN KEY (muscle_id)
        REFERENCES muscle_groups(muscle_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_term_translations_equipment
        FOREIGN KEY (equipment_id)
        REFERENCES equipment(equipment_id)
        ON DELETE CASCADE,

    -- Exactly one target, never both and never neither.
    CONSTRAINT chk_term_translations_target
        CHECK (num_nonnulls(muscle_id, equipment_id) = 1),

    -- NULLs compare as distinct in Postgres unique constraints, so the
    -- equipment rows (muscle_id IS NULL) do not collide with each other.
    CONSTRAINT uq_term_translations_muscle UNIQUE (muscle_id, language),
    CONSTRAINT uq_term_translations_equipment UNIQUE (equipment_id, language)
);

CREATE INDEX IF NOT EXISTS idx_term_translations_language
    ON term_translations(language);

DROP TRIGGER IF EXISTS trg_term_translations_updated_at ON term_translations;
CREATE TRIGGER trg_term_translations_updated_at
BEFORE UPDATE ON term_translations
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


-- ============================================================
-- Vietnamese seed
-- ============================================================
-- Matched by English name rather than hard-coded id, so re-running on
-- another environment cannot attach a translation to the wrong row.
-- muscle_groups."None" is deliberately absent: it is an import
-- placeholder meaning "no secondary muscle", not a real muscle.

INSERT INTO term_translations (muscle_id, language, name)
SELECT mg.muscle_id, 'vi', v.name_vi
FROM (VALUES
    ('Abductors',      N'Cơ dạng'),
    ('Adductors',      N'Cơ khép'),
    ('Abs',            N'Cơ bụng'),
    ('Biceps',         N'Cơ nhị đầu'),
    ('Calves',         N'Bắp chân'),
    ('Chest',          N'Cơ ngực'),
    ('Forearms',       N'Cẳng tay'),
    ('Glutes',         N'Cơ mông'),
    ('Hamstrings',     N'Cơ đùi sau'),
    ('Hip Flexors',    N'Cơ gấp hông'),
    ('IT Band',        N'Dải chậu chày'),
    ('Lats',           N'Cơ xô'),
    ('Lower Back',     N'Lưng dưới'),
    ('Middle Back',    N'Lưng giữa'),
    ('Neck',           N'Cổ'),
    ('Obliques',       N'Cơ chéo bụng'),
    ('Palmar Fascia',  N'Cân gan tay'),
    ('Plantar Fascia', N'Cân gan chân'),
    ('Quads',          N'Cơ đùi trước'),
    ('Shoulders',      N'Vai'),
    ('Traps',          N'Cơ thang'),
    ('Triceps',        N'Cơ tam đầu'),
    ('Upper Back',     N'Lưng trên')
) AS v(name_en, name_vi)
JOIN muscle_groups mg ON mg.name = v.name_en
ON CONFLICT (muscle_id, language) DO UPDATE SET name = EXCLUDED.name;


INSERT INTO term_translations (equipment_id, language, name)
SELECT e.equipment_id, 'vi', v.name_vi
FROM (VALUES
    ('Bands',         N'Dây kháng lực'),
    ('Barbell',       N'Tạ đòn'),
    ('Bench',         N'Ghế tập'),
    ('Bodyweight',    N'Trọng lượng cơ thể'),
    ('Box',           N'Bục nhảy'),
    ('Cable',         N'Máy cáp'),
    ('Chains',        N'Xích tạ'),
    ('Dumbbell',      N'Tạ đơn'),
    ('EZ Bar',        N'Thanh tạ EZ'),
    ('Exercise Ball', N'Bóng tập'),
    ('Foam Roll',     N'Con lăn xốp'),
    ('Hip Thruster',  N'Máy đẩy hông'),
    ('Jump Rope',     N'Dây nhảy'),
    ('Kettle Bells',  N'Tạ ấm'),
    ('Lacrosse Ball', N'Bóng lacrosse'),
    ('Landmine',      N'Tạ đòn chốt góc'),
    ('Machine',       N'Máy tập'),
    ('Medicine Ball', N'Bóng tạ'),
    ('Other',         N'Khác'),
    ('Rings',         N'Vòng treo'),
    ('Rope',          N'Dây thừng'),
    ('Sled',          N'Xe đẩy tạ'),
    ('Tiger Tail',    N'Cây lăn Tiger Tail'),
    ('Trap Bar',      N'Thanh tạ lục giác'),
    ('Valslide',      N'Đĩa trượt')
) AS v(name_en, name_vi)
JOIN equipment e ON e.name = v.name_en
ON CONFLICT (equipment_id, language) DO UPDATE SET name = EXCLUDED.name;

COMMIT;
