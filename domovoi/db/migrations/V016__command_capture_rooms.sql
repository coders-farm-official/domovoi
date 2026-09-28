-- V016 — Which rooms keep their command recordings for tuning.
--
-- The early-endpointing work (design notes 2026-09-28) needs real
-- household audio to judge the two options that decide when a person has
-- finished speaking from the sound itself (an end-of-turn model, a
-- streaming recognizer for short commands): how often each would have cut
-- somebody off. Owner decision the same day: collect it OPT-IN PER ROOM.
-- A satellite's command captures are saved on the Domovoi server only
-- for a room listed here, and every one is deleted after 14 days.
--
-- A row IS the opt-in. No row, no recording: every room starts off,
-- including every room created after this migration, and there is no
-- column to forget to set. Opting a room out deletes its row and, in the
-- same request, every recording already kept for it. Only an admin can
-- write here (the dashboard's security tier: Bearer-only, and refused
-- outright until first-run setup is complete), and the core records
-- nothing while no admin credential exists at all, so `--reset-admin`
-- pauses every room until someone claims the server again.
--
-- The recordings themselves are files, not rows: a WAV and a small JSON
-- sidecar per command under COMMAND_CAPTURES_DIR (~/.domovoi/captures by
-- default), which no web file route serves. See domovoi/command_captures.py
-- and docs/SECURITY_PRIVACY.md.
--
-- `room_id` is the satellite's identity, as everywhere else. Not a foreign
-- key: a room that predates the `satellites` inventory (V003) has only an
-- mpd_rooms row, and retiring a room deletes this row in app code
-- alongside the others.
--
-- IF NOT EXISTS so a database patched by hand ahead of Flyway (a test
-- lane) still takes this migration cleanly.
CREATE TABLE IF NOT EXISTS command_capture_rooms (
    room_id    TEXT PRIMARY KEY,
    enabled_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
