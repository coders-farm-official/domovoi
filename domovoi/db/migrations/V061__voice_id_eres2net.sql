-- V061 — Voice ID moves to a new speaker model (1.3.0).
--
-- Voice ID now embeds voices with 3D-Speaker's ERes2Net
-- (domovoi/clients/speaker_encoder.py, model tag 'eres2net-vox-v1')
-- instead of Resemblyzer ('resemblyzer-v1' before 1.3.0, 'resemblyzer-v2'
-- in its first builds). Embeddings from two models can't be compared, and
-- Domovoi keeps embeddings only, never the recordings, so nothing can be
-- re-embedded: every older sample is unusable. Owner decision 2026-11-14:
-- a hard cut, everyone says "my name is …" once again.
--
-- people.voice_relearn_since
--                when this person's voice samples were dropped because the
--                speaker model changed (NULL: never). With no sample of the
--                current model, the People page asks them to say "my name
--                is …" in a room to be recognized again
--                (web/backend/api/people.py ``voice_relearn``).
--
-- voice_profiles / voice_denylist
--                every row of another model is deleted: they could never be
--                used again (the code reads only the current tag anyway),
--                and a voice embedding is personal data with no reason left
--                to keep it. Learned samples that rest on a claimed one
--                (V029's claim_of cascade) go with it; all of them are of the
--                old model too. An opt-out ("never save my voice") from
--                before is one of them: that voice is unknown again, and the
--                release note asks whoever said it to say it again.
--
-- Idempotent: run twice, the second run marks and deletes nothing.

ALTER TABLE people ADD COLUMN IF NOT EXISTS voice_relearn_since TIMESTAMPTZ;

UPDATE people p
   SET voice_relearn_since = NOW()
 WHERE p.voice_relearn_since IS NULL
   AND EXISTS (SELECT 1 FROM voice_profiles v
                WHERE v.person_id = p.id AND v.model <> 'eres2net-vox-v1');

DELETE FROM voice_profiles WHERE model <> 'eres2net-vox-v1';

DELETE FROM voice_denylist WHERE model <> 'eres2net-vox-v1';
