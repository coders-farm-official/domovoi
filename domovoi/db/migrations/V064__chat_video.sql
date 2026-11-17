-- V064 — a video attached in chat, and what was understood from it (2026-11-16).
--
-- Video in chat (1.4.0, owner decisions 2026-11-16): a household member
-- attaches a video of at most 3 minutes to a home-server chat. The core
-- watches it in the background (domovoi/chat_video/runner.py), one job at
-- a time, holding off while any voice turn is under way, and keeps only
-- text: the transcript with times, a description of each of a few frames
-- (worded "might show" wherever it is shown), and a short summary. The
-- video file and every frame grab are deleted when the job ends, however
-- it ends. The text goes with the chat (CASCADE from chat_threads).
--
-- chat_video_jobs, one row per attached video:
--   id            32 hex characters, made by the web process.
--   thread_id     the chat; the job and its results go with it.
--   message_id    the person's message the video was sent with.
--   token         the staged file's token (~/.domovoi/chat_uploads/videos/
--                 <token><ext>), gone once the job ends.
--   name          the file's name as sent ("birthday.mp4").
--   question      words sent with the video, answered when it is watched.
--   duration_s, size_bytes, probe   what ffprobe said at the upload.
--   state         queued | running | done | failed | cancelled.
--   stage         the pass under way: scan | transcribe | frames | combine.
--   plan          pass 0's result, saved before pass 1: scene changes,
--                 the speech pieces, and (once picked) the frame times, so
--                 a restart carries on with the same plan.
--   progress      {pieces_done, frames_done, ...} as the job goes.
--   waiting_for_voice  true while it holds off for a voice turn.
--   estimate_s    the first estimate; eta_s the time left as last measured.
--   summary       the combine step's text.
--   notes         plain notes for the reply ("no speech heard", ...).
--   error, error_code   a failed job's plain reason and help-desk code.
--   cancel_requested    the person pressed stop; the core stops at once.
--   attempts      times the core started it (a restart counts); past three
--                 it fails cleanly instead of trying again.
--   stats         measured seconds per step (the next job's estimate).
--   files_deleted_at    when the video and the frames were deleted.
--
-- chat_video_segments: one row per transcribed piece of speech, saved as
-- each piece finishes. chat_video_frames: one row per described frame,
-- saved as each description finishes.

CREATE TABLE IF NOT EXISTS chat_video_jobs (
    id                 TEXT        PRIMARY KEY,
    thread_id          BIGINT      NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    message_id         BIGINT      REFERENCES chat_messages(id) ON DELETE SET NULL,
    token              TEXT        NOT NULL,
    name               TEXT        NOT NULL,
    question           TEXT        NOT NULL DEFAULT '',
    duration_s         REAL        NOT NULL,
    size_bytes         BIGINT,
    probe              JSONB       NOT NULL DEFAULT '{}'::jsonb,
    state              TEXT        NOT NULL DEFAULT 'queued'
                       CHECK (state IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    stage              TEXT,
    plan               JSONB,
    progress           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    waiting_for_voice  BOOLEAN     NOT NULL DEFAULT false,
    estimate_s         REAL,
    eta_s              REAL,
    summary            TEXT,
    notes              JSONB       NOT NULL DEFAULT '[]'::jsonb,
    error              TEXT,
    error_code         TEXT,
    cancel_requested   BOOLEAN     NOT NULL DEFAULT false,
    attempts           INTEGER     NOT NULL DEFAULT 0,
    stats              JSONB       NOT NULL DEFAULT '{}'::jsonb,
    files_deleted_at   TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at         TIMESTAMPTZ,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_chat_video_jobs_thread ON chat_video_jobs (thread_id);
-- The core's queue: the oldest live job first.
CREATE INDEX IF NOT EXISTS idx_chat_video_jobs_live ON chat_video_jobs (created_at)
    WHERE state IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS chat_video_segments (
    job_id   TEXT    NOT NULL REFERENCES chat_video_jobs(id) ON DELETE CASCADE,
    idx      INTEGER NOT NULL,
    start_s  REAL    NOT NULL,
    end_s    REAL    NOT NULL,
    text     TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (job_id, idx)
);

CREATE TABLE IF NOT EXISTS chat_video_frames (
    job_id       TEXT    NOT NULL REFERENCES chat_video_jobs(id) ON DELETE CASCADE,
    idx          INTEGER NOT NULL,
    at_s         REAL    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (job_id, idx)
);
