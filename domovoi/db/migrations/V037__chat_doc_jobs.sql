-- V037 — long documents in Chat: map/reduce jobs (2026-10-21).
--
-- Documents plan D2 ("long documents"): a summary of a document longer
-- than one pass is read in parts (map), then the notes are combined
-- (reduce), by the web process (web/backend/api/chat_doc_jobs.py). A
-- home-server chat's job is kept here, one row per job, so that:
--
--   * each part's notes are saved as it finishes, and a job the web
--     process lost (a restart) carries on from the next part when the
--     chat is opened again;
--   * a stopped job keeps what it read ("Summary of pages 1-9 (stopped)")
--     and can carry on later.
--
-- Columns:
--   id            32 hex characters, made by the server.
--   thread_id     the chat; the job goes with it (CASCADE).
--   user_message_id  the message that asked for the summary.
--   question      what the person asked ("summarize the lease").
--   state         running | stopped | failed. A finished job's row is
--                 deleted: its summary is the assistant message. A row left
--                 running by a restart reads as interrupted until the
--                 person carries it on.
--   chunks        the plan: [{doc, units: [[part, start, end]], chars}]
--                 over `documents` (the thread's tokens, in order).
--   documents     the document tokens the chunks index, in order.
--   notes         one entry per chunk: the checked notes, or null for a
--                 part not read yet.
--   estimate_s    the time estimated when it started (measured speeds),
--                 null when nothing was measured yet.
--   stats         the job's own counts: prompt/output tokens, durations,
--                 characters sent (they make the next estimate).
--   model         the model the person's message named (null: the QA
--                 model); carrying on keeps it.
--
-- A phone's private job ("Use home server" from a phone chat, owner
-- decision Q6) is never written here: it lives in the web process's
-- memory and a folder deleted when it ends.

CREATE TABLE IF NOT EXISTS chat_doc_jobs (
    id              TEXT        PRIMARY KEY,
    thread_id       BIGINT      NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    user_message_id BIGINT      REFERENCES chat_messages(id) ON DELETE SET NULL,
    question        TEXT        NOT NULL DEFAULT '',
    state           TEXT        NOT NULL DEFAULT 'running',
    documents       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    chunks          JSONB       NOT NULL DEFAULT '[]'::jsonb,
    notes           JSONB       NOT NULL DEFAULT '[]'::jsonb,
    estimate_s      REAL,
    stats           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    model           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_doc_jobs_thread ON chat_doc_jobs (thread_id);
