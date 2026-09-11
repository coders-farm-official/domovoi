-- Radio plugin V002 — play-without-favoriting + the Recent strip.
--
-- Two facts the V001 schema couldn't express:
--
--   1. A station can now be PLAYED without being favorited. The stream
--      proxy resolves by row id, so a directory hit has to be persisted
--      before it can play — but persisting it must not imply a favorite.
--      ``created_by_play`` marks exactly those implicitly-created rows so
--      the trim below can reclaim them; favoriting clears the flag (the
--      row is a keeper from then on).
--
--   2. Recency. ``last_played_at`` is the Recent strip's sort key. The
--      product decision is "10 and no history beyond that", so the web
--      layer trims on every play: rows outside the newest 10 lose their
--      stamp, and a ``created_by_play`` row that falls out AND was never
--      favorited is deleted outright. A row that was favorited at some
--      point keeps its detection history — only the stamp is dropped.
--      (That's why the trim can't just key off ``NOT favorited``: the FCC
--      import and a since-unfavorited station are both NOT favorited and
--      must both survive falling out of the window.)

ALTER TABLE radio_stations
    ADD COLUMN last_played_at  TIMESTAMPTZ,
    ADD COLUMN created_by_play BOOLEAN NOT NULL DEFAULT FALSE;

-- The Recent read ("newest 10 stamped rows") and the trim's window scan.
-- Partial: only a played station carries a stamp, and the trim keeps that
-- set at 10 rows, so this index stays tiny.
CREATE INDEX idx_radio_stations_recent
    ON radio_stations (last_played_at DESC, id DESC)
    WHERE last_played_at IS NOT NULL;
