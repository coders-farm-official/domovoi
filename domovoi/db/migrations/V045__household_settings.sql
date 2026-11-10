-- V045 — Household-wide dashboard settings that are documents, not knobs.
--
-- The flat settings (HOME_PROBLEMS_VISIBILITY, GUEST_QUEUE_EDITING, ...)
-- live in the core's .env through the config registry
-- (domovoi/config_schema.py), which holds scalars only: int, float, bool,
-- str, choice. A setting that is a small structured document, edited as a
-- whole by an admin and read by every browser, lives here instead: one row
-- per key, its value JSON, validated by the code that writes it.
--
--   key         which setting ("nav_layout": the web sidebar's household
--               layout, web/backend/api/nav_layout.py)
--   value       the setting, as the writer validated it; a JSON object
--   updated_at  when it was last saved
--
-- No row = the built-in default. "Reset to default" deletes the row.
-- No person or device is recorded: the setting is house-wide. The size cap
-- is a backstop; the writer's own limits are far smaller.
-- (V044 wake score came before this.)
-- IF NOT EXISTS, so a test lane patched by hand takes it again cleanly.

CREATE TABLE IF NOT EXISTS household_settings (
    key         TEXT PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{0,63}$'),
    value       JSONB NOT NULL CHECK (jsonb_typeof(value) = 'object'
                                      AND octet_length(value::text) <= 32768),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
