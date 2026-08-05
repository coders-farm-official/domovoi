-- V008: image generation moved out of core into the separately installed
-- Image Generation plugin (Coders Farm). The plugin owns its own jobs/
-- history table in its own schema (plugin_imagegen.generation_jobs);
-- the core table is retired. Downloaded engine/models on disk are
-- untouched — the plugin reuses the same default engine directory.

DROP TABLE IF EXISTS image_jobs;
