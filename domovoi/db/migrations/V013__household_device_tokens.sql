-- V013 — The household device token (the "device tier").
--
-- Ordinary actions — the dashboard, the phone app and the satellites
-- playing music, editing a room queue, announcing to a room — sit between
-- the open LAN and the admin password: they should not need the admin
-- Bearer (that tier is for destructive and code-adjacent work), but they
-- should not be open to any host that can reach the server either. The
-- device tier is one shared secret per install: a 256-bit token that every
-- household client presents as `X-Device-Token`. An admin Bearer always
-- passes the same gate, so an operator never needs both.
--
-- ── Why the raw token is stored ─────────────────────────────────────────
--
-- Unlike admin sessions (sha256 only), the raw token has to be READABLE:
-- the dashboard shows it to an admin so it can be entered on a new phone,
-- and both processes mirror it to `~/.domovoi/device-token.txt` (mode 0600,
-- next to the setup code) so the clients on the server box and the test
-- harnesses can pick it up without an admin session. The database is
-- already the trust root for the admin hash and the satellite pairing
-- hashes; a reader of this table already holds everything. `token_hash`
-- is what the gate compares against, so validation is a constant-time
-- hash compare like every other credential here.
--
-- Single row, like `admin_auth`: one household per install. Rotation
-- replaces the row in place — `rotated_at` records when — and the previous
-- token is refused from that moment. The row is created at the first boot
-- of either process (`domovoi.admin_auth.ensure_device_token`), never by a
-- request, and it is rotated automatically when first-run admin setup
-- completes so a token read during the pre-setup window does not outlive
-- that window.
CREATE TABLE household_device_tokens (
    id          INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    token       TEXT NOT NULL,
    token_hash  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at  TIMESTAMPTZ
);
