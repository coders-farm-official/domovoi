-- V083: a satellite's support key is pinned when its pairing is APPROVED
-- (1.5.0, docs/SECURITY_PRIVACY.md, "Support sessions").
--
-- V082 pinned satellites.support_key when an admin turned "Allow support
-- sessions" on, from whatever key the connected satellite presented at that
-- moment: trust on first use. Now the key rides the pairing request the
-- admin approves. A device that proves its approval (satellite/pairing_proof.py,
-- version 3) binds the key into the exchange's transcript, and the core pins
-- that key when the exchange approves it. A request approved by its code keeps
-- the key its parked hello presented here, and approving it pins that key.
--
-- satellites.support_key is from now on written at approval (a re-pair
-- re-pins it, or clears it for a device that presents none), and the switch
-- pins it only for a satellite that has none pinned (one paired before this),
-- as before. Turning the switch off no longer clears it.
--
-- Base64 of the 32-byte public key; NULL = none presented (or a proof request,
-- whose key comes from the run that approves it).

ALTER TABLE satellite_approvals ADD COLUMN IF NOT EXISTS support_key TEXT;
