-- V040 — a phone room's battery: its last report, its low-battery warnings
-- and the room's two switches (2026-10-25, domovoi/room_battery.py); and
-- every room's quiet-hours choice (domovoi/quiet_hours.py).
--
-- A phone in room mode reports its battery in its `health` frame
-- (`battery` = {percent, charging, on_power}). A phone room that is not on
-- its charger runs down and goes offline with no warning; these keep what
-- the core needs to warn first (at 20 % and again at 10 %, once per
-- discharge) and to say "probably out of battery" once it has gone quiet.
--
-- satellites.battery_say     "Say when the battery is low": the room
--                            itself says so out loud. Default on.
-- satellites.battery_notify  "Notify when the battery is low": the
--                            dashboard's notice and the phones'
--                            notification. Default on.
--   Both are read at each warning (no restart). A room with no
--   `satellites` row reads as both on.
--
-- room_battery               the last battery report per room, written
--                            when it changes (and at least every 10 min).
--                            What the room list shows, and what the
--                            offline row reads after the room went quiet.
--                            Not a foreign key: a room may predate
--                            `satellites` (like command_capture_rooms).
--
-- room_battery_warnings      one row per warning. The open rows of a room
--                            (ended_at NULL) are its current discharge
--                            cycle: the thresholds already warned. Charging
--                            (or a level back above the thresholds) ends
--                            them. `spoken` is whether the room said it.
--
-- satellites.quiet_mode       'house' (the house's quiet hours, Settings →
--                            Voice & speech), 'custom' (quiet_start..
--                            quiet_end, "HH:MM") or 'off'. Any room, Pi,
--                            laptop or phone. Read at each announcement.
--
-- NOTIFY room_battery_changed → the live socket's `room_battery.changed`.

ALTER TABLE satellites ADD COLUMN IF NOT EXISTS battery_say BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE satellites ADD COLUMN IF NOT EXISTS battery_notify BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE satellites ADD COLUMN IF NOT EXISTS quiet_mode TEXT NOT NULL DEFAULT 'house'
    CHECK (quiet_mode IN ('house', 'custom', 'off'));
ALTER TABLE satellites ADD COLUMN IF NOT EXISTS quiet_start TEXT;
ALTER TABLE satellites ADD COLUMN IF NOT EXISTS quiet_end TEXT;

CREATE TABLE IF NOT EXISTS room_battery (
    room_id     TEXT PRIMARY KEY,
    percent     SMALLINT,
    charging    BOOLEAN,
    on_power    BOOLEAN,
    reported_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS room_battery_warnings (
    id          BIGSERIAL PRIMARY KEY,
    room_id     TEXT NOT NULL,
    threshold   SMALLINT NOT NULL,
    percent     SMALLINT NOT NULL,
    spoken      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_room_battery_warnings_open
    ON room_battery_warnings (room_id) WHERE ended_at IS NULL;
