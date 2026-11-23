-- V076: mark the voice samples a satellite learned on its own (1.5.0).
--
-- The identifier saves a sample in a room after a run of confident matches
-- (domovoi/voice_identifier.py, job 4). Such a sample is the mic's: it
-- carries that room's distance and echo. When an admin moves a satellite to
-- another room its learned samples are dropped and enrolled ones kept
-- (owner answer Q7). Rows from before this migration can't be told apart and
-- count as enrolled, so nothing is dropped that may have been said on
-- purpose.

ALTER TABLE voice_profiles ADD COLUMN learned BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN voice_profiles.learned IS
    'Saved by the identifier after confident matches (not enrolled on '
    'purpose). Dropped when its satellite (satellite_id) moves to another room.';
