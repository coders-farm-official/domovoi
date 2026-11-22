-- V072: rooms with several satellites (1.5.0).
--
-- A room is the place; a satellite is a device in one room. Until now a
-- room was one device: its id was the room, the pairing and every history
-- row. This adds `rooms` and turns `satellites` into the device table.
--
-- A pure re-key: every existing device gets satellite_id = its old room id
-- and sits in a room with that same id, so existing houses, pairing tokens
-- and old clients keep working with no action. Rooms that share a label
-- stay separate rooms (the Rooms page offers a merge; never silent).
--
-- Moved to rooms: room_label (as name), voice, quiet_mode/start/end.
-- Moved to satellites: room_wake_words.device_max (as wake_max).
-- Renamed to satellite_id: satellite_pairings, satellite_approvals,
-- satellite_health, satellite_outages, room_battery.

CREATE TABLE rooms (
    room_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    voice       TEXT,
    quiet_mode  TEXT NOT NULL DEFAULT 'house'
        CHECK (quiet_mode IN ('house', 'custom', 'off')),
    quiet_start TEXT,
    quiet_end   TEXT,
    -- NULL: automatic (the house default, raised per room on underruns).
    playout_ms  INTEGER CHECK (playout_ms IS NULL OR playout_ms BETWEEN 0 AND 10000),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT rooms_id_chk CHECK (char_length(room_id) BETWEEN 1 AND 120),
    CONSTRAINT rooms_name_chk CHECK (char_length(btrim(name)) BETWEEN 1 AND 120)
);

COMMENT ON TABLE rooms IS
    'The places of the house. A room holds zero or more satellites; empty '
    'rooms are kept until an admin deletes them.';

-- A paired device with no inventory row gets one, so every device has a
-- room. The phone app's chat mic (phone-...) is not a room.
INSERT INTO satellites (room_id, adopted_via)
SELECT p.room_id, 'hello'
FROM satellite_pairings p
WHERE p.room_id NOT LIKE 'phone-%'
  AND NOT EXISTS (SELECT 1 FROM satellites s WHERE s.room_id = p.room_id);

-- The rooms: every device's, plus rooms known only by a music player or
-- a wake-word row. Room-level settings move over from the device row.
INSERT INTO rooms (room_id, name, voice, quiet_mode, quiet_start, quiet_end)
SELECT u.room_id,
       COALESCE(NULLIF(btrim(s.room_label), ''), u.room_id),
       s.voice,
       COALESCE(s.quiet_mode, 'house'),
       s.quiet_start,
       s.quiet_end
FROM (SELECT room_id FROM satellites
      UNION SELECT room_id FROM mpd_rooms WHERE room_id NOT LIKE 'phone-%'
      UNION SELECT room_id FROM room_wake_words WHERE room_id NOT LIKE 'phone-%') u
LEFT JOIN satellites s ON s.room_id = u.room_id;

-- satellites: the device table. Its id is the old room id.
ALTER TABLE satellites RENAME COLUMN room_id TO satellite_id;
ALTER TABLE satellites ADD COLUMN room_id TEXT;
UPDATE satellites SET room_id = satellite_id;
ALTER TABLE satellites ALTER COLUMN room_id SET NOT NULL;
ALTER TABLE satellites ADD CONSTRAINT satellites_room_fk
    FOREIGN KEY (room_id) REFERENCES rooms (room_id) ON UPDATE CASCADE;
CREATE INDEX idx_satellites_room ON satellites (room_id);

ALTER TABLE satellites ADD COLUMN name TEXT;
ALTER TABLE satellites ADD COLUMN wake_max SMALLINT
    CHECK (wake_max IS NULL OR wake_max BETWEEN 1 AND 5);
ALTER TABLE satellites ADD COLUMN output_latency_us INTEGER;
ALTER TABLE satellites ADD COLUMN calibrated_at TIMESTAMPTZ;

UPDATE satellites s SET wake_max = w.device_max
FROM room_wake_words w
WHERE w.room_id = s.satellite_id AND w.device_max IS NOT NULL;

ALTER TABLE satellites DROP COLUMN room_label;
ALTER TABLE satellites DROP COLUMN voice;
ALTER TABLE satellites DROP COLUMN quiet_mode;
ALTER TABLE satellites DROP COLUMN quiet_start;
ALTER TABLE satellites DROP COLUMN quiet_end;

ALTER TABLE room_wake_words DROP CONSTRAINT IF EXISTS room_wake_words_device_max_chk;
ALTER TABLE room_wake_words DROP COLUMN device_max;

-- Device tables keyed by the device: same values, new column name.
ALTER TABLE satellite_pairings RENAME COLUMN room_id TO satellite_id;
ALTER TABLE satellite_approvals RENAME COLUMN room_id TO satellite_id;
ALTER TABLE satellite_approvals ADD COLUMN requested_room TEXT;
ALTER TABLE satellite_health RENAME COLUMN room_id TO satellite_id;
ALTER TABLE satellite_outages RENAME COLUMN room_id TO satellite_id;
ALTER TABLE room_battery RENAME COLUMN room_id TO satellite_id;

COMMENT ON COLUMN satellite_approvals.requested_room IS
    'The room the device asked to join (enrolment in=), pre-selected on the '
    'approval card. NULL: a new room named after the device.';

-- Turns: the room stays room_id; which device owned the turn and whose
-- mic was transcribed (slice 3 fills them).
ALTER TABLE conversation_log ADD COLUMN satellite_id TEXT;
ALTER TABLE conversation_log ADD COLUMN mic_satellite_id TEXT;
ALTER TABLE intents_log ADD COLUMN satellite_id TEXT;
ALTER TABLE intents_log ADD COLUMN mic_satellite_id TEXT;

-- Learned voice samples belong to the mic that heard them.
ALTER TABLE voice_profiles ADD COLUMN satellite_id TEXT;
UPDATE voice_profiles SET satellite_id = room_id WHERE room_id IS NOT NULL;

-- Rooms that hear each other form one wake group (one pair per row).
CREATE TABLE room_hear_links (
    room_a     TEXT NOT NULL REFERENCES rooms (room_id) ON DELETE CASCADE ON UPDATE CASCADE,
    room_b     TEXT NOT NULL REFERENCES rooms (room_id) ON DELETE CASCADE ON UPDATE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (room_a, room_b),
    CONSTRAINT room_hear_links_order_chk CHECK (room_a < room_b)
);
