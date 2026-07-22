-- V003 — Satellite inventory (adoption metadata + satellite type).
--
-- Distinct from satellite_pairings (WS auth) and mpd_rooms (lazy MPD
-- provisioning): a row here means "this satellite is known" — adopted via
-- the USB flow, or self-registered by a `hello` frame that carried an
-- explicit sat_type. A row may exist before the first WS connect (the
-- dashboard shows such satellites as "waiting").
--
-- `room_id` is the satellite's IDENTITY (the WS path / MPD key / config
-- value), kept under its historical wire name for compatibility. Every
-- satellite has its own MPD instance; `room_label` is optional display
-- metadata grouping several satellites that share a physical room (e.g. a
-- voice puck and a video display in the living room). The label never
-- reaches the device — it lives only here and in the dashboard.
--
-- Write rules (enforced in app code, not constraints):
--   * the USB-adoption preseed upserts the full row;
--   * the WS `hello` handler upserts sat_type ONLY when the frame carries
--     it explicitly — an old client omitting the field must never reset an
--     adopted row back to 'voice'.

CREATE TABLE satellites (
    room_id     TEXT PRIMARY KEY,
    -- OPEN enum (registered_values domain 'sat_type'), app-validated.
    sat_type    TEXT NOT NULL DEFAULT 'voice',
    -- Optional physical-room grouping label ("Living Room"); NULL = ungrouped.
    room_label  TEXT,
    -- Human hardware description, e.g. 'Raspberry Pi Zero 2 W Rev 1.0'.
    hardware    TEXT,
    -- Machine board slug from the provisioning flow, e.g. 'raspberry_pi_zero_2_w'.
    board       TEXT,
    -- wlan MAC (lowercase, colon-separated) — correlates a re-plugged
    -- device with its existing row during USB adoption.
    mac         TEXT,
    -- 'usb' | 'manual' | 'hello' (app-validated provenance stamp).
    adopted_via TEXT,
    -- When the USB-adoption flow claimed the device; NULL = self-registered.
    adopted_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_satellites_mac ON satellites (mac) WHERE mac IS NOT NULL;

INSERT INTO registered_values (domain, value, owner) VALUES
    ('sat_type', 'voice', 'core'),
    ('sat_type', 'video', 'core');
