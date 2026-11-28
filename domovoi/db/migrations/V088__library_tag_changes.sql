-- V088 — What song recognition changed on each library track, so a run can
-- be undone (domovoi/library_tag_changes.py). Before this, the enricher,
-- its repair and the re-match overwrote a track's names in place, and the
-- files' own tags are never written, so nothing kept what they had been.
--
-- library_tag_changes: one row per track an enricher sweep (cause
-- 'enrich'), a repair ('repair'), a re-match ('rematch') or an undo
-- ('undo') changed, written in the same statement as the update.
--
--   track_id    the track; the history goes with it (ON DELETE CASCADE)
--   batch_id    the run that made the change: one per sweep, repair,
--               re-match or undo; an undo works on a whole batch
--   cause       which of the four made it
--   old / new   the track's tracked columns before and after: title,
--               artist, album, the MusicBrainz recording / release /
--               release group ids, year and genre (with their sources
--               and checked times), enriched_at and enrich_outcome
--   changed_at  when
--   undone_at   when an undo put "old" back (NULL: not undone)
--
-- A row is written only when the title, artist, album or recording id
-- moved. An undo restores "old" only on a track whose names still equal
-- "new", and never on one a person edited (enrich_outcome 'manual' or
-- 'reviewed').

CREATE TABLE IF NOT EXISTS library_tag_changes (
    id          BIGSERIAL    PRIMARY KEY,
    track_id    INT          NOT NULL REFERENCES library_tracks (id) ON DELETE CASCADE,
    batch_id    UUID         NOT NULL,
    cause       TEXT         NOT NULL CHECK (cause IN ('enrich', 'repair', 'rematch', 'undo')),
    old         JSONB        NOT NULL,
    new         JSONB        NOT NULL,
    changed_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    undone_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_library_tag_changes_batch
    ON library_tag_changes (batch_id);
CREATE INDEX IF NOT EXISTS idx_library_tag_changes_track
    ON library_tag_changes (track_id, changed_at);
