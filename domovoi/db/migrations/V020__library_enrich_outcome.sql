-- V020: what the library enricher concluded for each track (fix B3).
--
-- Before this, the enricher stamped `enriched_at` after EVERY attempt,
-- whether or not a provider answered: with no AcoustID key and no Shazam
-- add-on it marked a whole library "done" without sending a request. From
-- V020 on a stamp records its outcome, and only a provider's answer stamps:
--   'matched'      a provider identified the track
--   'no_match'     a provider answered and nothing scored
--   'missing_file' the file is gone
--   'manual'       a person edited the tags in the dashboard
--   'recheck'      requeued by the enricher's one-off recovery
-- NULL = never judged by the outcome-aware enricher (a legacy stamp, or not
-- tried yet). No CHECK constraint: it can't be added idempotently by hand.
--
-- Every legacy stamp that carries a MusicBrainz recording id was a real
-- match (an AcoustID match always wrote one), so it is marked 'matched' here
-- and the recovery never touches it. The enricher requeues the remaining
-- legacy stamps once, when a provider exists, and fills only empty fields.
--
-- Idempotent: safe to apply by hand to a test lane that already has it.

ALTER TABLE library_tracks ADD COLUMN IF NOT EXISTS enrich_outcome TEXT;

UPDATE library_tracks SET enrich_outcome = 'matched'
 WHERE enrich_outcome IS NULL AND enriched_at IS NOT NULL
   AND musicbrainz_recording_id IS NOT NULL;
