-- V035 — documents in Chat: attachments and checked sources (2026-10-21).
--
-- Documents plan D1 ("read and ask"): a home-server chat takes a PDF,
-- Word, OpenDocument, Excel, CSV, text or markdown file. The file and the
-- text read from it live beside the chat's pictures
-- (~/.domovoi/chat_uploads/documents/<token><ext> and <token>-parts.json),
-- addressed by a server-made token, as pictures are. Two columns record
-- them in the conversation:
--
--   documents  on a user row: the documents attached with that message,
--              [{token, name, kind, pages, words, parts, scanned_pages,
--              garbled_pages, summary, notes, size}]. A thread with any is
--              answered from its documents (find, then answer). The text
--              itself is never in the conversation; the row keeps a
--              one-line summary ("lease.pdf · 30 pages · about 9,800 words").
--   doc        on an assistant row answered from documents:
--              {mode, found, sources: [{token, name, label, page, text,
--              match, cited}], notes: [{code, text}]} — the passages the app
--              gave the model (only these can be cited; the app checks), and
--              the notes under the reply ("I couldn't find that ...").
--
-- Both NULL on every other row. IF NOT EXISTS, so a lane patched by hand
-- migrates cleanly. (V033 is the printing branch's and V034 chat
-- history's, in flight at the time of writing; the numbers are in order
-- once all three land.)

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS documents JSONB;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS doc JSONB;
