-- V073 — phone grants: admin sign-in on the phones for the rest of the day
-- (1.5.0, owner-approved 2026-11-22; docs/SECURITY_PRIVACY.md, "Admin on a
-- phone: the day's grant").
--
-- After a password sign-in on a phone, the phone may ask for a grant: a
-- 256-bit secret bound to its device id that it keeps behind the phone's
-- biometrics and swaps for a fresh admin session when the idle hour has
-- lapsed. grant_hash is the sha256 of that secret, never the secret.
-- expires_at is fixed at issue (created_at + 24 hours) and never moves.
-- One grant per device id: a new one replaces the old.
--
-- Rows are deleted on revoke (the phone's own, or an admin's on the
-- dashboard), on an admin password change and on --reset-admin.
--
-- admin_sessions.phone_grant_id names the grant a session was minted
-- from, so revoking (or replacing) a grant ends the sessions it made at
-- once. NULL for every password sign-in.
--
-- admin_sessions.hard_expires_at is an end no admin action moves: a session
-- minted from a grant ends when the grant does (its expires_at), even
-- inside its idle hour. NULL for every password sign-in.
--
-- IF NOT EXISTS throughout. No semicolon or double dash inside a
-- statement: the test kits split this file naively.

CREATE TABLE IF NOT EXISTS admin_phone_grants (
    id           BIGSERIAL PRIMARY KEY,
    grant_hash   TEXT NOT NULL UNIQUE,
    device_id    TEXT NOT NULL UNIQUE,
    label        TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_admin_phone_grants_expires
    ON admin_phone_grants (expires_at);

ALTER TABLE admin_sessions ADD COLUMN IF NOT EXISTS phone_grant_id BIGINT
    REFERENCES admin_phone_grants (id) ON DELETE CASCADE;

ALTER TABLE admin_sessions ADD COLUMN IF NOT EXISTS hard_expires_at TIMESTAMPTZ;
