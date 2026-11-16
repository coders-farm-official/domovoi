-- V062 — Approvals a room device can check (1.3.1, "every house").
--
-- A room device set up through the Wi-Fi portal with no server address
-- now asks EVERY Domovoi server it finds for approval, and accepts only an
-- approval that proves the server knows the code the device said out loud
-- (satellite/pairing_proof.py: a password-authenticated key exchange).
-- Such a device never sends its code to any server, so for its request the
-- core keeps no code at all: `code` stays NULL and `proof` says why.
--
-- When the operator types the code on the dashboard, the core cannot tell
-- yet whether it is right — only the device can. It keeps the typed code
-- in `entered_code` for ONE exchange with the device (taken, never reused,
-- and stale after ten minutes), and counts the runs the device did not
-- confirm in `proof_failures`, so the dashboard can say "the room device
-- did not accept that code".

ALTER TABLE satellite_approvals
    ADD COLUMN proof BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN entered_code TEXT,
    ADD COLUMN entered_at TIMESTAMPTZ,
    ADD COLUMN proof_failures INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN last_proof_failure_at TIMESTAMPTZ;

COMMENT ON COLUMN satellite_approvals.proof IS
    'The device proves approvals (pairing_proof): no code on file, the '
    'device checks the typed code itself.';
COMMENT ON COLUMN satellite_approvals.entered_code IS
    'The code an operator typed for a proof request, held for one exchange '
    'with the device; cleared when taken.';
