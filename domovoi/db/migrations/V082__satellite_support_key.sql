-- V082: the support key pinned for a satellite (1.5.0, docs/SECURITY_PRIVACY.md,
-- "Support sessions").
--
-- A satellite that offers support sessions has its own Ed25519 support key
-- (~/.domovoi/support_key on the device) and sends the public half in its
-- hello. When an admin turns "Allow support sessions" on, the core pins the
-- key the connected satellite presents here; every frame the satellite sends
-- about a session must carry a signature by that key, and a satellite that
-- presents any other key is not offered sessions. Turning the switch off
-- clears it. Base64 of the 32-byte public key; NULL = none pinned.

ALTER TABLE satellites ADD COLUMN IF NOT EXISTS support_key TEXT;
