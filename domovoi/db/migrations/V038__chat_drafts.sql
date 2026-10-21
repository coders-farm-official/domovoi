-- V038 — drafts in Chat: write, revise and save (2026-10-21).
--
-- Documents plan D4 ("write and save"): a home-server chat can write a
-- resume, a cover letter or a letter. The draft is markdown in a fixed
-- skeleton (the model writes content, never layout); our templates turn a
-- version into .docx or PDF only when someone saves it, into Documents →
-- "From Chat". Every version is kept here, with what produced it.
--
--   chat_drafts           one per document a chat is writing: its kind
--                         (resume | cover_letter | letter), template and
--                         title. Deleted with its thread.
--   chat_draft_versions   every version, numbered from 1: the markdown, the
--                         instruction that produced it ("shorter"; NULL for
--                         a first draft or a hand edit), who wrote it
--                         (author 'model' | 'person': "edited by you"), the
--                         model, and the files saved from it
--                         (saved: [{format, rel_path, check, at}]).
--   chat_messages.draft   on the assistant row that made a version (or
--                         offers to print one): {id, version, kind, title,
--                         template, words, action}. NULL on every other row.
--
-- The plan called the tables chat_documents / chat_document_versions; D1
-- uses "documents" for attachments (chat_messages.documents), so these are
-- named for drafts. IF NOT EXISTS, so a lane patched by hand migrates
-- cleanly. (V037 is taken twice by branches in flight at the time of
-- writing, wf/documents-d2 and wf/lookups; this is the next number.)

CREATE TABLE IF NOT EXISTS chat_drafts (
    id          BIGSERIAL PRIMARY KEY,
    thread_id   BIGINT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    template    TEXT NOT NULL,
    title       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_drafts_thread_idx ON chat_drafts (thread_id, id);

CREATE TABLE IF NOT EXISTS chat_draft_versions (
    id           BIGSERIAL PRIMARY KEY,
    draft_id     BIGINT NOT NULL REFERENCES chat_drafts(id) ON DELETE CASCADE,
    version      INTEGER NOT NULL,
    body         TEXT NOT NULL,
    instruction  TEXT,
    author       TEXT NOT NULL DEFAULT 'model',
    model        TEXT,
    saved        JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (draft_id, version)
);

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS draft JSONB;
