-- V055 — Calendars the house reads from somewhere else (1.3.0).
--
-- Until now calendar_events held the household's own events (source
-- 'local'; 'google' was reserved by V001 for a sync that never shipped).
-- Two read-only kinds of calendar join them (owner decisions 2026-11-14:
-- READ-ONLY everywhere, no two-way sync, no sign-in with any calendar
-- company):
--
--   'link'   a private ICS/webcal address an admin pasted (Google's "secret
--            address in iCal format", Outlook's published ICS link, an
--            iCloud public calendar). The core's calendar_feed_refresher
--            worker reads it through the outbound gate
--            (domovoi/calendar_feeds.py) when the internet answer allows.
--   'phone'  a calendar on a household phone whose owner chose "Share this
--            phone's calendars with the house" in the Android app. The
--            phone sends a copy over the LAN (PUT /api/calendar/phones/
--            {device_id}); no internet involved.
--
-- calendar_sources: one row per such calendar.
--   name, color       how it is labelled everywhere ('#rrggbb')
--   person_id         whose it is, optional (a label: every household
--                     member sees every calendar, as before)
--   url               'link' only. A SECRET (anyone holding it can read the
--                     calendar): never logged in full, never returned by
--                     the API (url_host is what is shown, as "host/…").
--   etag, last_modified   the last answer's validators (conditional GET)
--   last_refreshed_at the last time the feed was read successfully (or,
--                     for a phone, the last snapshot it sent)
--   last_attempt_at, last_error, failures, next_refresh_at
--                     the refresher's bookkeeping; last_error is a short
--                     code (domovoi/calendar_feeds.py ERROR_WORDS says it in
--                     plain words), failures drives the backoff
--   device_id, phone_calendar_id, account_name
--                     'phone' only: which phone (devices.device_id,
--                     self-asserted like every device id), which of its
--                     calendars, and the account it lives in. One row per
--                     (phone, calendar), so one phone can't overwrite
--                     another's.
--
-- calendar_events gains calendar_source_id: the events of a link or phone
-- calendar, replaced as a whole on each refresh or snapshot and deleted
-- with their calendar (ON DELETE CASCADE). NULL for the household's own.
-- IF NOT EXISTS / IF EXISTS so a test lane patched by hand takes it again.

CREATE TABLE IF NOT EXISTS calendar_sources (
    id                  SERIAL PRIMARY KEY,
    kind                TEXT NOT NULL CHECK (kind IN ('link', 'phone')),
    name                TEXT NOT NULL,
    color               TEXT NOT NULL DEFAULT '#5b7a99',
    person_id           INT REFERENCES people(id) ON DELETE SET NULL,
    url                 TEXT,
    url_host            TEXT,
    etag                TEXT,
    last_modified       TEXT,
    last_refreshed_at   TIMESTAMPTZ,
    last_attempt_at     TIMESTAMPTZ,
    last_error          TEXT,
    failures            INT NOT NULL DEFAULT 0,
    next_refresh_at     TIMESTAMPTZ,
    device_id           TEXT,
    phone_calendar_id   TEXT,
    account_name        TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT calendar_sources_link_chk
        CHECK (kind <> 'link' OR (url IS NOT NULL AND url_host IS NOT NULL)),
    CONSTRAINT calendar_sources_phone_chk
        CHECK (kind <> 'phone' OR (device_id IS NOT NULL AND phone_calendar_id IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_calendar_sources_phone
    ON calendar_sources (device_id, phone_calendar_id) WHERE kind = 'phone';

ALTER TABLE calendar_events
    ADD COLUMN IF NOT EXISTS calendar_source_id INT
        REFERENCES calendar_sources(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_calendar_events_source_id
    ON calendar_events (calendar_source_id) WHERE calendar_source_id IS NOT NULL;

ALTER TABLE calendar_events DROP CONSTRAINT IF EXISTS calendar_events_source_chk;
ALTER TABLE calendar_events ADD CONSTRAINT calendar_events_source_chk
    CHECK (source IN ('google', 'local', 'link', 'phone'));
