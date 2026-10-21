-- V037 — Chat lookups: a thread's internet permission, and whether it used it.
--
-- A home-server chat may look things up (assistant plan part 5, W1:
-- web/backend/api/chat.py). Before anything leaves the house the chat
-- shows a permission card: Allow once, Allow in this chat, or Not now.
--
--   internet_allowed  "Allow in this chat" was chosen (or the header chip
--                     turned it on): this thread's lookups go ahead
--                     without asking. False for every thread at first,
--                     and the chip turns it off again. The household's
--                     "Always allow internet" (LOOKUP_ALWAYS_ALLOW) and its
--                     internet answer "never" are read beside it, live.
--   used_internet     a lookup in this thread reached the internet at
--                     least once: the history list shows a globe on it.
--                     Never set by a lookup that stayed in the house (the
--                     manual, an offline library).
--
-- (V034 chat titles, V035 the help desk and V036 chat documents are in
-- flight beside this; the numbers are in order once they all land.)
-- IF NOT EXISTS throughout, so a test lane patched by hand takes it again
-- cleanly.

ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS internet_allowed BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS used_internet BOOLEAN NOT NULL DEFAULT false;
