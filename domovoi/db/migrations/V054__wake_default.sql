-- V054 — The household's default wake word, and which rooms it applies to.
--
-- A new install's rooms wake on Domovoi's own published wake word
-- ("domovoi", slug domovoi_v1, downloaded from Coders Farm's wake-word
-- models release; domovoi/wake_default.py). An install that already had
-- rooms keeps what they wake on: nothing here, and nothing after it,
-- changes the word of a room that existed before this migration.
--
-- household_settings 'wake_default' = {"slug": ..., "set_by": ...}
--   The word a NEW room is given the first time it connects. Seeded once:
--     * the household's own default trained word, when it had made one
--       (wake_words.is_default, ready): a choice, kept;
--     * else 'hey_jarvis' when the server has ever had a room (an upgrade:
--       new rooms match the ones the house already has);
--     * else 'domovoi_v1' (a new install).
--   An admin changes it on Settings → Voice → Wake words.
--
-- room_wake_words: one row per room, saying whether the household default
-- may still be applied to it.
--   room_id  the room
--   state    'kept'     the room existed before V054, or its device can't
--                       take a pushed wake word: never touched
--            'assigned' it was given the household default on its first
--                       connection (or already woke on it)
--            'pending'  a new room whose household default could not be
--                       given yet (the model is not on this server); it is
--                       given it as soon as it is, and waits on its own
--                       boot word ("hey_jarvis") until then
--            'chosen'   an admin pushed a word to it: never touched again
--   slug     the word given or waited for (NULL for 'kept')
-- IF NOT EXISTS / ON CONFLICT, so a test lane patched by hand takes it again.

CREATE TABLE IF NOT EXISTS room_wake_words (
    room_id     TEXT PRIMARY KEY,
    state       TEXT NOT NULL CHECK (state IN ('kept', 'assigned', 'pending', 'chosen')),
    slug        TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO room_wake_words (room_id, state)
SELECT room_id, 'kept'
FROM (SELECT room_id FROM satellite_pairings
      UNION
      SELECT room_id FROM satellites) AS existing
ON CONFLICT (room_id) DO NOTHING;

INSERT INTO household_settings (key, value)
SELECT 'wake_default', jsonb_build_object(
    'slug', COALESCE(
        (SELECT slug FROM wake_words WHERE is_default AND status = 'ready' LIMIT 1),
        CASE WHEN EXISTS (SELECT 1 FROM room_wake_words) THEN 'hey_jarvis' ELSE 'domovoi_v1' END),
    'set_by', CASE WHEN EXISTS (SELECT 1 FROM room_wake_words) THEN 'upgrade' ELSE 'install' END)
ON CONFLICT (key) DO NOTHING;
