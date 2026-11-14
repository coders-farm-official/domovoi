-- V059 — A library track's cover version (1.3.0 library tools follow-up).
--
-- library_tracks:
--   cover_rev    a counter that moves whenever the track's art changes:
--                the Cover Art Archive writes it into the file or keeps
--                Domovoi's own copy, an Undo takes it out again, or a
--                rescan finds the file or its album-folder image changed
--                (cover_sig below). The API hands it out as ``cover_v`` and
--                puts it on the cover URL (``?v=``), so a client's cached
--                picture is replaced exactly when there is a new one.
--   cover_sig    what the last rescan saw of the art's sources: the audio
--                file's mtime and size plus the album-folder image's name,
--                mtime and size, hashed (domovoi/library_cover_version.py).
--                NULL until a rescan first reads it; that first reading
--                only records it, it never counts as a change.
--   cover_changed_at
--                when cover_rev last moved; the ``library.cover`` realtime
--                channel lists the most recent ones.
--
-- Idempotent (IF NOT EXISTS) like its neighbours.

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS cover_rev INT NOT NULL DEFAULT 0;

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS cover_sig TEXT
        CHECK (cover_sig IS NULL OR char_length(cover_sig) <= 64);

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS cover_changed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_library_tracks_cover_changed
    ON library_tracks (cover_changed_at DESC, id DESC)
    WHERE cover_changed_at IS NOT NULL;
