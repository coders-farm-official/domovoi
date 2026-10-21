-- V034 — chat titles: who named a thread (2026-10-21).
--
-- A thread's title starts as the first five words of its first message.
-- After the first reply has finished, the server's own chat model writes a
-- short title in the background (web/backend/api/chat.py, assistant plan
-- part 7). A name a person gave the thread is never written over:
--
--   title_by_user    a person named the thread (PATCH .../threads/{id}
--                    with a title). The model's title job leaves it alone.
--   title_generated  the model has written this thread's title, so the job
--                    doesn't run for it again.
--
-- Threads from before this migration keep the title they have: they are
-- marked as titled, so no model renames the household's existing list.
-- (V033 is taken by the printing branch, in flight at the time of writing;
-- the numbers are in order once both land.)

ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS title_by_user BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS title_generated BOOLEAN NOT NULL DEFAULT false;

UPDATE chat_threads SET title_generated = true WHERE title IS NOT NULL;
