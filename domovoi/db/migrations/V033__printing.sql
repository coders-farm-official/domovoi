-- V033 — printers and scanners on the home network (2026-10-21).
--
-- Domovoi prints to network printers that speak IPP Everywhere, AirPrint or
-- Mopria, and scans from eSCL (AirScan) scanners, with no drivers and no
-- CUPS (domovoi/printing/). Network printers only: no USB, no Windows
-- spooler (owner, 2026-10-21).
--
-- printers: one row per device the household added. A multi-function
-- printer is ONE row with both an IPP and an eSCL address. Remembered by
-- its UUID, so a new DHCP address is found again by discovery and the row
-- updates itself. The addresses here passed the LAN-only check when they
-- were stored (domovoi/printing/lan_target.py) and are checked again on
-- every connection. capabilities / scan_capabilities hold what the device
-- itself answered (Get-Printer-Attributes, ScannerCapabilities), never the
-- mDNS TXT hints. cert_sha256 is the IPPS (and eSCL over https)
-- certificate pin taken when the printer was added; cert_seen_sha256 a
-- different certificate seen since, which stops printing to it until an
-- admin trusts it.
--
-- printer_room_defaults: the printer a room prints to by default.
--
-- print_jobs / scan_jobs: the household's job log (Settings, Recent jobs).
-- Titles can be personal, so the log is household-read only, kept 90 days
-- (pruned hourly by the core) and an admin can clear it.
-- docs/SECURITY_PRIVACY.md lists it with the other stored data.
--
-- print_limits: the one row of caps an admin can change (paper and ink
-- abuse from any household device).
--
-- IF NOT EXISTS throughout. No semicolon or double dash inside a
-- statement: the test kits split this file naively.

CREATE TABLE IF NOT EXISTS printers (
    id                  BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                TEXT         NOT NULL,
    uuid                TEXT,
    make_and_model      TEXT         NOT NULL DEFAULT '',
    location            TEXT         NOT NULL DEFAULT '',
    host                TEXT         NOT NULL,
    ipp_scheme          TEXT,
    ipp_address         TEXT,
    ipp_port            INTEGER,
    ipp_path            TEXT,
    escl_scheme         TEXT,
    escl_address        TEXT,
    escl_port           INTEGER,
    escl_path           TEXT,
    cert_sha256         TEXT,
    escl_cert_sha256    TEXT,
    cert_seen_sha256    TEXT,
    capabilities        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    scan_capabilities   JSONB,
    status              JSONB,
    status_at           TIMESTAMPTZ,
    misses              INTEGER      NOT NULL DEFAULT 0,
    is_default          BOOLEAN      NOT NULL DEFAULT false,
    two_sided_default   BOOLEAN,
    color_default       BOOLEAN,
    added_via           TEXT         NOT NULL DEFAULT 'typed',
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT printers_does_something CHECK (ipp_address IS NOT NULL OR escl_address IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_printers_uuid ON printers (uuid) WHERE uuid IS NOT NULL AND uuid <> '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_printers_one_default ON printers (is_default) WHERE is_default;

CREATE TABLE IF NOT EXISTS printer_room_defaults (
    room_id     TEXT         PRIMARY KEY,
    printer_id  BIGINT       NOT NULL REFERENCES printers (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS print_jobs (
    id            BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    printer_id    BIGINT       REFERENCES printers (id) ON DELETE SET NULL,
    printer_name  TEXT         NOT NULL DEFAULT '',
    ipp_job_id    INTEGER,
    title         TEXT         NOT NULL DEFAULT '',
    library_id    TEXT,
    path          TEXT,
    pages         INTEGER      NOT NULL DEFAULT 0,
    copies        INTEGER      NOT NULL DEFAULT 1,
    sheets        INTEGER      NOT NULL DEFAULT 0,
    options       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    state         TEXT         NOT NULL DEFAULT 'queued',
    pages_done    INTEGER      NOT NULL DEFAULT 0,
    error         TEXT,
    who           TEXT         NOT NULL DEFAULT '',
    device_key    TEXT         NOT NULL DEFAULT '',
    source        TEXT         NOT NULL DEFAULT 'dashboard',
    room_id       TEXT,
    speaker       TEXT,
    announced     BOOLEAN      NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_print_jobs_created ON print_jobs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_print_jobs_active ON print_jobs (state) WHERE state IN ('queued', 'printing');

CREATE TABLE IF NOT EXISTS scan_jobs (
    id            TEXT         PRIMARY KEY,
    printer_id    BIGINT       REFERENCES printers (id) ON DELETE SET NULL,
    scanner_name  TEXT         NOT NULL DEFAULT '',
    state         TEXT         NOT NULL DEFAULT 'scanning',
    source_used   TEXT,
    pages         INTEGER      NOT NULL DEFAULT 0,
    name          TEXT,
    path          TEXT,
    incomplete    BOOLEAN      NOT NULL DEFAULT false,
    error         TEXT,
    who           TEXT         NOT NULL DEFAULT '',
    source        TEXT         NOT NULL DEFAULT 'dashboard',
    room_id       TEXT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_scan_jobs_created ON scan_jobs (created_at DESC);

CREATE TABLE IF NOT EXISTS print_limits (
    id                            SMALLINT     PRIMARY KEY DEFAULT 1,
    max_pages_per_job             INTEGER      NOT NULL DEFAULT 100,
    max_copies                    INTEGER      NOT NULL DEFAULT 20,
    max_sheets_per_job            INTEGER      NOT NULL DEFAULT 200,
    max_jobs_per_hour_per_device  INTEGER      NOT NULL DEFAULT 20,
    max_sheets_per_day            INTEGER      NOT NULL DEFAULT 500,
    max_scan_pages                INTEGER      NOT NULL DEFAULT 50,
    updated_at                    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT print_limits_one_row CHECK (id = 1)
);

INSERT INTO print_limits (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
