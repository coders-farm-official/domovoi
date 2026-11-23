-- V077: satellite support sessions (1.5.0, docs/SECURITY_PRIVACY.md,
-- "Support sessions").
--
-- An admin may open a shell on one satellite, as the satellite's service
-- account, for a fault that diagnostics can't fix. Two things here:
--
-- * satellites.support_allowed: the per-satellite "Allow support
--   sessions" switch. Off by default; turning it on is a security-tier
--   write that needs a password sign-in within the last five minutes.
-- * support_sessions: the audit list, one row per session, kept after the
--   satellite is retired (no foreign key). The transcript itself is a file
--   under ~/.domovoi/support-sessions/<id>.cast on the server.

ALTER TABLE satellites ADD COLUMN support_allowed BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE support_sessions (
    id                 TEXT PRIMARY KEY,
    satellite_id       TEXT NOT NULL,
    room_id            TEXT,
    -- The opener's admin session: its label and the first 12 hex digits of
    -- its token hash (enough to tell sessions apart, never the token).
    opener_label       TEXT,
    opener_hash_prefix TEXT,
    opened_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at          TIMESTAMPTZ,
    end_reason         TEXT,
    bytes_in           BIGINT NOT NULL DEFAULT 0,
    bytes_out          BIGINT NOT NULL DEFAULT 0,
    -- The transcript reached its cap (the session ended with "log full").
    truncated          BOOLEAN NOT NULL DEFAULT false,
    transcript_bytes   BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT support_sessions_id_chk CHECK (id ~ '^[0-9a-f]{32}$')
);

-- One open session per satellite, held by the database as well as the core.
CREATE UNIQUE INDEX support_sessions_one_open
    ON support_sessions (satellite_id) WHERE closed_at IS NULL;
CREATE INDEX idx_support_sessions_opened ON support_sessions (opened_at DESC);

COMMENT ON TABLE support_sessions IS
    'Satellite support sessions (admin shells): who opened which, when it '
    'ended and why. Transcripts live on disk, kept 30 days.';
