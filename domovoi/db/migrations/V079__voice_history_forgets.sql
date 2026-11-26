-- V079 — voice history: the forgets plugins still have to hear about
-- (1.5.0, SDK 1.17, security review M8).
--
-- Number note: V079, the next free number on next/1.5.0 (2026-11-23).
-- Renumber if the 1.5.0 numbers shift.
--
-- A plugin that keeps something tied to a person (a lookup history, say)
-- must delete it when that person's voice history is deleted. The core
-- tells plugins with the event core.voice_history_deleted
-- {person_id} (domovoi/voice_history.py). The event bus is in-process
-- and keeps nothing, and a person can be forgotten from the dashboard,
-- which is the WEB process, while the core is down. So each deletion
-- writes one row here IN ITS OWN TRANSACTION (forget me, People -> delete
-- person, a delete-all job's finish), and the core announces the rows and
-- deletes them: at once after the commit when it can, otherwise at its
-- next start or its next hourly voice-history tick. A forget is never
-- lost, and a deletion that rolls back announces nothing.
--
--   person_id   the person whose turns were deleted (no FK: the person is
--               gone), NULL for "Delete all voice history".
--   created_at  when the deletion committed.
--
-- Nothing else: no name, no text. A row lives until the core announces it.
--
-- IF NOT EXISTS, so a lane patched by hand takes it again cleanly. No
-- semicolon or double dash inside a statement: the test kits split this
-- file naively.

CREATE TABLE IF NOT EXISTS voice_history_forgets (
    id          BIGSERIAL   PRIMARY KEY,
    person_id   INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
