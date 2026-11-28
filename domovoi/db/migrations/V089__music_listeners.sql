-- V089 — Listen along (1.5.0): a paired phone plays a room's music itself,
-- as a listener of that room's queue (domovoi/music_listeners.py).
--
-- The core keeps the listeners in memory (it routes every music command)
-- and mirrors them here, for two readers:
--
--   * the web process, whose now-playing cards and group list name the
--     phones listening along ("Sam's phone is listening along"), by a
--     LEFT JOIN on devices for the name;
--   * the core itself after a restart: it reloads every row and drops the
--     ones whose phone sends no heartbeat within 60 seconds of boot.
--
-- One row per phone (a phone listens to one room at a time):
--
--   device_id     the phone, as it introduced itself (devices.device_id).
--                 Soft reference, no foreign key: a phone that never
--                 registered a name may still listen ("A phone").
--   host_room_id  the room whose music player it hears: a shared queue's
--                 host (V060), or a room playing alone. It moves with the
--                 queue on a hand-off. A listener is never one of a shared
--                 queue's rooms.
--   joined_at     when it joined (the order the cards list listeners in).
--
-- A listener whose host stopped, or whose phone went quiet for a minute,
-- loses its row. Nothing here is kept as history.

CREATE TABLE IF NOT EXISTS music_listeners (
    device_id     TEXT PRIMARY KEY,
    host_room_id  TEXT NOT NULL,
    joined_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_music_listeners_host ON music_listeners (host_room_id, joined_at);
