-- Synthetic-voices-only wake words, and what each trained model was made from.
--
-- synthetic_only: the word trains on the trainer's synthetic voices alone.
-- None of the household's recordings reach the trainer (the worker hands it
-- an empty clips folder and tells the wrapper to mount none), and the
-- minimum-clips gate does not apply. Fixed when the word is created, so a
-- model of one kind is never replaced by one of the other kind under the
-- same name: a synthetic-only model of a recorded word's phrase is its own
-- word (its own slug, so its own model file), and the two can be compared
-- side by side.
--
-- trained_clips: how many of the household's recordings the word's CURRENT
-- model was trained on (0 = synthetic voices only). NULL = no model yet, or
-- a model trained before this column existed (the dashboard then falls back
-- to the clips folder's training marker, or says it can't tell).
ALTER TABLE wake_words ADD COLUMN synthetic_only BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE wake_words ADD COLUMN trained_clips INT;
ALTER TABLE wake_words ADD CONSTRAINT wake_words_trained_clips_chk
    CHECK (trained_clips IS NULL OR trained_clips >= 0);
