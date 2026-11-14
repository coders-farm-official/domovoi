-- V056 — Up next P7 and the Music mix.
--
-- (V055 is left for wf/cal-130, which claimed it first.)
--
-- 1. What ListenBrainz says about the library's artists and songs, fetched
--    ONLY by the core's background worker (domovoi/workers/listenbrainz_fetch.py)
--    and read by the Up next picker from here. Picking never asks the
--    network; nothing stored means the signal is simply absent.
--
--    artist_similarity: artists ListenBrainz relates to a library artist
--      (its similar-artists dataset, keyed by MusicBrainz artist id).
--      score is ListenBrainz's own figure (higher = closer); source names
--      the dataset ('listenbrainz').
--    artist_similarity_lookups: one row per library artist asked about,
--      so each is asked once and again after 90 days; outcome is
--      'ok' | 'none' | 'error', attempts counts errors in a row.
--    recording_popularity: how many listens and listeners ListenBrainz
--      counts for a song (by MusicBrainz recording id). NULL counts =
--      asked, ListenBrainz had nothing. Re-asked after 30 days.
--
--    Only public MusicBrainz ids leave the house for these: no titles, no
--    names, no play history, no person (docs/SECURITY_PRIVACY.md).
--
-- 2. The Music mix (Settings -> Household -> Music mix): the household's
--    choices live in household_settings (key 'music_mix', V045); a person's
--    own override of the two sliders lives on the person here, NULL = use
--    the household's.
--
-- 3. Each Up next pick keeps the signals that weighed most in choosing it
--    (up_next_picks.why), so "why this song?" says what really happened.
--
-- No semicolons or double dashes inside statements: the test kits split
-- this file naively.

CREATE TABLE IF NOT EXISTS artist_similarity (
    artist_mbid   TEXT         NOT NULL,
    similar_mbid  TEXT         NOT NULL,
    score         REAL         NOT NULL,
    source        TEXT         NOT NULL DEFAULT 'listenbrainz',
    fetched_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (artist_mbid, similar_mbid)
);

CREATE TABLE IF NOT EXISTS artist_similarity_lookups (
    artist_mbid  TEXT         PRIMARY KEY,
    fetched_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    outcome      TEXT         NOT NULL CHECK (outcome IN ('ok', 'none', 'error')),
    attempts     INT          NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS recording_popularity (
    recording_mbid  TEXT         PRIMARY KEY,
    listen_count    BIGINT,
    user_count      INT,
    fetched_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_recording_popularity_fetched
    ON recording_popularity (fetched_at);

-- Similar artists are mapped back to library songs by artist id.
CREATE INDEX IF NOT EXISTS idx_library_tracks_mb_artist_id
    ON library_tracks (musicbrainz_artist_id)
    WHERE musicbrainz_artist_id IS NOT NULL;

ALTER TABLE people ADD COLUMN IF NOT EXISTS music_mix JSONB;

ALTER TABLE up_next_picks ADD COLUMN IF NOT EXISTS why JSONB;
