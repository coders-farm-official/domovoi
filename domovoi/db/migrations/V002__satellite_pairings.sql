-- V002 — Satellite pairing tokens (WS auth for satellites).
--
-- Closes the LAN spoofing hole where any device could open the voice
-- WebSocket as an existing room (e.g. "kitchen") and be treated as that
-- room's satellite (and, via drop-in, listen in). The pairing model is
-- LENIENT trust-on-first-use: the first satellite to present a token for a
-- room CLAIMS it; thereafter that room's WS `hello` must carry the matching
-- token or it is refused. Only the sha256 of the token is ever stored here
-- (mirrors the admin_sessions token_hash pattern) — the raw token lives only
-- in the satellite's ~/.domovoi/pairing_token sidecar.
--
-- Case matrix (see domovoi/streaming.py:StreamSession._validate_pairing):
--   token + no row      -> PAIR (insert), accept
--   token + row match   -> accept, bump last_seen_at
--   token + row mismatch-> REFUSE (impostor with the wrong token)
--   no token + row      -> REFUSE (a paired room requires its token)
--   no token + no row   -> accept (older/unpaired) UNLESS
--                          SATELLITE_PAIRING_STRICT is on
--
-- Reset (DELETE the row) is an admin-gated op — needed after re-flashing a
-- Pi or moving a room to a new device, so the next connect re-pairs.

CREATE TABLE satellite_pairings (
    room_id      TEXT PRIMARY KEY,
    token_hash   TEXT NOT NULL,
    paired_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ
);
