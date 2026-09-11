-- V010 — Named client devices, room-queue provenance, and queue blocks.
--
-- Three tables for one feature: a room's play queue becomes EDITABLE from
-- any client, every entry says which device put it there, and an admin can
-- take the queue away from a named device.
--
-- ── Why a provenance SIDECAR and not a queue table ──────────────────────
--
-- MPD already owns the room queue, and it is the only thing that can: it's
-- what the satellite's stream actually plays from, it survives a core
-- restart, and voice commands mutate it directly. A second DB-backed queue
-- would be a rival source of truth that drifts the first time anything
-- touches MPD without going through us.
--
-- So the queue stays in MPD and we annotate it. `room_queue_items` is keyed
-- by MPD's SONGID — not its position — because a songid is stable for the
-- life of a queue entry: moving an entry, or inserting ahead of it, changes
-- every Pos but no Id. Rows whose songid has left the queue are stale and
-- the reader reaps them; nothing depends on them being gone promptly.
--
-- ── What this is NOT ───────────────────────────────────────────────────
--
-- `queue_device_blocks` is a HOUSEHOLD POLICY control, not a security
-- boundary. A device id is self-asserted by the client over a trusted LAN
-- (same posture as the rest of the daily tier — see docs/SECURITY_PRIVACY.md),
-- so a determined user can claim a different one. It stops the kids' tablet
-- from hijacking the kitchen queue; it does not stop an attacker. The
-- dashboard says as much where the blocks are managed.
--
-- A block can target a device id, a device NAME, or both. Both matter:
--   * id   — survives a rename, which is the obvious way to slip a block;
--   * name — covers a reinstall (new id, same name the household uses) and
--            is the thing a person actually thinks in.
-- The enforcement matches EITHER, so a block created from the device roster
-- (which fills both) keeps working through a rename or a reinstall.

-- Client devices that have introduced themselves: browsers and phones, each
-- keyed by the stable per-install id it already generates for resume
-- positions (`browser-xxxx`, `android-xxxx`). `name` is the human label the
-- "added by" tag shows and the blocklist matches on; it's seeded from the
-- platform on first contact and editable afterwards.
CREATE TABLE devices (
    device_id     TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    -- Advisory only: 'browser' | 'android' | anything a future client sends.
    -- Not a CHECK — new client kinds shouldn't need a migration to connect.
    platform      TEXT,
    user_agent    TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The admin roster lists most-recently-seen first.
CREATE INDEX idx_devices_last_seen ON devices (last_seen_at DESC);

-- Who added each entry in a room's live MPD queue. See the songid note above.
-- `device_name` is a SNAPSHOT taken at add time; the reader LEFT JOINs
-- `devices` and prefers the live name, so a rename relabels the queue, while
-- a device that has since been forgotten still shows the name it used.
CREATE TABLE room_queue_items (
    room_id     TEXT    NOT NULL,
    song_id     INTEGER NOT NULL,
    -- Soft ref to devices.device_id: no FK, because forgetting a device must
    -- not delete queue history, and voice-added entries have no device at all.
    device_id   TEXT,
    device_name TEXT,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (room_id, song_id)
);

-- Reading one room's provenance, and the reap of entries MPD no longer has.
CREATE INDEX idx_room_queue_items_room ON room_queue_items (room_id, added_at);

-- Devices barred from editing a queue. `room_id` NULL = every room.
CREATE TABLE queue_device_blocks (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id   TEXT,
    device_name TEXT,
    room_id     TEXT,
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- A block that names nothing would match nothing (or, worse, read as
    -- "block everyone" to a future maintainer). Refuse it at the schema.
    CONSTRAINT queue_device_blocks_targets_something
        CHECK (device_id IS NOT NULL OR device_name IS NOT NULL)
);

-- One block per (device_id, scope) and per (device_name, scope). Postgres
-- treats NULLs as distinct, so a plain UNIQUE would let duplicate
-- all-rooms blocks pile up — hence the paired partial indexes, the same
-- shape playback_positions uses for its nullable person_id.
CREATE UNIQUE INDEX idx_queue_blocks_id_room
    ON queue_device_blocks (device_id, room_id)
    WHERE device_id IS NOT NULL AND room_id IS NOT NULL;
CREATE UNIQUE INDEX idx_queue_blocks_id_all
    ON queue_device_blocks (device_id)
    WHERE device_id IS NOT NULL AND room_id IS NULL;
CREATE UNIQUE INDEX idx_queue_blocks_name_room
    ON queue_device_blocks (device_name, room_id)
    WHERE device_name IS NOT NULL AND room_id IS NOT NULL;
CREATE UNIQUE INDEX idx_queue_blocks_name_all
    ON queue_device_blocks (device_name)
    WHERE device_name IS NOT NULL AND room_id IS NULL;
