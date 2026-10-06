-- V021 — lyrics for the library's songs (owner decisions 2026-10-03).
--
-- One row per library track. Three sources, in the household's order:
--   1. the owner's own .lrc beside the song: same base name, any case,
--      re-read when its mtime or size changes          (local_source sidecar)
--   2. lyrics inside the file: ID3 USLT / SYLT, Vorbis LYRICS /
--      UNSYNCEDLYRICS, the MP4 lyrics atom, WM/Lyrics, APE Lyrics;
--      LRC-formatted text counts as timed               (local_source embedded)
--   3. LRCLIB (lrclib.net), only while the household has it switched on
--                                                        (the lrclib_* columns)
-- A .lrc Domovoi wrote itself (it carries [re:Domovoi] AND is
-- byte-identical to what was written: lrc_sha256) is a copy of source 3,
-- never source 1. Plain-only LRCLIB lyrics are never written to disk.
--
-- Who writes what:
--   * domovoi/workers/lyrics_scan.py   creates the rows, and writes the
--     file_*, local_*, sidecar_* columns and lrc_state (edited, deleted)
--   * domovoi/workers/lyrics_fetch.py  the lrclib_* and lrc_* columns
--   * domovoi/workers/lyrics_index.py  lines_md5, lines_version and
--     every track_lyric_lines row
--   * the web process only reads (household tier: lyrics are never
--     served to an open page, and nothing logs their text).
-- Each writer updates only its own columns, so they never undo each other.
--
-- Timed lyrics are JSON arrays of [milliseconds, "line"] pairs in time
-- order, the file's [offset:] already applied; an empty line marks a gap.
-- A source with lyrics always has its plain column filled (built from
-- the timed lines when that is all there is), one lyric line per text
-- line.
--
-- What is SHOWN and SEARCHED is derived, never written: the owner's own
-- .lrc first, whatever it holds; then timed lyrics beat plain ones, and
-- within each kind the song's own tags beat LRCLIB. eff_source,
-- has_synced, instrumental and plain_md5 are generated here; the shown
-- text itself is the view track_lyrics_shown (no second copy is stored).
--
-- Status columns follow V020's lesson: a stamp records what was
-- concluded, and only an answer stamps. The internet being turned off,
-- the server being offline, a rate limit or an unreachable service leave
-- a row exactly as it was.
--
-- track_lyric_lines is lyric search's index: every distinct line of the
-- shown lyrics (span 1) and every pair of consecutive lines (span 2),
-- normalized by spoken_names.lyric_words, with how often it occurs (the
-- chorus weighting). pg_trgm ships in V001.
--
-- IF NOT EXISTS / OR REPLACE throughout, so a test lane patched by hand
-- takes it again cleanly. No semicolon or double dash inside a statement:
-- the test kits split this file naively.

CREATE TABLE IF NOT EXISTS track_lyrics (
    track_id             INTEGER     PRIMARY KEY REFERENCES library_tracks(id) ON DELETE CASCADE,

    -- the audio file as the local scan last saw it
    file_mtime_ns        BIGINT,
    file_size            BIGINT,
    file_missing         BOOLEAN     NOT NULL DEFAULT FALSE,
    local_checked_at     TIMESTAMPTZ,                     -- NULL = the scan has not looked yet
    scan_version         SMALLINT    NOT NULL DEFAULT 0,  -- domovoi.lyrics.SCAN_VERSION that read it

    -- local lyrics: source 1 or 2
    local_source         TEXT,                            -- sidecar | embedded | NULL = none found
    local_detail         TEXT,                            -- sidecar: its file name, embedded: the tag
    local_plain          TEXT,
    local_synced         JSONB,                           -- NULL = not timed
    local_offset_ms      INTEGER,                         -- the [offset:] that was applied, for the record

    -- the .lrc beside the song, whoever wrote it (a file name in the song's folder)
    sidecar_name         TEXT,                            -- NULL = none
    sidecar_mtime_ns     BIGINT,
    sidecar_size         BIGINT,

    -- LRCLIB: source 3
    lrclib_status        TEXT,                            -- found | instrumental | not_found | skipped | error, NULL = never asked
    lrclib_id            BIGINT,
    lrclib_match         TEXT,                            -- get | search
    lrclib_plain         TEXT,
    lrclib_synced        JSONB,
    lrclib_instrumental  BOOLEAN     NOT NULL DEFAULT FALSE,
    lrclib_query_md5     TEXT,                            -- md5 of the title, artist, album and duration asked about
    lrclib_checked_at    TIMESTAMPTZ,                     -- the last ANSWER
    lrclib_next_at       TIMESTAMPTZ,                     -- not asked again before this, NULL = no timer
    lrclib_attempts      INTEGER     NOT NULL DEFAULT 0,  -- the same miss or refusal in a row
    lrclib_error         TEXT,                            -- short code, never a payload

    -- the .lrc Domovoi wrote for LRCLIB's timed lyrics
    lrc_state            TEXT,                            -- written | exists | edited | deleted | failed, NULL = never tried
    lrc_name             TEXT,                            -- a file name in the song's folder
    lrc_sha256           TEXT,                            -- of the bytes written
    lrc_attempted_at     TIMESTAMPTZ,
    lrc_error            TEXT,                            -- permission | outside_music_dir | io | name

    -- lyric search's bookkeeping: the plain_md5 the lines were built from
    lines_md5            TEXT,
    lines_version        SMALLINT,                        -- spoken_names.LYRIC_NORM_VERSION used

    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- derived: what is shown and searched
    eff_source           TEXT GENERATED ALWAYS AS (
        CASE
            WHEN local_source = 'sidecar' THEN 'sidecar'
            WHEN local_synced IS NOT NULL THEN local_source
            WHEN lrclib_synced IS NOT NULL THEN 'lrclib'
            WHEN local_plain IS NOT NULL THEN local_source
            WHEN lrclib_plain IS NOT NULL THEN 'lrclib'
        END
    ) STORED,
    has_synced           BOOLEAN GENERATED ALWAYS AS (
        CASE
            WHEN local_source = 'sidecar' OR local_synced IS NOT NULL THEN local_synced IS NOT NULL
            ELSE lrclib_synced IS NOT NULL
        END
    ) STORED,
    instrumental         BOOLEAN GENERATED ALWAYS AS (
        local_plain IS NULL AND lrclib_plain IS NULL AND lrclib_instrumental
    ) STORED,
    plain_md5            TEXT GENERATED ALWAYS AS (
        md5(
            CASE
                WHEN local_source = 'sidecar' OR local_synced IS NOT NULL THEN local_plain
                WHEN lrclib_synced IS NOT NULL THEN lrclib_plain
                ELSE COALESCE(local_plain, lrclib_plain)
            END
        )
    ) STORED,

    CONSTRAINT track_lyrics_local_source_chk
        CHECK (local_source IS NULL OR local_source IN ('sidecar', 'embedded')),
    CONSTRAINT track_lyrics_local_text_chk
        CHECK ((local_source IS NULL) = (local_plain IS NULL)),
    CONSTRAINT track_lyrics_local_synced_chk
        CHECK (local_synced IS NULL
               OR (local_plain IS NOT NULL AND jsonb_typeof(local_synced) = 'array')),
    CONSTRAINT track_lyrics_lrclib_status_chk
        CHECK (lrclib_status IS NULL
               OR lrclib_status IN ('found', 'instrumental', 'not_found', 'skipped', 'error')),
    CONSTRAINT track_lyrics_lrclib_match_chk
        CHECK (lrclib_match IS NULL OR lrclib_match IN ('get', 'search')),
    CONSTRAINT track_lyrics_lrclib_text_chk
        CHECK (lrclib_plain IS NULL OR lrclib_status = 'found'),
    CONSTRAINT track_lyrics_lrclib_found_chk
        CHECK (lrclib_status IS DISTINCT FROM 'found' OR lrclib_plain IS NOT NULL),
    CONSTRAINT track_lyrics_lrclib_synced_chk
        CHECK (lrclib_synced IS NULL
               OR (lrclib_plain IS NOT NULL AND jsonb_typeof(lrclib_synced) = 'array')),
    CONSTRAINT track_lyrics_lrc_state_chk
        CHECK (lrc_state IS NULL
               OR lrc_state IN ('written', 'exists', 'edited', 'deleted', 'failed')),
    CONSTRAINT track_lyrics_size_chk
        CHECK (COALESCE(octet_length(local_plain), 0) <= 262144
               AND COALESCE(octet_length(lrclib_plain), 0) <= 262144
               AND COALESCE(jsonb_array_length(
                       CASE WHEN jsonb_typeof(local_synced) = 'array' THEN local_synced END), 0) <= 4000
               AND COALESCE(jsonb_array_length(
                       CASE WHEN jsonb_typeof(lrclib_synced) = 'array' THEN lrclib_synced END), 0) <= 4000)
);

-- The lyrics a player shows and lyric search reads (the same rule as
-- eff_source): the owner's .lrc, then timed beats plain, then local
-- beats LRCLIB.
CREATE OR REPLACE VIEW track_lyrics_shown AS
SELECT l.track_id,
       l.eff_source AS source,
       CASE WHEN l.eff_source = 'sidecar' THEN l.local_detail END AS sidecar_name,
       CASE
           WHEN l.local_source = 'sidecar' OR l.local_synced IS NOT NULL THEN l.local_synced
           ELSE l.lrclib_synced
       END AS synced,
       CASE
           WHEN l.local_source = 'sidecar' OR l.local_synced IS NOT NULL THEN l.local_plain
           WHEN l.lrclib_synced IS NOT NULL THEN l.lrclib_plain
           ELSE COALESCE(l.local_plain, l.lrclib_plain)
       END AS plain,
       l.has_synced,
       l.instrumental,
       l.plain_md5,
       l.local_checked_at,
       l.lrclib_status,
       l.updated_at
FROM track_lyrics l;

CREATE TABLE IF NOT EXISTS track_lyric_lines (
    track_id   INTEGER  NOT NULL REFERENCES track_lyrics(track_id) ON DELETE CASCADE,
    span       SMALLINT NOT NULL,      -- 1 = one line, 2 = that line and the next
    line_no    SMALLINT NOT NULL,      -- where it first occurs, 0-based, in the shown line order
    repeats    SMALLINT NOT NULL,      -- how many times it occurs in the song
    text       TEXT     NOT NULL,      -- spoken_names.lyric_words(...) joined by single spaces
    PRIMARY KEY (track_id, span, line_no),
    CONSTRAINT track_lyric_lines_span_chk CHECK (span IN (1, 2)),
    CONSTRAINT track_lyric_lines_repeats_chk CHECK (repeats >= 1),
    CONSTRAINT track_lyric_lines_text_chk CHECK (char_length(text) BETWEEN 1 AND 2000)
);

-- Lyric search: candidate lines by trigram word similarity (the
-- <% operator), scored in Python (domovoi/handlers/shared/lyric_search.py).
CREATE INDEX IF NOT EXISTS idx_track_lyric_lines_trgm
    ON track_lyric_lines USING gin (text gin_trgm_ops);
