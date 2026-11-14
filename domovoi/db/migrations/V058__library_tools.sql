-- V058 — Library tools (1.3.0): what files say about themselves, the
-- duplicates finder, the "Needs review" list, playlists from files, and
-- cover art from the Cover Art Archive.
--
-- library_tracks:
--   musicbrainz_release_group_id
--                the album (release group) MusicBrainz files the
--                recording under: from the file's own tags, from song
--                recognition, or from the year fetch's release choice.
--                With musicbrainz_release_id (V001) it is what the cover
--                art is looked up by.
--   artist_credits
--                every artist the file credits (TXXX:ARTISTS and the
--                Vorbis / MP4 / ASF equivalents), NULL when it names none.
--                Searched (search_text below) and read by the voice
--                resolver, so "play songs by B" finds a song credited to
--                "A feat. B" whose artist field says only "A".
--   file_playlists
--                the playlists the file says it is on (TXXX:PLAYLIST ...).
--   search_text  recreated to include artist_credits (a generated column's
--                expression can't be altered in place on Postgres 16).
--   tags_version rows below 2 are read again by the tag backfill
--                (domovoi/workers/library_tag_backfill.py): genre keys,
--                the file's MusicBrainz ids, credits and playlists. This
--                is how "blogs" / "animation" genre keys (a video site's
--                category written as a genre) leave a library already
--                there.
--   enrich_outcome gains 'file' (the file named its own recording id, so
--                it was never fingerprinted) and 'reviewed' (a person
--                applied a candidate from "Needs review"); the column has
--                no CHECK, as before.
--
-- library_match_candidates   what song recognition found for a track it
--                didn't use (its answer disagreed with the file's names,
--                or was not sure enough): up to 5 candidates, kept for the
--                dashboard's "Needs review" list. decided: NULL (waiting),
--                'applied' or 'skipped' (a skip is remembered).
-- library_duplicate_keeps    "Keep both" on a duplicates group: the group's
--                members as they were, so a group whose membership changes
--                is shown again.
-- library_trash  copies moved out of the library by the duplicates
--                finder: where the file was, where it is now (a trash
--                folder inside the music folder), and its library row as
--                it was, so Restore can put both back.
-- library_cover_art   the Cover Art Archive's answer per track: written
--                into the file (with the original pictures' snapshot for
--                Undo), kept in Domovoi's own cache (a format Domovoi
--                doesn't write), no art available, or skipped (a
--                read-only folder, a file that changed).
-- playlists.imported_from / import_name
--                a playlist made from the music files (an .m3u/.m3u8 file
--                or the songs' PLAYLIST tags): 'files', and the name the
--                files give it. Read-only in Domovoi; re-synced on every
--                rescan. (V057 / V056 were left for other 1.3.0 work.)

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS musicbrainz_release_group_id TEXT;

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS artist_credits TEXT[]
        CHECK (artist_credits IS NULL OR cardinality(artist_credits) <= 32);

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS file_playlists TEXT[]
        CHECK (file_playlists IS NULL OR cardinality(file_playlists) <= 32);

-- The credits, each normalised like every other searched name (V046).
CREATE OR REPLACE FUNCTION library_search_credits(t text[]) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$
    SELECT coalesce(string_agg(library_search_norm(x), ' '), '') FROM unnest(t) AS x
$$;

DROP INDEX IF EXISTS idx_library_tracks_search_trgm;
ALTER TABLE library_tracks DROP COLUMN IF EXISTS search_text;
ALTER TABLE library_tracks
    ADD COLUMN search_text TEXT
    GENERATED ALWAYS AS (
        library_search_norm(title) || ' ' || library_search_norm(artist)
        || ' ' || library_search_norm(album)
        || ' ' || library_search_credits(artist_credits)
    ) STORED;
CREATE INDEX IF NOT EXISTS idx_library_tracks_search_trgm
    ON library_tracks
    USING gin (search_text gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_library_tracks_tags_pending_v2
    ON library_tracks (tags_version, id)
    WHERE tags_version < 2;

CREATE INDEX IF NOT EXISTS idx_library_tracks_artist_credits
    ON library_tracks
    USING gin (artist_credits);

CREATE TABLE IF NOT EXISTS library_match_candidates (
    track_id    INT          PRIMARY KEY REFERENCES library_tracks(id) ON DELETE CASCADE,
    reason      TEXT         NOT NULL CHECK (reason IN ('disagreed', 'low_confidence')),
    candidates  JSONB        NOT NULL,
    stored_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    decided     TEXT         CHECK (decided IS NULL OR decided IN ('applied', 'skipped')),
    decided_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_library_match_candidates_waiting
    ON library_match_candidates (stored_at)
    WHERE decided IS NULL;

CREATE TABLE IF NOT EXISTS library_duplicate_keeps (
    group_key   TEXT         PRIMARY KEY CHECK (char_length(group_key) <= 400),
    member_ids  INT[]        NOT NULL,
    decided_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS library_trash (
    id             SERIAL       PRIMARY KEY,
    original_path  TEXT         NOT NULL,
    trash_path     TEXT         NOT NULL,
    track          JSONB        NOT NULL,
    kept_track_id  INT          REFERENCES library_tracks(id) ON DELETE SET NULL,
    moved_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    restored_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_library_trash_open
    ON library_trash (moved_at DESC)
    WHERE restored_at IS NULL;

CREATE TABLE IF NOT EXISTS library_cover_art (
    track_id        INT          PRIMARY KEY REFERENCES library_tracks(id) ON DELETE CASCADE,
    state           TEXT         NOT NULL
        CHECK (state IN ('written', 'cached', 'no_art', 'skipped', 'same', 'undone')),
    source_kind     TEXT         CHECK (source_kind IS NULL OR source_kind IN ('release', 'release-group')),
    source_id       TEXT,
    note            TEXT         CHECK (note IS NULL OR char_length(note) <= 300),
    -- The original embedded pictures, kept before the first write
    -- (a JSON file under the cover-art folder in ~/.domovoi); NULL when
    -- the file had none (Undo then just removes what Domovoi added).
    snapshot_path   TEXT,
    had_art         BOOLEAN,
    created_tag     BOOLEAN      NOT NULL DEFAULT FALSE,
    -- Domovoi's own copy of the picture (formats it doesn't write).
    cache_path      TEXT,
    file_size       BIGINT,
    file_mtime_ns   BIGINT,
    written_at      TIMESTAMPTZ,
    checked_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

ALTER TABLE playlists
    ADD COLUMN IF NOT EXISTS imported_from TEXT
        CHECK (imported_from IS NULL OR imported_from IN ('files'));
ALTER TABLE playlists
    ADD COLUMN IF NOT EXISTS import_name TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_playlists_import_name
    ON playlists (lower(import_name))
    WHERE imported_from IS NOT NULL;
