-- V009 — Pending satellite approvals (portal onboarding).
--
-- The USB-adoption flow preseeds a pairing row before the device ever
-- connects, so its first `hello` matches an already-paired room (case 2 in
-- streaming.py) and nothing is taken on trust. The Wi-Fi setup portal
-- cannot do that: the core is NOT a participant in a portal adoption — the
-- customer's phone talks to the satellite directly, and the server first
-- hears about the device when it turns up on the LAN.
--
-- Left alone, such a device lands on case 1 (token + no row -> claim the
-- room), which is exactly the trust-on-first-use race USB adoption was
-- built to remove: anyone who guesses a room_id and connects first wins it.
--
-- So a portal-onboarded satellite parks here instead of pairing. It shows
-- the customer a four-digit code as its setup network closes, presents that
-- code on connect, and the dashboard asks a human to confirm the two match
-- before the pairing row is written. TOFU becomes a decision someone
-- actually made, and a mis-discovered core is caught because approval
-- happens on the dashboard you already trust.
--
-- Only devices that PRESENT an approval_code park here. A satellite with no
-- code (the manual/legacy path) keeps the old case-1 behaviour, still
-- governed by SATELLITE_PAIRING_STRICT — upgrading the server must not
-- strand satellites that were provisioned by hand.
--
-- token_hash is the sha256 of the pairing token the device presented, same
-- as satellite_pairings: approving copies it across, so approval binds the
-- room to THAT device and not merely to the room name.

CREATE TABLE satellite_approvals (
    room_id      TEXT PRIMARY KEY,
    token_hash   TEXT NOT NULL,
    code         TEXT,
    mac          TEXT,
    board        TEXT,
    sat_type     TEXT NOT NULL DEFAULT 'voice',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempts     INTEGER NOT NULL DEFAULT 1
);

COMMENT ON TABLE satellite_approvals IS
    'Satellites waiting for a human to approve them (portal onboarding). '
    'Approving moves token_hash into satellite_pairings; rejecting deletes '
    'the row and the device keeps retrying until approved or powered off.';
