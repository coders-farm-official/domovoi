-- V051 — Listening history: every song a player plays, and for whom
-- (Up next phase P1, 2026-11-12).
--
-- ── music_listens ──────────────────────────────────────────────────────
--
-- One row per library song a player played, written when the song ENDS:
--
--   * a room: the core's ListenLogger worker (domovoi/workers/listen_logger.py)
--     watches each room's music player and writes the song before when the
--     player moves on, stops, or is skipped;
--   * a browser or a phone: the player posts its own rows to
--     POST /api/music/listens (web/backend/api/music_listens.py), in batches,
--     late if it was offline (up to 7 days).
--
-- Unlike media_plays (V001, "Recently played"), which is room-only, logs a
-- START, covers every source and is pruned at 90 days, this is library
-- songs only, with how long they played, how they ended and the person they
-- were played for. Up next (later phases) learns from it.
--
--   player      room | browser | android | ios
--   origin      explicit (asked for) | list (from a list being played) |
--               auto (picked by Up next). Rooms and the browser send
--               explicit until Up next exists.
--   outcome     finished (90 percent of the song or more) | skipped (a skip
--               before that) | stopped (anything else: stop, a new play, the
--               page closed)
--   person_id   the person it was played for: a phone or browser's claimed
--               person (devices.owner_person_id) or the listener it named,
--               a room's last starter (music_room_listeners). NULL = the
--               household. ON DELETE CASCADE: forgetting a person deletes
--               their listening history with them.
--
-- The unique index makes a batch a phone sends twice (its answer was lost)
-- land once: the same device or room, song and start time is one listen.
--
-- Kept music_listens_keep days (365 by default, 90, or not at all), pruned
-- by the core's MusicListensPruner. Who may clear what: docs/SECURITY_PRIVACY.md,
-- "Listening history".
--
-- ── music_room_listeners ──────────────────────────────────────────────
--
-- Who a room's music is playing for, so the core can attribute a room's
-- listens: the identified voice that last started music there (NULL for an
-- unrecognised voice: the household), or the claimed person of the phone or
-- browser that last replaced the room's queue. One row per room, replaced
-- on every start. ON DELETE SET NULL: forgetting a person makes their room
-- the household's.
--
-- ── devices.claim_key_hash, devices.owner_claimed_at ───────────────────
--
-- "This is me" (devices.owner_person_id, V035) lets a phone or browser act
-- as its person: clear that person's listening history (favourites later).
-- A device id is self-asserted, so acting as the person needs more than the
-- id: when the device ITSELF claims a person it is handed a claim key once
-- (web/backend/api/devices.py), and only the key's SHA-256 is kept here.
-- The person's own actions must present the key (X-Domovoi-Claim-Key). An
-- admin setting or clearing a device's person, marking it a shared screen,
-- or the device letting go of its claim clears the hash. NULL on every row
-- from before (a V035 claim): such a device says "this is me" again.
-- owner_claimed_at: when the device last claimed (a person's history can
-- only be cleared by an admin in the first 10 minutes after a claim).

-- IF NOT EXISTS throughout, so a test lane patched by hand takes it again
-- cleanly. No semicolon or double dash inside a statement: the test kits
-- split this file naively.

CREATE TABLE IF NOT EXISTS music_listens (
    id            BIGSERIAL    PRIMARY KEY,
    track_id      INT          NOT NULL REFERENCES library_tracks(id) ON DELETE CASCADE,
    person_id     INT          REFERENCES people(id) ON DELETE CASCADE,
    room_id       TEXT,
    device_id     TEXT,
    player        TEXT         NOT NULL CHECK (player IN ('room', 'browser', 'android', 'ios')),
    session_id    UUID,
    origin        TEXT         NOT NULL CHECK (origin IN ('explicit', 'list', 'auto')),
    started_at    TIMESTAMPTZ  NOT NULL,
    played_sec    INT          NOT NULL CHECK (played_sec >= 0),
    duration_sec  INT          CHECK (duration_sec IS NULL OR duration_sec >= 0),
    outcome       TEXT         NOT NULL CHECK (outcome IN ('finished', 'skipped', 'stopped')),
    recorded_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (room_id IS NOT NULL OR device_id IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_music_listens_once
    ON music_listens (COALESCE(device_id, ''), COALESCE(room_id, ''), track_id, started_at);

CREATE INDEX IF NOT EXISTS idx_music_listens_person
    ON music_listens (person_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_music_listens_track
    ON music_listens (track_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_music_listens_room
    ON music_listens (room_id, started_at DESC) WHERE room_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_music_listens_device
    ON music_listens (device_id, started_at DESC) WHERE device_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_music_listens_started
    ON music_listens (started_at);

CREATE TABLE IF NOT EXISTS music_room_listeners (
    room_id    TEXT         PRIMARY KEY,
    person_id  INT          REFERENCES people(id) ON DELETE SET NULL,
    device_id  TEXT,
    set_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

ALTER TABLE devices ADD COLUMN IF NOT EXISTS claim_key_hash TEXT;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS owner_claimed_at TIMESTAMPTZ;
