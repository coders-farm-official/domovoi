-- What opened the mic for a turn: 'wake_word', 'barge_in', 'followup',
-- 'push_to_talk' — NULL for rows that predate this column and for
-- non-satellite paths (/v1/intent has no mic).
--
-- The satellite has always sent this on `utterance_start`, and the core has
-- always latched it to decide clip-vs-command and then thrown it away. That
-- left a turn's ORIGIN unrecoverable after the fact, which is the first
-- thing worth knowing about a turn that looks wrong: in the 2026-09-10
-- office log the satellite barged in on its own TTS and answered itself,
-- and telling that apart from a genuine wake word meant inferring it from
-- timestamps.
--
-- Named `utterance_trigger`, not `trigger`: TRIGGER is a SQL keyword, and a
-- column that needs quoting in every statement is a column someone will
-- eventually forget to quote. It also matches `_utterance_trigger`, the
-- name the streaming layer already uses for the same value.
--
-- No CHECK constraint — matched_path learned that lesson in V001. A new
-- trigger kind should not need a migration.
ALTER TABLE conversation_log ADD COLUMN IF NOT EXISTS utterance_trigger text;

-- Partial index: the question worth asking is "show me the barge-triggered
-- turns in this room", and those are a small minority of rows.
CREATE INDEX IF NOT EXISTS conversation_log_utterance_trigger_idx
    ON conversation_log (room_id, at DESC)
    WHERE utterance_trigger IS NOT NULL;
