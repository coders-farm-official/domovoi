-- The house-wide daily listens cap (web/backend/api/music_listens.py,
-- _recent_house_count) counts rows from phones and browsers (those with a
-- device id) in the last 24 hours. A partial index on recorded_at keeps that
-- count off a full scan of the table.
CREATE INDEX IF NOT EXISTS idx_music_listens_house_recent
    ON music_listens (recorded_at)
    WHERE device_id IS NOT NULL;
