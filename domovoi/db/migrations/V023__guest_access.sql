-- V023 — guests on a playing room, and the admin's guest bans (2026-10-10).
--
-- Owner decision: a phone or browser holding NO household credential (a
-- friend on the Wi-Fi) may change the queue of a room that is playing and
-- use its transport controls (pause, resume, skip, previous) — "until they
-- get banned by someone with admin privileges". The rule itself lives in
-- the core (domovoi/guest_queue.py); these tables are what lets an admin
-- see who those guests were and ban one.
--
-- ── What this is NOT ───────────────────────────────────────────────────
--
-- A guest ban is HOUSEHOLD POLICY, not a security boundary, exactly like
-- `queue_device_blocks` (V010). A guest's device id is self-asserted (a
-- cleared browser simply gets a new one) and its address is whatever the
-- DHCP lease says today. Matching either keeps a known phone out for as
-- long as it stays that phone on that lease; it does not stop someone
-- determined. The real gate is the household device token, and the admin
-- can turn guest access off altogether (GUEST_QUEUE_EDITING).
--
-- guests_seen: one row per (self-asserted device id, source address), the
-- last thing it did and where. Written by the core each time it lets a
-- guest act; read by an admin in Settings. Pruned to 30 days on write.
--
-- guest_bans: a ban matches a guest's device id OR its source address,
-- on every room, for queue edits and transport alike. It is never
-- consulted for a caller holding a household credential.
--
-- room_queue_items.guest_*: who, among guests, queued an entry — so an
-- admin can ban that guest from the queue row. The row's own device_id
-- stays NULL for a guest's entry (V010's soft ref to devices must never
-- join a name the guest only claimed).
--
-- IF NOT EXISTS throughout, so a test lane patched by hand takes it again
-- cleanly. No semicolon or double dash inside a statement: the test kits
-- split this file naively.

CREATE TABLE IF NOT EXISTS guests_seen (
    device_id    TEXT         NOT NULL DEFAULT '',
    source       TEXT         NOT NULL,
    user_agent   TEXT,
    room_id      TEXT,
    last_action  TEXT,
    first_seen   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, source)
);

CREATE INDEX IF NOT EXISTS idx_guests_seen_last_seen ON guests_seen (last_seen DESC);

CREATE TABLE IF NOT EXISTS guest_bans (
    id          BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id   TEXT,
    source      TEXT,
    note        TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT guest_bans_names_someone CHECK (device_id IS NOT NULL OR source IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_guest_bans_who
    ON guest_bans ((COALESCE(device_id, '')), (COALESCE(source, '')));

CREATE INDEX IF NOT EXISTS idx_guest_bans_device ON guest_bans (device_id) WHERE device_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_guest_bans_source ON guest_bans (source) WHERE source IS NOT NULL;

ALTER TABLE room_queue_items ADD COLUMN IF NOT EXISTS guest_device_id TEXT;

ALTER TABLE room_queue_items ADD COLUMN IF NOT EXISTS guest_source TEXT;
