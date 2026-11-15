-- V060 — Groups: rooms that share one music queue (1.3.0).
--
-- Two things are kept (SHARED-QUEUES-PLAN.md, owner answers 2026-11-14):
--
-- 1. Named groups: a lasting set of rooms the household made and named
--    ("upstairs", "the north wing"), used by name: "play jazz upstairs".
--    Nothing plays because a group exists; playing to one starts a shared
--    queue across its rooms. "Everywhere" is every room and has no row.
--
--    room_groups
--      name       as the household typed it ("The north wing")
--      name_key   how a spoken place compares with it (lower case, no
--                 articles, no spaces or punctuation, each word singular:
--                 handlers/shared/rooms.py room_key); unique, so two groups
--                 can never answer to the same words
--    room_group_rooms
--      one row per room in the group (a room id, as satellites name it); a
--      room may be in several groups. Rows go with their group.
--
-- 2. The live shared queue: one room's music player (the HOST) heard in
--    other rooms too (its MEMBERS), whose satellites play the host's
--    stream. Kept in the core's memory and mirrored here, so the web
--    process can show it and a core restart can pick it up again.
--
--    shared_queues
--      host_room_id     whose music player plays the queue; at most one
--                       live shared queue per host (the partial index)
--      group_id         the named group it was started for, if any (a
--                       group deleted later leaves the queue playing)
--      everywhere       started with "everywhere"
--      started_by_device the device that started it from the app or the
--                       dashboard (devices.device_id, self-asserted), else
--                       NULL (a voice turn)
--      updated_at       moved on every change; a core starting up ends a
--                       queue untouched for two hours
--      ended_at         NULL while it plays; set when the last other room
--                       left or it was ended. Ended rows are history only
--                       and are pruned after 30 days at boot.
--    shared_queue_rooms
--      one row per MEMBER room (the host is shared_queues.host_room_id).
--      room_id is unique: a room hears one shared queue at a time. A row
--      is deleted when its room leaves, and with its queue.
--      offline_since    when its satellite was last seen gone; after 30
--                       minutes offline the room leaves
--
-- IF NOT EXISTS so a test lane patched by hand takes it again.

CREATE TABLE IF NOT EXISTS room_groups (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 40),
    name_key    TEXT NOT NULL CHECK (char_length(name_key) BETWEEN 1 AND 80),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS room_groups_name_key ON room_groups (name_key);

CREATE TABLE IF NOT EXISTS room_group_rooms (
    group_id    INT NOT NULL REFERENCES room_groups(id) ON DELETE CASCADE,
    room_id     TEXT NOT NULL CHECK (char_length(room_id) BETWEEN 1 AND 64),
    PRIMARY KEY (group_id, room_id)
);

CREATE INDEX IF NOT EXISTS room_group_rooms_room ON room_group_rooms (room_id);

CREATE TABLE IF NOT EXISTS shared_queues (
    id                  UUID PRIMARY KEY,
    host_room_id        TEXT NOT NULL CHECK (char_length(host_room_id) BETWEEN 1 AND 64),
    group_id            INT REFERENCES room_groups(id) ON DELETE SET NULL,
    everywhere          BOOLEAN NOT NULL DEFAULT FALSE,
    started_by_device   TEXT CHECK (started_by_device IS NULL OR char_length(started_by_device) <= 64),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at            TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS shared_queues_live_host
    ON shared_queues (host_room_id) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS shared_queue_rooms (
    queue_id        UUID NOT NULL REFERENCES shared_queues(id) ON DELETE CASCADE,
    room_id         TEXT NOT NULL CHECK (char_length(room_id) BETWEEN 1 AND 64),
    joined_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    offline_since   TIMESTAMPTZ,
    PRIMARY KEY (queue_id, room_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS shared_queue_rooms_one_per_room
    ON shared_queue_rooms (room_id);
