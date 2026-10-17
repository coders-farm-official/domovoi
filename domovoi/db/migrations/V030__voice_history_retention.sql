-- V030 — voice history: how long it is kept, and deleting it (2026-10-17).
--
-- Until now what people say to Domovoi (intents_log, conversation_log, a
-- room's short-term conversation in sessions.context) was kept with no
-- limit, and forgetting a person only took their name off it. Owner,
-- 2026-10-17: a "Keep voice history" setting (30 days, 90 days by default,
-- 1 year, forever) applied by a daily worker, a "Delete all voice history"
-- for the admin, and "forget me" / forget person deleting that person's
-- own spoken turns. See domovoi/voice_history.py.
--
-- 1. sessions(last_activity). The worker deletes sessions idle past the
--    setting, in batches. The only index on the table leads with room_id,
--    so "idle since" was a whole-table scan per batch. intents_log and
--    conversation_log already have their `at` indexes (V001), and their
--    person_id ones (partial) serve forgetting a person.
--
-- 2. voice_history_jobs: one row per run — a "Delete all voice history"
--    (kind delete_all) and each daily prune (kind retention). A delete-all
--    is written here BEFORE it starts, so one the core was restarted in
--    the middle of finishes at the next boot instead of half-done, and the
--    dashboard reads its progress from here. `cutoff`: everything said
--    before it goes. `deleted`: counts per store, never any text.
--    The worker keeps the newest rows only.
--
-- IF NOT EXISTS throughout, so a lane patched by hand takes it again
-- cleanly. No semicolon or double dash inside a statement: the test kits
-- split this file naively.

CREATE INDEX IF NOT EXISTS idx_sessions_last_activity ON sessions (last_activity);

CREATE TABLE IF NOT EXISTS voice_history_jobs (
    id            BIGSERIAL   PRIMARY KEY,
    kind          TEXT        NOT NULL,
    cutoff        TIMESTAMPTZ NOT NULL,
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    deleted       JSONB,
    error         TEXT,
    CONSTRAINT voice_history_jobs_kind_chk CHECK (kind IN ('delete_all', 'retention'))
);

CREATE INDEX IF NOT EXISTS idx_voice_history_jobs_open
    ON voice_history_jobs (id) WHERE finished_at IS NULL;
