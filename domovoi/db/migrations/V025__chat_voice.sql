-- V025 — voice turns in the chat thread (2026-10-12, the chat mic).
--
-- The Android chat screen gains a hold-to-talk mic: the phone posts a
-- recorded clip to the core (POST /v1/phone/voice, domovoi/phone_voice.py),
-- which runs it like a satellite turn (speech-to-text, voice
-- identification, the router, text-to-speech) and writes BOTH turns into
-- the thread, so voice and typed messages scroll together.
--
-- `source` (every row): 'typed' for what the chat composer sends (every row
-- before this migration), 'voice' for both rows of a spoken turn.
--
-- `speaker_person_id` / `speaker_confidence` (the user row of a voice
-- turn): who voice identification thought was speaking, and its match
-- score — the cosine against that person's enrolled voice, the number
-- domovoi/voice_identifier.py decides with. NULL when nobody was
-- recognized (the score is then the best near miss, or NULL when the clip
-- could not be embedded). The app draws a softer marker for a low score or
-- for someone other than the phone's owner. Forgetting a person keeps
-- their messages and clears the marker (ON DELETE SET NULL).
--
-- IF NOT EXISTS throughout, so a lane patched by hand takes it again
-- cleanly. No semicolon or double dash inside a statement: the test kits
-- split this file naively.

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'typed'
    CONSTRAINT chat_messages_source_chk CHECK (source IN ('typed', 'voice'));

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS speaker_person_id BIGINT
    REFERENCES people(id) ON DELETE SET NULL;

ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS speaker_confidence REAL
    CONSTRAINT chat_messages_speaker_confidence_chk CHECK (
        speaker_confidence IS NULL OR (speaker_confidence >= -1 AND speaker_confidence <= 1)
    );

-- Clearing a forgotten person's marker reads this, not the whole table.
CREATE INDEX IF NOT EXISTS idx_chat_messages_speaker
    ON chat_messages (speaker_person_id) WHERE speaker_person_id IS NOT NULL;
