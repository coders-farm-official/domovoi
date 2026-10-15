-- V026 — phone-mic voice samples, kept apart from the satellites' (2026-10-12).
--
-- A voice reaches a phone's mic and a satellite's far-field mic very
-- differently, so a person gets a separate set of samples for each
-- (owner decision 2026-10-12). `voice_profiles.source` says which set a
-- row is in:
--
--   'satellite'  heard by a room's satellite (every row before this
--                migration, and everything a satellite saves later)
--   'phone'      said into the app's chat mic (domovoi/phone_voice.py):
--                "my name is …" there, and "yes" to "Add this phone's
--                voice to …?" for someone already known
--
-- Satellites match only 'satellite' rows, so their centroids never move
-- because of a phone. A phone turn matches a person's 'phone' rows when
-- they have any, else their 'satellite' rows (domovoi/voice_identifier.py).
--
-- Rows a phone turn enrolled before this migration were filed under the
-- phone's own room id (`phone-<device id>`, which no satellite room can
-- take, see models.PHONE_ROOM_PREFIX). They move to the phone set here.
--
-- IF NOT EXISTS throughout, so a lane patched by hand takes it again
-- cleanly. No semicolon or double dash inside a statement: the test kits
-- split this file naively.

ALTER TABLE voice_profiles ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'satellite'
    CONSTRAINT voice_profiles_source_chk CHECK (source IN ('satellite', 'phone'));

UPDATE voice_profiles SET source = 'phone'
 WHERE source = 'satellite' AND room_id LIKE 'phone-%';

-- A phone turn reads one person's rows by set (the cap, the matching).
CREATE INDEX IF NOT EXISTS idx_voice_profiles_person_source
    ON voice_profiles (person_id, source);
