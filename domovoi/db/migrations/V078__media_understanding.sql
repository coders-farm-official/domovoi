-- V078 — Media understanding: the catalogue, the runner's state, and the
-- tables the analysis slices write (1.5.0, design MEDIA-UNDERSTANDING §7).
--
-- Number note: V078 per the 1.5.0 coordinator (2026-11-22): 1.4.1's V072 is
-- on main and 1.5.0's own migrations moved to V073-V077. Renumber again if
-- the 1.5.0 numbers shift.
--
-- Pictures and Videos stay folders. This adds a catalogue of their image
-- and video files (media_items, kept by the media_catalog walker from
-- stat() alone) and what the background runner (domovoi/media_understanding/)
-- works out about each one. Nothing is ever written into the media files.
--
-- media_libraries: one row per library the walker has walked.
--   library_id      'core:pictures', 'core:videos', later plugin/removable ids.
--   walked_at       the last complete walk; NULL until the first one ends.
--                   Files found by a later walk are "new arrivals" (the
--                   daytime trickle takes only those), files found by the
--                   first walk are the backlog (night only).
--   present         whether the root was there at the last walk. A library
--                   that is not there (a drive unplugged) keeps its rows.
--
-- media_items: one row per image or video file.
--   (library_id, rel_path)   identity, as the Videos tab and Files use.
--   kind            image | video.
--   size_bytes, mtime        from stat(); a change re-queues the item.
--   fingerprint     size + SHA-256 of the first and last 1 MB (hex), made
--                   by the runner through the read-rate bucket. A moved or
--                   renamed file is matched by it and keeps its results.
--   taken_at, gps, place_name  photo date and (places on only) location.
--   state           pending | running | done | failed | skipped.
--   stage           the analyser under way (resume picks up there).
--   attempts, error_code     3 attempts, then failed with a help-desk code,
--                            skipped until the file changes.
--   new_arrival     found by a walk after the library's first one.
--   frame_pass      NULL (no frames yet) | quick | deep: the video frame
--                   pass this item has had (the adaptive budget, owner
--                   2026-11-22: a quick first pass over a big backlog, a
--                   later deepen pass up to the cap).
--   vision_model    the model the captions came from (a change re-queues
--                   captions only).
--   search_doc      tsvector over captions, tags, text, transcript,
--                   summary and names, kept by the writer; GIN-indexed.
--
-- media_item_stages: one row per (item, analyser): each analyser's own
--   state, pass, saved progress (resume), attempts and time spent (the
--   per-video time budget carries across a restart).
--
-- media_frames, media_segments, media_summaries: the photo/frame captions,
--   tags and OCR text; the video transcript; the video summary.
-- media_faces, face_groups, face_denylist: faces (off by default; an admin
--   turns them on; turning them off deletes them all).
-- media_excluded: the household's excluded folders (prefix '' = the whole
--   library). Excluding deletes the results under it.
-- media_runner_state: the one row the runner keeps for the dashboard
--   (phase, waiting for voice) and the vision benchmark.
--
-- household_settings 'media_understanding' = the switches. Seeded once:
-- "Understand photos and videos" is ON for a new install and OFF for a
-- house upgrading from 1.4.x (it already has people, rooms, history or an
-- admin) until an admin turns it on (owner answers, 2026-11-22, Q2).
-- Faces and places are off for everyone.

CREATE TABLE IF NOT EXISTS media_libraries (
    library_id   TEXT PRIMARY KEY,
    walked_at    TIMESTAMPTZ,
    present      BOOLEAN NOT NULL DEFAULT true,
    files        INTEGER NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS media_items (
    id            BIGSERIAL PRIMARY KEY,
    library_id    TEXT        NOT NULL,
    rel_path      TEXT        NOT NULL,
    kind          TEXT        NOT NULL CHECK (kind IN ('image', 'video')),
    size_bytes    BIGINT      NOT NULL,
    mtime         DOUBLE PRECISION NOT NULL,
    fingerprint   TEXT,
    taken_at      TIMESTAMPTZ,
    gps           JSONB,
    place_name    TEXT,
    state         TEXT        NOT NULL DEFAULT 'pending'
                  CHECK (state IN ('pending', 'running', 'done', 'failed', 'skipped')),
    stage         TEXT,
    attempts      INTEGER     NOT NULL DEFAULT 0,
    error_code    TEXT,
    new_arrival   BOOLEAN     NOT NULL DEFAULT false,
    frame_pass    TEXT        CHECK (frame_pass IN ('quick', 'deep')),
    duration_s    REAL,
    vision_model  TEXT,
    analysed_at   TIMESTAMPTZ,
    search_doc    TSVECTOR,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (library_id, rel_path)
);

-- The runner's queue: live items, newest files first.
CREATE INDEX IF NOT EXISTS idx_media_items_queue ON media_items (mtime DESC)
    WHERE state IN ('pending', 'running');
CREATE INDEX IF NOT EXISTS idx_media_items_fingerprint ON media_items (size_bytes, fingerprint)
    WHERE fingerprint IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_media_items_search ON media_items USING gin (search_doc);
CREATE INDEX IF NOT EXISTS idx_media_items_names ON media_items USING gin (rel_path gin_trgm_ops);

CREATE TABLE IF NOT EXISTS media_item_stages (
    item_id     BIGINT  NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
    analyser    TEXT    NOT NULL,
    state       TEXT    NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending', 'running', 'done', 'failed', 'skipped')),
    pass_kind   TEXT,
    progress    JSONB   NOT NULL DEFAULT '{}'::jsonb,
    attempts    INTEGER NOT NULL DEFAULT 0,
    error_code  TEXT,
    spent_s     REAL    NOT NULL DEFAULT 0,
    capped      BOOLEAN NOT NULL DEFAULT false,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (item_id, analyser)
);

CREATE TABLE IF NOT EXISTS media_frames (
    item_id   BIGINT NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
    idx       INTEGER NOT NULL,
    at_s      REAL,
    caption   TEXT NOT NULL DEFAULT '',
    tags      TEXT[] NOT NULL DEFAULT '{}',
    has_text  BOOLEAN,
    ocr_text  TEXT,
    model     TEXT,
    PRIMARY KEY (item_id, idx)
);

CREATE TABLE IF NOT EXISTS media_segments (
    item_id  BIGINT NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
    idx      INTEGER NOT NULL,
    start_s  REAL NOT NULL,
    end_s    REAL NOT NULL,
    text     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (item_id, idx)
);

CREATE TABLE IF NOT EXISTS media_summaries (
    item_id          BIGINT PRIMARY KEY REFERENCES media_items(id) ON DELETE CASCADE,
    summary          TEXT NOT NULL DEFAULT '',
    suggested_title  TEXT,
    model            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS face_groups (
    id         BIGSERIAL PRIMARY KEY,
    person_id  INT REFERENCES people(id) ON DELETE CASCADE,
    hidden     BOOLEAN NOT NULL DEFAULT false,
    centroid   BYTEA,
    model      TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS media_faces (
    id                   BIGSERIAL PRIMARY KEY,
    item_id              BIGINT NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
    at_s                 REAL,
    box                  REAL[] NOT NULL,
    quality              REAL,
    embedding            BYTEA NOT NULL,
    model                TEXT NOT NULL,
    group_id             BIGINT REFERENCES face_groups(id) ON DELETE SET NULL,
    person_id            INT REFERENCES people(id) ON DELETE SET NULL,
    confirmed            BOOLEAN NOT NULL DEFAULT false,
    rejected_person_ids  INT[] NOT NULL DEFAULT '{}',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_media_faces_item ON media_faces (item_id);
CREATE INDEX IF NOT EXISTS idx_media_faces_person ON media_faces (person_id) WHERE person_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_media_faces_group ON media_faces (group_id) WHERE group_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS face_denylist (
    id         BIGSERIAL PRIMARY KEY,
    embedding  BYTEA NOT NULL,
    model      TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS media_excluded (
    library_id  TEXT NOT NULL,
    prefix      TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (library_id, prefix)
);

CREATE TABLE IF NOT EXISTS media_runner_state (
    id                 INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    phase              TEXT NOT NULL DEFAULT 'idle',
    waiting_for_voice  BOOLEAN NOT NULL DEFAULT false,
    current_item       BIGINT,
    rescan_requested   BOOLEAN NOT NULL DEFAULT false,
    benchmark          JSONB,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO media_runner_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

INSERT INTO household_settings (key, value)
SELECT 'media_understanding', jsonb_build_object(
    'enabled', NOT upgraded,
    'faces', false,
    'places', false,
    'paused', false,
    'run_now', false,
    'frame_cap_mode', 'auto',
    'frame_cap', NULL,
    'set_by', CASE WHEN upgraded THEN 'upgrade' ELSE 'install' END)
FROM (SELECT (EXISTS (SELECT 1 FROM people)
              OR EXISTS (SELECT 1 FROM satellites)
              OR EXISTS (SELECT 1 FROM conversation_log)
              OR EXISTS (SELECT 1 FROM library_tracks)
              OR EXISTS (SELECT 1 FROM admin_auth)) AS upgraded) AS house
ON CONFLICT (key) DO NOTHING;
