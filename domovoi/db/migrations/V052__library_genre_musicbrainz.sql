-- V052 — A genre from MusicBrainz for a track whose file names none.
--
-- A track song recognition matched has a MusicBrainz recording id, and
-- MusicBrainz keeps genres for recordings, albums (release groups) and
-- artists. The year fetch (domovoi/workers/library_year_fetch.py) already
-- asks MusicBrainz about such a recording; it now asks for its genres in
-- the same request and, when the FILE names no genre, keeps the top-voted
-- (recording, else album, else artist).
--
--   genre_source      'tag' or 'musicbrainz': where genre / genre_keys
--                     came from; NULL with no genre. A file's own genre
--                     always wins: the tag backfill and the indexer write
--                     'tag' over a MusicBrainz genre, and keep a
--                     MusicBrainz genre when the file still names none.
--   genre_checked_at  when MusicBrainz was asked for this track's genre
--                     (an answer with no genre counts), so it is asked
--                     once. Tracks the year fetch already asked about
--                     (year_checked_at) have this NULL and are asked once
--                     more, for the genre; a track with a year and a genre
--                     is never asked.
--
-- The partial index is the genre half of the fetch's work queue, the size
-- of the queue. (V051 Up next listens came before this.)

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS genre_source TEXT
        CHECK (genre_source IS NULL OR genre_source IN ('tag', 'musicbrainz'));

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS genre_checked_at TIMESTAMPTZ;

-- Every genre there so far came from a file.
UPDATE library_tracks SET genre_source = 'tag'
    WHERE genre_source IS NULL AND (genre IS NOT NULL OR genre_keys IS NOT NULL);

CREATE INDEX IF NOT EXISTS idx_library_tracks_genre_pending
    ON library_tracks (id)
    WHERE genre_keys IS NULL AND musicbrainz_recording_id IS NOT NULL AND genre_checked_at IS NULL;
