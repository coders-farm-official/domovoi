-- V029 — which voice samples came from a name claim (2026-10-16).
--
-- Delta security review S1: since V026 and 2026-10-15, "my name is X" for
-- an X who exists, answered "yes", adds the speaker's clip to X's samples
-- ("Add this phone's voice to X?" on the chat mic, "I already know someone
-- called X. Is that you?" at a satellite). Nothing checks that the clip
-- sounds like X, so a stranger could then say "forget me" and delete X,
-- their profile and their memories. Owner decision 2026-10-16: keep the
-- claim (it teaches rooms the voice), but record it, so "forget me" from a
-- voice that does not match X's own removes only the samples that voice
-- added, and deleting X needs a voice that matches X or an admin.
--
--   claimed_at            when the sample was added by a name claim
--                         (NULL: the person's own voice, enrolled when the
--                         person was created or learned from it)
--   claim_room            where the yes was said: the satellite's room, or
--                         the phone's own room id (phone-<device id>)
--   claimed_by_person_id  who that voice was recognized as when it claimed
--                         the name (NULL: nobody, an unknown voice)
--   claim_of              a sample a satellite LEARNED from a voice that
--                         was recognized only through a claimed sample
--                         points at that claim, and goes with it
--
-- Rows from before this migration have no claim marks: they count as the
-- person's own voice. IF NOT EXISTS throughout, so a lane patched by hand
-- takes it again cleanly. No semicolon or double dash inside a statement:
-- the test kits split this file naively.

ALTER TABLE voice_profiles ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;

ALTER TABLE voice_profiles ADD COLUMN IF NOT EXISTS claim_room TEXT;

ALTER TABLE voice_profiles ADD COLUMN IF NOT EXISTS claimed_by_person_id INT
    REFERENCES people(id) ON DELETE SET NULL;

ALTER TABLE voice_profiles ADD COLUMN IF NOT EXISTS claim_of INT
    REFERENCES voice_profiles(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_voice_profiles_claim_of ON voice_profiles (claim_of);
