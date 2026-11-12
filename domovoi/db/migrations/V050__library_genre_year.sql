-- V050 — Genre and year on every library track (Up next, phase 0).
--
-- The Genres tab on the Music page lists the library by genre, and Up next
-- (a later phase) picks songs near the one playing by artist, era and
-- genre. The library kept none of that: the indexer read title, artist,
-- album and length only, and it never updates a row it already has, so
-- the tracks already in the library are filled in by a paced backfill
-- (domovoi/workers/library_tag_backfill.py), not by "Rescan".
--
--   genre        the file's first genre as tagged (an ID3 "(17)" or an
--                ID3v1 / MP4 number already turned into its name), for
--                display; NULL when the file has none.
--   genre_keys   every genre of the file, normalised and split
--                (domovoi/library_genres.py: "Rock; Alt Rock" ->
--                {rock, alternative rock}, "Hip-Hop" and "Rap" ->
--                {hip hop}); NULL when the file has none. Two tracks share
--                a genre when their keys overlap.
--   year         the original year when the file says one (TDOR,
--                originaldate ...), else its date (TDRC, date, ©day ...);
--                else MusicBrainz's first-release-date for a track with a
--                recording id (domovoi/workers/library_year_fetch.py).
--   year_source  'tag' or 'musicbrainz': where year came from.
--   year_checked_at
--                when MusicBrainz was asked for this track's year (an
--                answer with no date counts), so it is asked once.
--   tags_version the backfill marker: the tag reader's version that last
--                read this row's genre and year (0 = never). The indexer
--                writes the current one on new rows; the backfill reads
--                the rest, 0 first. A later reader that reads more bumps
--                the version in Python and the backfill runs again.
--   artist_norm  library_search_norm(artist) (V046), generated: one
--                spelling per credit for "same artist" lookups.
--
-- The indexes are the reads Up next and the Genres tab make: by artist,
-- by year range, by genre overlap (GIN), and the two work queues (tracks
-- the backfill has not read, tracks waiting for a MusicBrainz year), each
-- partial so it stays the size of the queue.
-- (V049 voice-match columns came before this.)

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS genre TEXT
        CHECK (genre IS NULL OR char_length(genre) <= 200);

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS genre_keys TEXT[]
        CHECK (genre_keys IS NULL OR cardinality(genre_keys) <= 16);

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS year SMALLINT
        CHECK (year IS NULL OR year BETWEEN 1000 AND 3000);

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS year_source TEXT
        CHECK (year_source IS NULL OR year_source IN ('tag', 'musicbrainz'));

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS year_checked_at TIMESTAMPTZ;

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS tags_version SMALLINT NOT NULL DEFAULT 0;

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS artist_norm TEXT
    GENERATED ALWAYS AS (library_search_norm(artist)) STORED;

CREATE INDEX IF NOT EXISTS idx_library_tracks_artist_norm
    ON library_tracks (artist_norm);

CREATE INDEX IF NOT EXISTS idx_library_tracks_year
    ON library_tracks (year)
    WHERE year IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_library_tracks_genre_keys
    ON library_tracks
    USING gin (genre_keys);

CREATE INDEX IF NOT EXISTS idx_library_tracks_tags_pending
    ON library_tracks (tags_version, id)
    WHERE tags_version < 1;

CREATE INDEX IF NOT EXISTS idx_library_tracks_year_pending
    ON library_tracks (id)
    WHERE year IS NULL AND musicbrainz_recording_id IS NOT NULL AND year_checked_at IS NULL;
