-- Compliments fixture plugin V001 — lives in schema plugin_compliments
-- (the runner presets search_path, so table names stay unqualified).
CREATE TABLE compliments_log (
    id          BIGSERIAL PRIMARY KEY,
    topic       TEXT NOT NULL,
    said_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX compliments_log_said_idx ON compliments_log (said_at);
