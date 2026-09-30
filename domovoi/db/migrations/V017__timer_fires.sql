-- V017 — Timers and reminders reach the whole house, and every one that
-- goes off leaves a record.
--
-- Until now a timer or reminder that came due was DELETEd from `timers`
-- and spoken once, in the room it was set in, if that room's satellite
-- happened to be connected and idle at that instant. Anything else (the
-- room mid-reply, the satellite reconnecting after a core restart, a
-- reminder set from the app with no room at all) was dropped with one log
-- line, and nobody else in the house was told. Owner decision 2026-09-30:
-- EVERY online satellite announces every timer and reminder, the app and
-- the dashboard notify too, and each satellite has one setting, "Only
-- reminders for this device", that limits it to the ones set on it. The
-- room a timer or reminder was set in always announces its own.
--
-- Three tables:
--
-- * timer_own_only_rooms — a row IS that setting turned ON for the room.
--   No row = OFF, the default for every room, including rooms added later.
--   Written by the web process (device tier: it only makes a room say
--   less); read by the core each time something goes off. Deleted when a
--   room is retired.
--
-- * timer_fires — one row per timer or reminder that went off. The core's
--   timer watcher deletes the `timers` row and inserts this one in ONE
--   transaction, so a timer is either still pending or recorded as fired,
--   never lost in between. `base_text` is the line the origin room speaks;
--   like `timers.message` it is household speech, kept in the database
--   only (no API serializes it) and pruned after timer_fire_retention_days
--   (default 7). A fire is a reminder iff its `message` IS NOT NULL.
--
-- * timer_fire_deliveries — one row per room the fire was (or is being)
--   announced in, and how that went. The primary key (fire_id, room_id)
--   is what makes "never twice in one room" hold across reconnects and
--   core restarts: a room's row is claimed (pending -> sending) before a
--   single frame is sent, and a room that reconnects later is added with
--   ON CONFLICT DO NOTHING. `detail` holds reason codes only (offline,
--   capturing, forced_over:followup, tts_failed, acknowledged:<room>, ...),
--   never speech. Written by the core only; the web reads it for the
--   dashboard and the phone ("heard in garage, kitchen").
--
-- IF NOT EXISTS throughout so a database patched by hand ahead of Flyway
-- (a test lane) still takes this migration cleanly.

-- A row IS the setting "Only reminders for this device" turned ON for that room.
-- No row = OFF (the default for every room, including rooms added later).
-- Written by the web process (device tier); read by the core at fire time.
-- Not a foreign key, like command_capture_rooms (V016): a room may predate `satellites`.
CREATE TABLE IF NOT EXISTS timer_own_only_rooms (
    room_id    TEXT PRIMARY KEY,
    enabled_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per timer or reminder that went off. The `timers` row is deleted in the same
-- transaction that inserts this one.
CREATE TABLE IF NOT EXISTS timer_fires (
    id             BIGSERIAL   PRIMARY KEY,
    timer_id       INTEGER     NOT NULL,              -- timers.id it came from (that row is gone)
    kind           TEXT        NOT NULL CHECK (kind IN ('timer', 'reminder')),
    label          TEXT,
    message        TEXT,                              -- NULL = plain timer
    origin_room_id TEXT,                              -- NULL = set with no room (app, dashboard chat)
    created_at     TIMESTAMPTZ NOT NULL,              -- when it was set (timers.created_at)
    due_at         TIMESTAMPTZ NOT NULL,              -- timers.expires_at
    fired_at       TIMESTAMPTZ NOT NULL DEFAULT now(),-- when the watcher popped it
    base_text      TEXT        NOT NULL,              -- the origin-room line, no prefix (household speech: DB only)
    settled_at     TIMESTAMPTZ,                       -- every target resolved and the grace window closed
    acked_at       TIMESTAMPTZ,                       -- someone said "stop the timer" right after it
    acked_by       TEXT                               -- the room it was said in
);
CREATE INDEX IF NOT EXISTS idx_timer_fires_fired_at  ON timer_fires (fired_at DESC);
CREATE INDEX IF NOT EXISTS idx_timer_fires_timer_id  ON timer_fires (timer_id);
CREATE INDEX IF NOT EXISTS idx_timer_fires_unsettled ON timer_fires (fired_at) WHERE settled_at IS NULL;

-- One row per room a fire was (or is being) announced in. The primary key is what makes
-- "never twice in one room" hold across reconnects and core restarts.
CREATE TABLE IF NOT EXISTS timer_fire_deliveries (
    fire_id     BIGINT      NOT NULL REFERENCES timer_fires (id) ON DELETE CASCADE,
    room_id     TEXT        NOT NULL,
    is_origin   BOOLEAN     NOT NULL DEFAULT FALSE,
    outcome     TEXT        NOT NULL DEFAULT 'pending'
                CHECK (outcome IN ('pending', 'sending', 'spoken', 'interrupted',
                                   'failed', 'offline', 'busy_timeout', 'cancelled')),
    detail      TEXT,                                 -- reason code, never speech (see §3.5)
    spoken_text TEXT,                                 -- the exact line spoken there (DB only)
    attempts    SMALLINT    NOT NULL DEFAULT 0,
    queued_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    PRIMARY KEY (fire_id, room_id)
);
CREATE INDEX IF NOT EXISTS idx_timer_fire_deliveries_room_finished
    ON timer_fire_deliveries (room_id, finished_at DESC);
