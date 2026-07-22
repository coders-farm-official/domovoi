-- V004 — Durable job tracking for satellite media-prep builds (dashboard
-- "prepare media" card), modeled on model_jobs: same status vocabulary,
-- same pct/status_text progress shape, same active-target uniqueness so a
-- double-click attaches to the running build instead of racing it.
--
-- `target_kind` ∈ 'drive' | 'zip' (app-validated); `target_ref` is the
-- drive token (e.g. 'E') or 'zip'. `artifact_path` is the server-local
-- overlay zip for kind=zip (served by the download endpoint; never a
-- client-visible path semantically).

CREATE TABLE satellite_media_jobs (
    id           BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    board        TEXT         NOT NULL,
    mic_profile  TEXT         NOT NULL,
    target_kind  TEXT         NOT NULL,
    target_ref   TEXT         NOT NULL,
    offline      BOOLEAN      NOT NULL DEFAULT TRUE,
    status       TEXT         NOT NULL DEFAULT 'pending',
    phase        TEXT,
    pct          INTEGER,
    status_text  TEXT,
    error        TEXT,
    warnings     JSONB        NOT NULL DEFAULT '[]'::jsonb,
    artifact_path TEXT,
    requested_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT satellite_media_jobs_status_chk
        CHECK (status IN ('pending', 'running', 'done', 'failed', 'cancelled')),
    CONSTRAINT satellite_media_jobs_pct_chk
        CHECK (pct IS NULL OR (pct >= 0 AND pct <= 100))
);
CREATE INDEX idx_satellite_media_jobs_status
    ON satellite_media_jobs (status, requested_at DESC);
-- One live build per target; a duplicate "prepare" attaches to it.
CREATE UNIQUE INDEX idx_satellite_media_jobs_active_target
    ON satellite_media_jobs (target_ref)
    WHERE status IN ('pending', 'running');
