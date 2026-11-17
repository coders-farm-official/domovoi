-- V063 — Several wake words per room (1.4.0), and which word woke a turn.
--
-- A room may wake on up to 5 words, in order (domovoi/wake_default.py,
-- "Several words per room"). The first is the room's main word: the one it
-- has always had, the one an older satellite runs alone. The household
-- default stays ONE word: it is what a new room is given, and most homes
-- never add another.
--
-- room_wake_words.slugs   the room's words, main word first (1-5, no
--                         repeats), as the server last gave them. NULL for a
--                         'kept' room the server never gave a word: its
--                         device keeps whatever it had and reports it.
--                         `slug` stays, always equal to slugs[1].
-- room_wake_words.device_max
--                         how many words the room's device said it can run
--                         at once (hello `wake_words_max`), kept so the
--                         dashboard can say it while the room is offline.
--                         NULL: it never said (software from before 1.4.0),
--                         which the server treats as one word.
-- conversation_log.wake_word
--                         which of the room's words opened the mic for this
--                         turn (utterance_start `wake_word`); NULL when the
--                         satellite didn't say (older software, a follow-up,
--                         a barge-in) or the turn didn't start with a word.
--
-- Every existing room keeps its word as its first (and only) entry.
-- IF NOT EXISTS, so a test lane patched by hand takes it again.

ALTER TABLE room_wake_words ADD COLUMN IF NOT EXISTS slugs TEXT[];
ALTER TABLE room_wake_words ADD COLUMN IF NOT EXISTS device_max SMALLINT;

UPDATE room_wake_words SET slugs = ARRAY[slug]
WHERE slug IS NOT NULL AND slugs IS NULL;

ALTER TABLE room_wake_words DROP CONSTRAINT IF EXISTS room_wake_words_slugs_chk;
ALTER TABLE room_wake_words ADD CONSTRAINT room_wake_words_slugs_chk
    CHECK (slugs IS NULL OR (cardinality(slugs) BETWEEN 1 AND 5 AND slugs[1] = slug));
ALTER TABLE room_wake_words DROP CONSTRAINT IF EXISTS room_wake_words_device_max_chk;
ALTER TABLE room_wake_words ADD CONSTRAINT room_wake_words_device_max_chk
    CHECK (device_max IS NULL OR device_max BETWEEN 1 AND 5);

ALTER TABLE conversation_log ADD COLUMN IF NOT EXISTS wake_word TEXT;
