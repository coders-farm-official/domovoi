-- Media catalogue (1.5.0, security review 2026-11-23, M10): a file the
-- walk didn't see is marked missing first and deleted only after a grace
-- (domovoi/media_understanding/catalog.py), so an empty mount point, an
-- unreadable folder or the walk's cap never deletes analysis, face
-- confirmations or names.
ALTER TABLE media_items ADD COLUMN IF NOT EXISTS missing_since TIMESTAMPTZ;
ALTER TABLE media_items ADD COLUMN IF NOT EXISTS missed_walks INT NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_media_items_missing ON media_items (size_bytes) WHERE missing_since IS NOT NULL;
