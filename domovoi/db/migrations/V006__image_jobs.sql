-- ─── Image jobs (Images tab: local generation + model downloads) ─────────
-- One durable background-job table for both long-running image operations,
-- mirroring model_jobs (progress over LISTEN/NOTIFY → the web WebSocket):
--   kind = 'generate' — one ComfyUI generation; `prompt`/`params` hold the
--          request, `result` the produced pictures-library rel paths.
--   kind = 'pull'     — a curated image-model download into the ComfyUI
--          models dir; `model` is the catalog id, pct tracks bytes.
-- Both run in the WEB process (models.py pull-task pattern) so the voice
-- pipeline is never tied up by a multi-GB transfer or a render.

CREATE TABLE image_jobs (
    id           BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind         TEXT         NOT NULL,
    model        TEXT         NOT NULL,
    prompt       TEXT,
    params       JSONB,
    status       TEXT         NOT NULL DEFAULT 'pending',
    pct          INTEGER,
    status_text  TEXT,
    error        TEXT,
    result       JSONB,
    requested_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT image_jobs_kind_chk
        CHECK (kind IN ('generate', 'pull')),
    CONSTRAINT image_jobs_status_chk
        CHECK (status IN ('pending', 'running', 'done', 'failed', 'cancelled')),
    CONSTRAINT image_jobs_pct_chk
        CHECK (pct IS NULL OR (pct >= 0 AND pct <= 100))
);
CREATE INDEX idx_image_jobs_status ON image_jobs (status, requested_at DESC);
-- One live download per catalog model; a duplicate "install X" attaches to
-- the running job (model_jobs pattern). Generations are never deduped.
CREATE UNIQUE INDEX idx_image_jobs_active_pull
    ON image_jobs (model)
    WHERE kind = 'pull' AND status IN ('pending', 'running');
