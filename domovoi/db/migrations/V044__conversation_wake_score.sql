-- V044 — The wake word's score on a satellite turn (false wakes,
-- 2026-11-09: rooms woke on a TV or a phone saying the stock wake word, and
-- nobody could see what those wakes scored, so the threshold could not be
-- tuned with data).
--
-- A satellite from 2026-11-09 sends the wake's peak openWakeWord score
-- (0..1) in its utterance_start (`wake_score`, satellite/wake_gate.py); the
-- core keeps it on the turn's conversation_log row (domovoi/turn_history.py).
-- NULL for every turn not opened by a wake word, for older satellites and
-- for rows from before V044.
--
-- Also new on 2026-11-09, with no DDL: no_answer_reason 'overheard' — a
-- long capture that sounded like background talk and was deliberately left
-- unanswered (domovoi/overheard.py). The column has no CHECK (V043), so the
-- value needs no migration; it is listed here so the reasons are in one
-- place: timed_out | error | interrupted | restarted | speech_off | overheard.
--
-- IF NOT EXISTS, so a test lane patched by hand takes it again cleanly.

ALTER TABLE conversation_log ADD COLUMN IF NOT EXISTS wake_score real;
