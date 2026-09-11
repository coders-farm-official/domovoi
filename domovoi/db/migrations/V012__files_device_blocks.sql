-- V012 — Per-device blocks for the Files surface.
--
-- The Files tab (and the phone's Files screen) is DAILY TIER: browsing,
-- downloading, uploading, moving and importing are open to any device on the
-- LAN, the same posture as playing music or editing a room queue. Only
-- DELETE stays behind the admin password — it's the one verb that destroys
-- something. That leaves a gap the room-queue feature already solved: a
-- household sometimes wants to take a capability away from ONE device (the
-- kids' tablet filling the music folder) without making every phone sign in
-- as admin. `files_device_blocks` is that control for files.
--
-- ── What this is NOT ───────────────────────────────────────────────────
--
-- A HOUSEHOLD POLICY control, not a security boundary — exactly like
-- `queue_device_blocks` (V010). A device id is self-asserted by the client
-- over a trusted LAN (docs/SECURITY_PRIVACY.md, daily tier), so a determined
-- user can claim a different one. It reliably keeps a known device from
-- writing into the libraries; it does not stop an attacker. The dashboard
-- says as much where the blocks are managed.
--
-- A block matches on device id OR device name (both nullable, at least one
-- required) for the same reasons as V010: the id survives a rename, the name
-- survives a reinstall. There is no per-library scope in v1 — a blocked
-- device can't write anywhere, and can still browse and download everything.
-- Reads are never blocked, so the device can see what it isn't allowed to
-- change, and the browse response says why.
CREATE TABLE files_device_blocks (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id   TEXT,
    device_name TEXT,
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- A block that names nothing would match nothing (or read as "block
    -- everyone" to a future maintainer). Refuse it at the schema.
    CONSTRAINT files_device_blocks_targets_something
        CHECK (device_id IS NOT NULL OR device_name IS NOT NULL)
);

-- One block per device id and one per device name. Partial so the NULL half
-- of a one-sided block doesn't collide (Postgres treats NULLs as distinct in
-- a plain UNIQUE, but being explicit matches the V010 shape).
CREATE UNIQUE INDEX idx_files_blocks_device_id
    ON files_device_blocks (device_id)
    WHERE device_id IS NOT NULL;
CREATE UNIQUE INDEX idx_files_blocks_device_name
    ON files_device_blocks (device_name)
    WHERE device_name IS NOT NULL;
