-- V032 — what an audiobook is made of, for saving it to a phone (2026-10-20).
--
-- The Android app saves a book into its own storage and plays it offline,
-- so it needs the size before it asks ("Save to phone · 412 MB") and, for a
-- per-chapter folder, the list of files to fetch one by one (each resumable
-- with Range). The indexer already reads every file's tags and duration;
-- it now keeps the rest of what it learns:
--
--   size_bytes  the whole book on disk: the file, or every chapter file in
--               the folder. NULL until the indexer has seen the book again.
--   files       the files to fetch, in play order:
--               [{file, title, start_sec, duration_sec, size_bytes}] —
--               one entry for a single-file book, one per chapter file for
--               a folder book. NULL as above (the API then derives what it
--               can from `chapters`).
--
-- `artwork` (V001, never written until now) is filled by the indexer too:
-- 'embedded' or 'image', saying only WHERE a cover was found. The picture
-- itself is still read from the files on request (cover-art decision,
-- 2026-10-02); no copy is stored. IF NOT EXISTS, so a lane patched by hand
-- takes it again cleanly.

ALTER TABLE audiobooks ADD COLUMN IF NOT EXISTS size_bytes BIGINT;
ALTER TABLE audiobooks ADD COLUMN IF NOT EXISTS files JSONB;
