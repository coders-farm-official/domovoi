-- V043 — A room's conversation history records every voice turn, answered
-- or not (incident 2026-11-09: speech recognition wedged and every room
-- sat "thinking" for good; the history showed nothing, because a turn was
-- written only once it had been routed).
--
-- A satellite's turn now writes its conversation_log row as soon as the
-- transcript exists, with the reply pending, and the router fills in the
-- SAME row when the reply comes (domovoi/turn_history.py; still exactly
-- one row per routed turn). A turn that fails or times out leaves the row
-- saying so; a request speech recognition never made out gets a row of its
-- own, with no text and the recording's length.
--
--   turn_status       pending   — said, the reply is still being worked out
--                     answered  — the reply is in assistant_text
--                     no_answer — the turn ended with no reply
--                     unheard   — speech recognition failed or timed out;
--                                 user_text is ''
--                     NULL      — a row from before V043 (answered)
--   no_answer_reason  for no_answer / unheard: timed_out | error |
--                     interrupted | restarted | speech_off; NULL otherwise
--   audio_ms          the request's recording length, for a satellite's
--                     turn (NULL for typed and older rows)
--
-- Every row a pending turn leaves behind when the core stops is closed as
-- no_answer / restarted at the next start.
-- IF NOT EXISTS throughout, so a test lane patched by hand takes it again
-- cleanly.

ALTER TABLE conversation_log ADD COLUMN IF NOT EXISTS turn_status text;
ALTER TABLE conversation_log ADD COLUMN IF NOT EXISTS no_answer_reason text;
ALTER TABLE conversation_log ADD COLUMN IF NOT EXISTS audio_ms integer;

ALTER TABLE conversation_log DROP CONSTRAINT IF EXISTS conversation_log_turn_status_chk;
ALTER TABLE conversation_log ADD CONSTRAINT conversation_log_turn_status_chk
    CHECK (turn_status IS NULL
           OR turn_status IN ('pending', 'answered', 'no_answer', 'unheard'));

-- The start-up sweep and the dashboard's "still waiting" read find pending
-- rows without a scan.
CREATE INDEX IF NOT EXISTS conversation_log_pending_idx
    ON conversation_log (at) WHERE turn_status = 'pending';
