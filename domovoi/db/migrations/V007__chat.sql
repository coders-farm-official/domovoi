-- ─── Text chat (web + Android chat surface) ──────────────────────────────
-- Claude-desktop-style threaded chat, backed directly by Ollama
-- (clients/ollama.chat_stream) — deliberately NOT the voice chat-mode path
-- (Letta), which stays a per-room session concept. The schema leaves a
-- bridge open: `letta_agent_id` on a thread means "a stateful agent backs
-- this thread"; NULL means plain stateless-completion threads (v1).

CREATE TABLE chat_threads (
    id             BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title          TEXT,
    person_id      BIGINT      REFERENCES people(id) ON DELETE SET NULL,
    letta_agent_id TEXT,
    archived       BOOLEAN     NOT NULL DEFAULT false,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_chat_threads_updated ON chat_threads (archived, updated_at DESC);

-- `images` holds upload tokens ([{token, name}]) resolved against the
-- chat-uploads store (~/.domovoi/chat_uploads); `model` records which
-- Ollama model produced an assistant row (vision switches per message).
CREATE TABLE chat_messages (
    id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id  BIGINT      NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    role       TEXT        NOT NULL,
    content    TEXT        NOT NULL,
    images     JSONB,
    model      TEXT,
    error      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chat_messages_role_chk CHECK (role IN ('user', 'assistant'))
);
CREATE INDEX idx_chat_messages_thread ON chat_messages (thread_id, id);
