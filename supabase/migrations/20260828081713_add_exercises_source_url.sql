ALTER TABLE exercises
    ADD COLUMN source_url TEXT;
CREATE UNIQUE INDEX uq_exercises_source_url
ON exercises
    (
        source_url
    )
WHERE source_url IS NOT NULL;