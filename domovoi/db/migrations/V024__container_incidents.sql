-- V024 — the container guard's incidents and baselines (2026-10-11).
--
-- The core's container guard (domovoi/container_guard/, docs/CONTAINER_GUARD.md)
-- compares the Docker containers Domovoi expects (the compose stack, each
-- room's music player, every plugin's declared containers) against what
-- Docker reports, and records each distinct problem here.
--
-- class 'failure' (missing, stopped, restarting, unhealthy, a failed
-- one-shot, Docker not answering) resolves by itself when the problem
-- clears; an admin may acknowledge one to hide the dashboard banner.
-- class 'tamper' (a different image, an unknown container under Domovoi's
-- names, privileged/host settings Domovoi never gives) never resolves by
-- itself: it freezes plugin installs and upgrades, satellite code and
-- payload pushes, satellite media preparation and updates until an admin
-- clears it, and the clear is recorded (cleared_at / cleared_by / note).
--
-- One OPEN row per incident_key (the partial unique index); resolved rows
-- are history, kept 90 days (the guard prunes older ones), except a cleared
-- tamper finding that is still ACCEPTED: it stays until the guard has seen
-- the finding gone, however long the box was off. A failure that comes
-- back within 15 minutes of resolving reopens its row instead of opening a
-- new one (a flapping container, one incident). Container names, image
-- references and IDs only: nothing about a person.
--
-- IF NOT EXISTS throughout, so a test lane patched by hand takes it again
-- cleanly. No semicolon or double dash inside a statement: the test kits
-- split this file naively.

CREATE TABLE IF NOT EXISTS container_incidents (
    id               BIGSERIAL    PRIMARY KEY,
    incident_key     TEXT         NOT NULL,
    class            TEXT         NOT NULL,
    kind             TEXT         NOT NULL,
    severity         TEXT         NOT NULL,
    container        TEXT,
    owner            TEXT         NOT NULL,
    label            TEXT         NOT NULL,
    summary          TEXT         NOT NULL,
    expected         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    found            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    opened_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    resolved_at      TIMESTAMPTZ,
    resolution       TEXT,
    acknowledged_at  TIMESTAMPTZ,
    acknowledged_by  TEXT,
    cleared_at       TIMESTAMPTZ,
    cleared_by       TEXT,
    clear_note       TEXT,
    accepted         BOOLEAN      NOT NULL DEFAULT false,
    CONSTRAINT container_incidents_class_chk CHECK (class IN ('failure', 'tamper')),
    CONSTRAINT container_incidents_severity_chk CHECK (severity IN ('error', 'warn')),
    CONSTRAINT container_incidents_key_chk CHECK (char_length(incident_key) BETWEEN 1 AND 400),
    CONSTRAINT container_incidents_resolution_chk CHECK (
        resolution IS NULL OR resolution IN ('recovered', 'no_longer_expected', 'cleared')
    ),
    CONSTRAINT container_incidents_note_chk CHECK (clear_note IS NULL OR char_length(clear_note) <= 500)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_container_incidents_open_key
    ON container_incidents (incident_key) WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_container_incidents_opened
    ON container_incidents (opened_at DESC);

-- The dashboard's list (open, plus resolved in the last day) and the prune.
CREATE INDEX IF NOT EXISTS idx_container_incidents_resolved
    ON container_incidents (resolved_at DESC) WHERE resolved_at IS NOT NULL;

-- The cleared tamper findings still accepted (looked up every check).
CREATE INDEX IF NOT EXISTS idx_container_incidents_accepted
    ON container_incidents (incident_key) WHERE accepted;

CREATE TABLE IF NOT EXISTS container_guard_baselines (
    key          TEXT         PRIMARY KEY,
    value        TEXT         NOT NULL,
    source       TEXT         NOT NULL,
    recorded_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT container_guard_baselines_key_chk CHECK (char_length(key) BETWEEN 1 AND 120)
);
