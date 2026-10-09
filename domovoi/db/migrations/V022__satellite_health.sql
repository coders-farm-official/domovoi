-- V022 — satellite health telemetry and outage reports (2026-10-09).
--
-- A satellite sends one `health` frame a minute (satellite/health.py): its
-- memory, fds, CPU, temperature and throttle flags, uptime, mic frames/s,
-- the wake loop's heartbeat, queue depths, the link and whether the
-- gateway and the core answer. The core keeps the latest per room in
-- memory (and does NOT drop it on disconnect - the last sample before an
-- outage is the point) and every sample here, pruned to 24 h per room.
--
-- `prev_health` in a hello is the record the previous life of that
-- satellite's process left in ~/.domovoi/last-health.json: its last sample,
-- the failed connects it saw while disconnected, the self-check actions it
-- took and why it exited. One row per report here, the last 20 per room.
--
-- Numbers and short strings only: no transcript, no audio, no person. What
-- the samples contain and who may read them: docs/SECURITY_PRIVACY.md.
--
-- IF NOT EXISTS throughout, so a test lane patched by hand takes it again
-- cleanly. No semicolon or double dash inside a statement: the test kits
-- split this file naively.

CREATE TABLE IF NOT EXISTS satellite_health (
    id           BIGSERIAL    PRIMARY KEY,
    room_id      TEXT         NOT NULL,
    received_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    sample       JSONB        NOT NULL,
    CONSTRAINT satellite_health_room_chk CHECK (char_length(room_id) BETWEEN 1 AND 120),
    CONSTRAINT satellite_health_sample_chk CHECK (jsonb_typeof(sample) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_satellite_health_room_time
    ON satellite_health (room_id, received_at DESC);

CREATE TABLE IF NOT EXISTS satellite_outages (
    id            BIGSERIAL    PRIMARY KEY,
    room_id       TEXT         NOT NULL,
    reported_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    prev_boot_id  TEXT,
    report        JSONB        NOT NULL,
    CONSTRAINT satellite_outages_room_chk CHECK (char_length(room_id) BETWEEN 1 AND 120),
    CONSTRAINT satellite_outages_report_chk CHECK (jsonb_typeof(report) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_satellite_outages_room_time
    ON satellite_outages (room_id, reported_at DESC);
