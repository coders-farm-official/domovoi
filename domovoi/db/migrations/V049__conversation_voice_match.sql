-- V049 — How voice identification scored a satellite turn (2026-11-11).
--
-- A household member was saved under a misheard spelling of their name and
-- later recognized as somebody else, and nothing showed what the speaker
-- matcher had seen: one INFO line per turn, and only when somebody was
-- scored. Each satellite turn's conversation_log row now keeps the
-- identification outcome with the closest person and the runner-up
-- (domovoi/voice_identifier.py, VoiceMatch; written by
-- domovoi/turn_history.py onto the pending row, V043, and by the router's
-- record when there was no pending row).
--
-- voice_outcome (no CHECK, like turn_status, so a new outcome needs no
-- migration):
--   confident   named, clear enough for a room to learn from
--   match       named
--   ambiguous   two people both over the threshold, too close: nobody named
--   no_match    the closest person was under the threshold
--   too_short   the recording was too short to embed (under
--               voice_profile_min_utterance_sec)
--   no_embedding long enough, but the embedder gave nothing (not installed,
--               or it failed)
--   no_samples  nobody has a voice sample to compare with
--   timeout     identification ran out of time; the turn went on
--   denylist    the voice opted out ("never save my voice"): no scores kept
--   error       identification failed (the database, say)
-- NULL for rows from before V049 and for turns that never came through a
-- satellite (the chat mic, /v1/intent).
--
-- Scores are cosines, 0..1 in practice. The people are kept by id, so the
-- dashboard shows the CURRENT name, and ON DELETE SET NULL: forgetting a
-- person forgets that they were ever a candidate on someone else's turn.
--
-- IF NOT EXISTS, so a test lane patched by hand takes it again cleanly.

ALTER TABLE conversation_log ADD COLUMN IF NOT EXISTS voice_outcome text;
ALTER TABLE conversation_log ADD COLUMN IF NOT EXISTS voice_best_person_id
    INT REFERENCES people(id) ON DELETE SET NULL;
ALTER TABLE conversation_log ADD COLUMN IF NOT EXISTS voice_best_score real;
ALTER TABLE conversation_log ADD COLUMN IF NOT EXISTS voice_runner_up_person_id
    INT REFERENCES people(id) ON DELETE SET NULL;
ALTER TABLE conversation_log ADD COLUMN IF NOT EXISTS voice_runner_up_score real;

-- The People page's "recent voice matches" reads a person's turns by the
-- closest person, named or not; and ON DELETE SET NULL needs an index to
-- find the rows to clear without a full scan when a person is forgotten.
CREATE INDEX IF NOT EXISTS idx_conversation_log_voice_best_person
    ON conversation_log (voice_best_person_id, at DESC)
    WHERE voice_best_person_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_conversation_log_voice_runner_up_person
    ON conversation_log (voice_runner_up_person_id)
    WHERE voice_runner_up_person_id IS NOT NULL;
