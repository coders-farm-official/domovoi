-- V057 — Phones' shared calendars: whose they are, how long they last,
-- and the same event from two phones shown once (1.3.0 review fixes).
--
-- calendar_phones: one row per phone that has shared calendars.
--   device_id          the phone (devices.device_id; self-asserted)
--   key_hash           SHA-256 (hex) of the calendar key the server handed
--                      the phone on its first snapshot. Every later PUT or
--                      DELETE of /api/calendar/phones/{device_id} must
--                      present the key (X-Domovoi-Calendar-Key), so one
--                      household device can't write or delete another
--                      phone's calendars. NULL: none handed out yet (a row
--                      from before V057, or after an admin removed the
--                      phone's calendars); the next snapshot mints one.
--   last_generation    the highest snapshot generation stored (the phone's
--                      own counter, optional in the body)
--   deleted_generation the phone turned sharing off at this generation: a
--                      snapshot with a generation at or below it arrived
--                      late (sent before the DELETE) and is refused (410)
--   deleted_at         when it last did
--   last_snapshot_at   the last snapshot stored
-- The row goes when the phone's calendars expire (7 days without a
-- snapshot), when an admin removes all of them, and when the household
-- device token is replaced.
--
-- calendar_events gains:
--   ical_uid   the event's iCalendar UID when its calendar gives one (a
--              phone sends UID_2445; a link's VEVENT UID). The same event
--              shared by two phones, or by a phone and a link, is listed
--              once (same ical_uid and start).
--   busy       a private or confidential event a phone sends as "busy":
--              no title or place, shown as "Busy".
--
-- calendar_sources.account_name is no longer stored (usually an e-mail
-- address): existing values are cleared.

CREATE TABLE IF NOT EXISTS calendar_phones (
    device_id           TEXT PRIMARY KEY,
    key_hash            TEXT,
    last_generation     BIGINT,
    deleted_generation  BIGINT,
    deleted_at          TIMESTAMPTZ,
    last_snapshot_at    TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO calendar_phones (device_id, last_snapshot_at)
SELECT device_id, max(last_refreshed_at)
FROM calendar_sources
WHERE kind = 'phone'
GROUP BY device_id
ON CONFLICT (device_id) DO NOTHING;

ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS ical_uid TEXT;
ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS busy BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_calendar_events_ical_uid
    ON calendar_events (ical_uid, starts_at) WHERE ical_uid IS NOT NULL;

UPDATE calendar_sources SET account_name = NULL WHERE account_name IS NOT NULL;
