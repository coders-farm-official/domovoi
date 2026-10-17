-- V031 — the voice a room speaks in, kept on the server (2026-10-17).
--
-- A Pi keeps its own voice choice ("switch to Ryan" writes its
-- ~/.domovoi/voice sidecar and it reports the name in `voice_status`), and
-- the server renders that room's replies in it. A device whose hello
-- declares `supports` without "voice" (an Android phone in room mode) keeps
-- none, so "switch to Ryan" was refused there. The server now keeps that
-- room's choice instead, here, and renders the room's replies,
-- announcements and greeting clips in it.
--
--   voice   the registered voice's name (voices.name); NULL = the
--           server's own voice. Read only for a room whose device did not
--           declare "voice": a Pi's own report always wins.
--
-- Not a foreign key: a renamed or removed voice leaves the name behind and
-- the room speaks in the server's voice, as a Pi does. IF NOT EXISTS, so a
-- lane patched by hand takes it again cleanly.

ALTER TABLE satellites ADD COLUMN IF NOT EXISTS voice TEXT;
