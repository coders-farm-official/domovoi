-- V065 — reading scans: the text of a scanned page, read on the server (2026-11-16).
--
-- Scan reader (1.4.0, owner decision 2026-11-16): a scan saved to
-- Documents/Scans is a PDF of pictures. The core reads it in the
-- background (domovoi/scan_reader/runner.py), one page at a time, holding
-- off while any voice turn is under way: Tesseract OCR first, and for a
-- page OCR can't read well the vision model (worded "might say" wherever
-- it is shown; skipped with AI features off). Each page's text is saved
-- the moment it is read, so a restart carries on from the next page.
--
-- scan_reads, one row per scan read:
--   id            32 hex characters, made by the core.
--   scan_id       the scan session it came from (scan_jobs.id), when one did.
--   path          the scan, relative to Documents ("Scans/Scan 2026-11-16 09.12.pdf").
--   sha256        the file's bytes when the read started: the text belongs
--                 to exactly those bytes, so a copy (Ask about this
--                 document copies it into the chat) finds it, and an
--                 edited file doesn't.
--   pages         the PDF's page count (0 until the first page is read).
--   languages     the Tesseract languages asked ("eng").
--   state         queued | running | done | failed | cancelled.
--   waiting_for_voice  true while it holds off for a voice turn.
--   attempts      times the core started it (a restart counts); past three
--                 it fails cleanly instead of trying again.
--   summary       "What does it say?": the short summary, made once on the
--                 first ask and kept ({"text", "said", "notes", ...}).
--   error, error_code   a failed read's plain reason and help-desk code.
--
-- scan_read_pages, one row per page, saved as each page is read:
--   method        ocr | vision | none (couldn't be read).
--   confidence    OCR's mean word confidence, 0-100 (null for vision/none).
--   text          the page's text ('' for none).
--
-- The text is the household's letters: kept on the server only, in this
-- database, household-read like the Documents folder it came from, and
-- deleted with its row (an admin clearing the scan history keeps it: the
-- scan itself is still in Documents). docs/SECURITY_PRIVACY.md lists it.
--
-- IF NOT EXISTS throughout. No semicolon or double dash inside a
-- statement: the test kits split this file naively.

CREATE TABLE IF NOT EXISTS scan_reads (
    id                 TEXT        PRIMARY KEY,
    scan_id            TEXT,
    path               TEXT        NOT NULL,
    sha256             TEXT,
    pages              INTEGER     NOT NULL DEFAULT 0,
    languages          TEXT        NOT NULL DEFAULT 'eng',
    state              TEXT        NOT NULL DEFAULT 'queued'
                       CHECK (state IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    waiting_for_voice  BOOLEAN     NOT NULL DEFAULT false,
    attempts           INTEGER     NOT NULL DEFAULT 0,
    summary            JSONB,
    error              TEXT,
    error_code         TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at         TIMESTAMPTZ,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_scan_reads_path ON scan_reads (path, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_scan_reads_sha ON scan_reads (sha256) WHERE state = 'done';
CREATE INDEX IF NOT EXISTS idx_scan_reads_scan ON scan_reads (scan_id);
-- The core's queue: the oldest live read first.
CREATE INDEX IF NOT EXISTS idx_scan_reads_live ON scan_reads (created_at)
    WHERE state IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS scan_read_pages (
    read_id     TEXT        NOT NULL REFERENCES scan_reads(id) ON DELETE CASCADE,
    page        INTEGER     NOT NULL,
    method      TEXT        NOT NULL CHECK (method IN ('ocr', 'vision', 'none')),
    confidence  REAL,
    words       INTEGER     NOT NULL DEFAULT 0,
    text        TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (read_id, page)
);
