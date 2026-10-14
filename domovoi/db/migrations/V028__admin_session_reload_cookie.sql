-- V028 — the admin session's reload window and its own browser cookie
-- (2026-10-14).
--
-- signed_in_at: when the session last proved the password. Owner decision:
-- a dashboard tab reloaded (or a new tab opened) in the same browser within
-- 60 minutes of signing in stays signed in as the admin. The Bearer lives in
-- the page's memory only and a reload forgets it; POST /api/auth/resume
-- hands it back to the page holding the session cookie while now() is within
-- ADMIN_RELOAD_WINDOW_MIN of signed_in_at (domovoi/admin_auth.py
-- resume_session). Absolute, not sliding: using the session never moves
-- signed_in_at; a login, first-run setup or a password change made with the
-- session does.
--
-- cookie_hash / cookie_token: the browser cookie is a secret of its own,
-- never the Bearer (review finding 4: a cookie is sent to every port of its
-- host, so the Bearer-as-cookie reached every other service on the box).
-- cookie_hash is the sha256 of that secret; cookie_token is the Bearer
-- wrapped under it (XOR with an HMAC-SHA256 of the secret), so the database
-- alone never yields the Bearer.
--
-- All three are NULL for every session that existed before this migration,
-- on purpose. No DEFAULT now() on signed_in_at: that would stamp every live
-- session "just signed in" and make each one resumable for an hour after the
-- upgrade. And with no cookie_hash, a pre-V028 cookie (which IS the Bearer)
-- stops rendering anything: that browser reads as signed out until its owner
-- signs in again. A tab still holding the Bearer in memory keeps working.
--
-- IF NOT EXISTS throughout, so a lane patched by hand takes it again
-- cleanly. No semicolon or double dash inside a statement: the test kits
-- split this file naively.

ALTER TABLE admin_sessions ADD COLUMN IF NOT EXISTS signed_in_at TIMESTAMPTZ;

ALTER TABLE admin_sessions ADD COLUMN IF NOT EXISTS cookie_hash TEXT;

ALTER TABLE admin_sessions ADD COLUMN IF NOT EXISTS cookie_token TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_admin_sessions_cookie_hash
    ON admin_sessions (cookie_hash) WHERE cookie_hash IS NOT NULL;
