-- V053 — Up next sessions and their picks (Up next phase P3, 2026-11-12).
--
-- ── up_next_sessions ──────────────────────────────────────────────────
--
-- One Up next per player: a target is 'device:<device_id>' (a browser or a
-- phone, kept by the web process, web/backend/api/up_next.py) or
-- 'room:<room_id>' (a room, later: phase P4). At most ONE active session per
-- target (the partial unique index): a new start ends the one before it.
--
--   mode       list (a song clicked in a list: the rest of that list, then
--              round again), continue (a whole list played on purpose:
--              similar songs once it is nearly over) or library (a song on
--              its own: similar songs from the whole library)
--   seed       the starting song and the list it came from, as JSON
--              (domovoi/up_next/session.py SeedRef, plus the list's name)
--   person_id  whose taste the mix follows. NULL = the household. ON DELETE
--              SET NULL: forgetting a person makes their mixes the
--              household's.
--   autoplay   keep playing similar songs when the queue ends
--   rng_seed   every random choice of a batch comes from (rng_seed, batch_no)
--   state      the rest of the session (the list cycle, the songs it picked,
--              early-skip demotions, artists excluded by "less like this",
--              the last five songs listened to), as JSON
--   version    optimistic locking: a write names the version it read
--   ended_at   set when a new start replaced it. Ended sessions are deleted
--              after 30 days (web/backend/api/up_next.py prunes them).
--
-- ── up_next_picks ─────────────────────────────────────────────────────
--
-- The songs a session handed out, numbered per session (seq from 1), in
-- batches. A client says which it already has (have_through_seq), so asking
-- twice for the same batch gets the same songs back.
--
--   reason_code   same_artist | same_era | same_genre | played_together |
--                 wildcard | similar_artist | from_list
--   reason_label  what the queue shows beside the song ("same artist",
--                 "90s", "from road trip"). Never names a person.
--   status        queued | played | skipped | removed
--   mpd_song_id   a room's queue entry for the pick (phase P4); NULL here
--
-- IF NOT EXISTS throughout, so a lane patched by hand takes it again
-- cleanly. No semicolon or double dash inside a statement: the test kits
-- split this file naively.

CREATE TABLE IF NOT EXISTS up_next_sessions (
    id          UUID         PRIMARY KEY,
    target      TEXT         NOT NULL CHECK (target LIKE 'room:%' OR target LIKE 'device:%'),
    person_id   INT          REFERENCES people(id) ON DELETE SET NULL,
    mode        TEXT         NOT NULL CHECK (mode IN ('list', 'continue', 'library')),
    seed        JSONB        NOT NULL,
    autoplay    BOOLEAN      NOT NULL DEFAULT TRUE,
    rng_seed    BIGINT       NOT NULL,
    batch_no    INT          NOT NULL DEFAULT 0,
    state       JSONB        NOT NULL DEFAULT '{}',
    version     INT          NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    ended_at    TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_up_next_sessions_active
    ON up_next_sessions (target) WHERE ended_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_up_next_sessions_ended
    ON up_next_sessions (ended_at) WHERE ended_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS up_next_picks (
    session_id     UUID         NOT NULL REFERENCES up_next_sessions(id) ON DELETE CASCADE,
    seq            INT          NOT NULL CHECK (seq >= 1),
    batch_no       INT          NOT NULL,
    track_id       INT          NOT NULL REFERENCES library_tracks(id) ON DELETE CASCADE,
    reason_code    TEXT         NOT NULL,
    reason_label   TEXT         NOT NULL,
    seed_track_id  INT,
    mpd_song_id    INT,
    status         TEXT         NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued', 'played', 'skipped', 'removed')),
    PRIMARY KEY (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_up_next_picks_batch
    ON up_next_picks (session_id, batch_no);
