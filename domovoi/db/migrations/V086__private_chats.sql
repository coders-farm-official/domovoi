-- V086 — Private chats (1.5.0, design-notes PRIVATE-CHATS.md).
--
-- A home-server chat is private to the phone or browser that started it,
-- with a "Share with household" switch; the house's admins can read,
-- share and delete every chat (never write in another device's). The rule
-- lives in one place, domovoi/chat_access.py.
--
-- The device key: a per-device secret minted by POST /api/devices/register
-- (shown once; the browser keeps it in an HttpOnly cookie, the apps in
-- their secure storage). Only its SHA-256 is stored. It is the one
-- identity chats trust: every self-asserted device id is ignored for them.
ALTER TABLE devices ADD COLUMN IF NOT EXISTS device_key_hash TEXT;
ALTER TABLE devices ADD COLUMN IF NOT EXISTS device_key_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_device_key
    ON devices (device_key_hash) WHERE device_key_hash IS NOT NULL;

-- A thread's owner (the device id its key resolves to; not a foreign key,
-- like chat_messages.device_id since V017) and whether it is shared with
-- the household. Private: an owner, not shared. Shared: shared, with or
-- without an owner. Admin-only: neither (chats nobody could be matched to).
ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS owner_device_id TEXT;
ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS shared BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS shared_changed_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_chat_threads_owner
    ON chat_threads (owner_device_id, archived, updated_at DESC);

-- Existing chats (owner decision 2026-11-25: they may disappear): private
-- to the device that wrote their first user message; none (a voice letter,
-- a "Photos:" or "Help:" chat, a thread never written to) leaves them
-- admin-only. Nothing records that they used to be shared, so this can't
-- be undone by going back to 1.4.x.
UPDATE chat_threads t SET owner_device_id = (
    SELECT m.device_id FROM chat_messages m
     WHERE m.thread_id = t.id AND m.role = 'user' AND m.device_id IS NOT NULL
     ORDER BY m.id LIMIT 1)
 WHERE t.owner_device_id IS NULL;

-- The phone a delivery ("Photos: …" to a phone) went to, so only that
-- phone's catch-up read lists it.
ALTER TABLE help_deliveries ADD COLUMN IF NOT EXISTS device_id TEXT;
