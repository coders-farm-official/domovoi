-- ─── Video resume positions (web Videos tab + Android) ───────────────────
-- Videos are browsed live out of the Files media libraries (never indexed
-- into the DB), so a position row is keyed by (library_id, rel_path) rather
-- than an item id. Per-(device × person) like playback_positions: positions
-- deliberately do NOT sync across devices. `title` + `duration_sec` are
-- denormalized so the "recently played" strip renders without re-walking
-- the library or probing the file.

CREATE TABLE video_positions (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    library_id   TEXT        NOT NULL,
    rel_path     TEXT        NOT NULL,
    device_id    TEXT        NOT NULL,
    person_id    BIGINT      REFERENCES people(id) ON DELETE CASCADE,
    position_sec INTEGER     NOT NULL DEFAULT 0,
    duration_sec INTEGER,
    title        TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Same two-partial-unique-index shape as playback_positions: Postgres treats
-- NULLs as distinct, so a plain UNIQUE would let two "unidentified" rows
-- coexist for the same video+device. The pair gives ON CONFLICT concrete
-- targets for both upsert paths.
CREATE UNIQUE INDEX idx_video_positions_person
    ON video_positions (library_id, rel_path, device_id, person_id)
    WHERE person_id IS NOT NULL;
CREATE UNIQUE INDEX idx_video_positions_anon
    ON video_positions (library_id, rel_path, device_id)
    WHERE person_id IS NULL;
-- Recently-played reads: newest rows for one device (+ optional person).
CREATE INDEX idx_video_positions_device_person
    ON video_positions (device_id, person_id, updated_at DESC);
