-- V042 — co-editing for Documents (and later Sheets): the shared editing
-- state and the version history (editors v1, builder B, 2026-10-26).
--
-- Several household devices can edit one document at once. Each browser or
-- app runs Yjs; the web process runs pycrdt and keeps the shared state here.
-- The FILE in Documents is always the clean latest version (.html, .md,
-- .docx, .xlsx): nothing below is ever written into it, and nothing in it
-- depends on these rows. Delete every row and each document simply starts
-- again from its file (web/backend/collab/ explains the save path).
--
--   collab_docs       one row per document that has been opened for
--                     co-editing: the stable id the editor's WebSocket uses
--                     (/api/collab/{doc_id}), the Documents-relative path it
--                     stands for, the EPOCH (bumped whenever the shared
--                     state is rebuilt from the file because the file was
--                     changed outside the editor, so an offline phone holding
--                     the old state can tell), the compacted state, and the
--                     fingerprint of the file that state matches.
--   collab_updates    the edits since the last compaction, appended about
--                     once a second while people type and folded into
--                     collab_docs.state every couple of hundred rows.
--   collab_snapshots  version history: the whole state every few minutes
--                     while someone edits, when the last editor leaves, and
--                     before anything replaces it (a restore, a rebuild from
--                     a changed file). Who edited is kept by NAME (the
--                     household person or the device), as the history list
--                     shows it. Thinned out with age (web/backend/collab).
--
-- ON DELETE CASCADE from collab_docs: deleting a document in Documents
-- deletes its row, and with it every update and every saved version.

CREATE TABLE IF NOT EXISTS collab_docs (
    doc_id         TEXT PRIMARY KEY,
    rel_path       TEXT NOT NULL UNIQUE,
    epoch          INTEGER NOT NULL DEFAULT 1,
    state          BYTEA,
    seeded         BOOLEAN NOT NULL DEFAULT FALSE,
    file_sha256    TEXT,
    file_size      BIGINT,
    file_mtime_ns  BIGINT,
    -- How many edits the state has taken (rev), and the last one a clean
    -- file was saved from (saved_rev). rev > saved_rev: the file is behind,
    -- and the next editor to connect is asked to save it.
    rev            BIGINT NOT NULL DEFAULT 0,
    saved_rev      BIGINT NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS collab_updates (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES collab_docs(doc_id) ON DELETE CASCADE,
    epoch       INTEGER NOT NULL,
    data        BYTEA NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_collab_updates_doc ON collab_updates (doc_id, id);

CREATE TABLE IF NOT EXISTS collab_snapshots (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES collab_docs(doc_id) ON DELETE CASCADE,
    epoch       INTEGER NOT NULL,
    -- from_file | auto | leave | before_rebuild | offline_copy | before_restore | restored
    kind        TEXT NOT NULL,
    state       BYTEA NOT NULL,
    editors     JSONB NOT NULL DEFAULT '[]'::jsonb,
    size        INTEGER NOT NULL,
    -- offline_copy only: the device it came from (one copy per device per
    -- epoch; a reconnect replaces it). NULL for every other kind.
    origin      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_collab_snapshots_doc ON collab_snapshots (doc_id, created_at DESC);
