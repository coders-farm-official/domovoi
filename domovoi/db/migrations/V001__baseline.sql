-- V001 — Domovoi core baseline (fresh squash).
--
-- This is the ONLY core migration at 1.0.0: the complete core schema in
-- its final state, minus everything that belongs to plugins, plus
-- the new plugin-platform tables. FROZEN once cut — every later core schema
-- change is a new V###__*.sql. Plugins NEVER migrate this schema: each
-- plugin owns its own `plugin_<slug>` Postgres schema with its own ledger,
-- applied by the core migration runner (never Flyway).
--
-- Deliberate design choices:
--
--   * Provider tables are GONE. The provider download queue is replaced by
--     the generic `media_acquisitions` queue (see below); station/detection/
--     fingerprint tables ship as the radio plugin's own migrations in its
--     own schema.
--   * OPEN ENUMS: `intents_log.matched_path`, `conversation_log.matched_path`,
--     `library_tracks.source`, `media_plays.source`, and `voices.engine`
--     carry NO CHECK constraint. Validation happens in app code against
--     in-process registries; the informational `registered_values` table
--     mirrors the live vocabulary for DBAs/analytics. This avoids
--     drop-and-recreate CHECK churn and lets plugins add sources without
--     core DDL.
--   * CLOSED ENUMS STAY CHECKED where the vocabulary is truly core-owned:
--     presence tiers, connectivity states, call/job statuses, engine locks,
--     added_via, categories. Closed enums remain a correctness feature.
--   * New tables: `plugins` (registry), `registered_values`,
--     `media_acquisitions`, `admin_auth`, `admin_sessions`.
--
-- Extensions are core-only privilege: plugins may not CREATE EXTENSION
-- (enforced by the install-time SQL lint). pg_trgm ships here because the
-- core fuzzy library dedup depends on it.

CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- ─── People / voice identity ─────────────────────────────────────────────

-- Known speakers. Multiple people can share a name — the voice embedding
-- (voice_profiles) is the actual identifier; `name` is the user-facing label.
-- `preferences` is a SHALLOW one-value-per-person JSONB (tts_voice, timezone,
-- do_not_remember, favorite_quick); list-shaped data goes in `favorites`,
-- free-form facts in `memories`. `last_extracted_at` is the implicit-memory
-- extractor's high-water mark.
CREATE TABLE people (
    id                SERIAL PRIMARY KEY,
    name              TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at      TIMESTAMPTZ,
    notes             TEXT,
    preferences       JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_extracted_at TIMESTAMPTZ
);
CREATE INDEX idx_people_last_seen_at ON people (last_seen_at);

-- Voice embedding samples. A person can have multiple samples (re-enrollments,
-- different rooms/mics). `model` lets us re-enroll cleanly if we swap models.
CREATE TABLE voice_profiles (
    id              SERIAL PRIMARY KEY,
    person_id       INT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    embedding       BYTEA NOT NULL,                 -- serialized float32 vector
    model           TEXT NOT NULL,                  -- e.g. 'resemblyzer-v1'
    enrolled_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    room_id         TEXT,
    sample_seconds  REAL
);
CREATE INDEX idx_voice_profiles_person_id ON voice_profiles (person_id);
CREATE INDEX idx_voice_profiles_model ON voice_profiles (model);

-- Persistent voice opt-out. The embedding is stored but tied to nothing —
-- no person_id, no last_seen tracking, no name. The identifier consults the
-- denylist on every match attempt AND the enrollment paths consult it before
-- saving a new profile, so an opted-out voice is neither identified nor
-- (re-)enrolled. Deliberately NOT a relation to `people`: a denylisted user
-- has explicitly refused to be a `people` row.
CREATE TABLE voice_denylist (
    id              SERIAL PRIMARY KEY,
    embedding       BYTEA NOT NULL,                 -- serialized float32 vector
    model           TEXT NOT NULL,                  -- match against voice_profiles.model
    denylisted_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes           TEXT
);
CREATE INDEX idx_voice_denylist_model ON voice_denylist (model);


-- ─── Timers / reminders ──────────────────────────────────────────────────

-- A reminder is a timer with `message IS NOT NULL`; the watcher speaks the
-- message on fire.
CREATE TABLE timers (
    id          SERIAL PRIMARY KEY,
    label       TEXT,
    message     TEXT,                          -- null = plain timer, non-null = reminder
    expires_at  TIMESTAMPTZ NOT NULL,
    room_id     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_timers_expires_at ON timers (expires_at);


-- ─── Music library ───────────────────────────────────────────────────────

-- Library metadata + provenance + MusicBrainz enrichment + favorited flag.
-- `source` is an OPEN enum (registered_values domain 'library_source'):
-- core seeds 'manual' / 'indexed' / 'upload'; media-provider plugins register
-- their own slug and stamp it on tracks they acquire.
CREATE TABLE library_tracks (
    id                       SERIAL PRIMARY KEY,
    file_path                TEXT UNIQUE NOT NULL,
    title                    TEXT,
    artist                   TEXT,
    album                    TEXT,
    duration_sec             INT,
    source                   TEXT,
    source_id                TEXT,
    added_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    added_via                TEXT,
    musicbrainz_recording_id TEXT,
    musicbrainz_artist_id    TEXT,
    musicbrainz_release_id   TEXT,
    mb_lookup_at             TIMESTAMPTZ,
    enriched_at              TIMESTAMPTZ,
    favorited                BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT library_tracks_added_via_chk
        CHECK (added_via IS NULL OR added_via IN ('voice', 'manual'))
);
CREATE INDEX idx_library_tracks_source ON library_tracks (source, source_id);
-- Partial — only successful MusicBrainz matches carry an id.
CREATE INDEX idx_library_tracks_mb_recording_id
    ON library_tracks (musicbrainz_recording_id)
    WHERE musicbrainz_recording_id IS NOT NULL;
-- Enricher's "find unenriched" sweep; empties out once the library is done.
CREATE INDEX idx_library_tracks_enriched_at
    ON library_tracks (enriched_at)
    WHERE enriched_at IS NULL;
-- Favoriting is rare; the "show me my favorites" plan reads exactly these rows.
CREATE INDEX idx_library_tracks_favorited
    ON library_tracks (favorited)
    WHERE favorited;
-- Trigram index for fuzzy dedup (`find_fuzzy_library_match`): real
-- similarity scores instead of LIKE substring matching, immune to the
-- LIKE-wildcard injection class of bug (underscores in titles).
CREATE INDEX idx_library_tracks_title_trgm
    ON library_tracks
    USING gin (LOWER(title) gin_trgm_ops);


-- ─── Sessions / audit logs ───────────────────────────────────────────────

-- Conversation sessions (short-term memory for multi-turn + "double check that").
CREATE TABLE sessions (
    id             UUID PRIMARY KEY,
    room_id        TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_activity  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    context        JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX idx_sessions_room_last_activity ON sessions (room_id, last_activity);

-- Intent log: routing decisions (matched_handler, matched_path, latency,
-- presence auditing). One row per routed turn — the intent-log invariant.
-- `matched_path` is an OPEN enum (registered_values domain 'matched_path'):
-- new dispatch paths must not require a migration. `presence_tier` stays
-- CHECKed — a closed, core-owned vocabulary.
CREATE TABLE intents_log (
    id                BIGSERIAL PRIMARY KEY,
    room_id           TEXT,
    transcript        TEXT,
    matched_handler   TEXT,
    matched_path      TEXT,
    online            BOOLEAN,
    latency_ms        INT,
    presence_tier     TEXT,
    person_id         INT REFERENCES people(id) ON DELETE SET NULL,
    at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT intents_log_presence_tier_chk
        CHECK (presence_tier IS NULL OR presence_tier IN ('low', 'medium', 'high'))
);
CREATE INDEX idx_intents_log_at ON intents_log (at);
CREATE INDEX idx_intents_log_person_id ON intents_log (person_id) WHERE person_id IS NOT NULL;

-- Conversation audit log: full content of every routed turn (user transcript
-- + assistant response side-by-side) with the same routing metadata for
-- joins. ON DELETE SET NULL keeps audit rows anonymized-not-gone when a
-- person is forgotten or a session expires. `matched_path` open, in lockstep
-- with intents_log via the shared registry domain — no two-table
-- CHECK lockstep to maintain.
CREATE TABLE conversation_log (
    id              BIGSERIAL PRIMARY KEY,
    session_id      UUID REFERENCES sessions(id) ON DELETE SET NULL,
    room_id         TEXT,
    person_id       INT  REFERENCES people(id)   ON DELETE SET NULL,
    user_text       TEXT NOT NULL,
    assistant_text  TEXT,
    matched_handler TEXT,
    matched_path    TEXT,
    presence_tier   TEXT,
    online          BOOLEAN,
    latency_ms      INT,
    at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT conversation_log_presence_tier_chk
        CHECK (presence_tier IS NULL OR presence_tier IN ('low', 'medium', 'high'))
);
CREATE INDEX idx_conversation_log_at ON conversation_log (at);
CREATE INDEX idx_conversation_log_session_id ON conversation_log (session_id)
    WHERE session_id IS NOT NULL;
CREATE INDEX idx_conversation_log_room_id_at ON conversation_log (room_id, at);
CREATE INDEX idx_conversation_log_person_id ON conversation_log (person_id)
    WHERE person_id IS NOT NULL;

-- ConnectivityProbe state transitions (not every poll — only changes).
CREATE TABLE connectivity_events (
    id                  BIGSERIAL PRIMARY KEY,
    connectivity_state  TEXT NOT NULL,
    target              TEXT NOT NULL,
    at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT connectivity_events_state_chk
        CHECK (connectivity_state IN ('online', 'offline'))
);
CREATE INDEX idx_connectivity_events_at ON connectivity_events (at);


-- ─── Calendar / notes ────────────────────────────────────────────────────

-- Google-synced AND locally-created events, distinguished by `source`.
CREATE TABLE calendar_events (
    id              SERIAL PRIMARY KEY,
    external_id     TEXT,
    source          TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    starts_at       TIMESTAMPTZ NOT NULL,
    ends_at         TIMESTAMPTZ,
    all_day         BOOLEAN NOT NULL DEFAULT FALSE,
    location        TEXT,
    last_synced_at  TIMESTAMPTZ,
    created_via     TEXT,
    CONSTRAINT calendar_events_source_chk
        CHECK (source IN ('google', 'local')),
    CONSTRAINT calendar_events_created_via_chk
        CHECK (created_via IS NULL OR created_via IN ('voice', 'sync', 'manual'))
);
CREATE INDEX idx_calendar_events_starts_at ON calendar_events (starts_at);

CREATE TABLE voice_notes (
    id          SERIAL PRIMARY KEY,
    room_id     TEXT,
    text        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_voice_notes_created_at ON voice_notes (created_at);


-- ─── Per-room MPD provisioning ───────────────────────────────────────────

-- One MPD container per satellite room, lazily provisioned on first
-- WebSocket connect. Source of truth for which room owns which port pair /
-- container so the mapping survives restarts. Allocation is serialized by a
-- Postgres advisory lock. Fresh install ⇒ starts empty; the provisioner
-- creates `domovoi-mpd-<room>` containers on first connect.
CREATE TABLE mpd_rooms (
    room_id            TEXT PRIMARY KEY,
    control_port       INT  NOT NULL UNIQUE,
    http_port          INT  NOT NULL UNIQUE,
    container_name     TEXT NOT NULL UNIQUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_connected_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ─── Web-search prefs / personalization ──────────────────────────────────

-- Per-speaker auto-search opt-in funnel for the proactive web-search offer
-- + volatile-question gate. Categories are a small core-owned fixed set.
CREATE TABLE web_search_prefs (
    person_id         INT          NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    category          TEXT         NOT NULL,
    auto_search       BOOLEAN      NOT NULL DEFAULT FALSE,
    yes_count         INT          NOT NULL DEFAULT 0,
    no_count          INT          NOT NULL DEFAULT 0,
    prefs_offered_at  TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (person_id, category),
    CONSTRAINT web_search_prefs_category_chk
        CHECK (category IN (
            'current_events', 'prices_finance', 'sports_scores',
            'general_recent', 'weather'
        ))
);
CREATE INDEX idx_web_search_prefs_auto ON web_search_prefs (person_id, category)
    WHERE auto_search = TRUE;

-- List-shaped favorites by (kind, value). `kind` is intentionally
-- un-CHECKed — handlers and plugins add kinds without a migration
-- (registered_values domain 'favorite_kind' mirrors the live set).
CREATE TABLE favorites (
    id            SERIAL       PRIMARY KEY,
    person_id     INT          NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    kind          TEXT         NOT NULL,
    value         TEXT         NOT NULL,
    rank          INT          NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT favorites_person_kind_value_unique
        UNIQUE (person_id, kind, value)
);
CREATE INDEX idx_favorites_person_id ON favorites (person_id);
CREATE INDEX idx_favorites_person_kind ON favorites (person_id, kind);

-- Free-form facts. Source-tagged (explicit / implicit / manual) and
-- status-tagged so pending extracted memories never leak into the prompt
-- prefix before confirmation.
CREATE TABLE memories (
    id              SERIAL       PRIMARY KEY,
    person_id       INT          NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    body            TEXT         NOT NULL,
    topic           TEXT,
    source          TEXT         NOT NULL,
    status          TEXT         NOT NULL DEFAULT 'active',
    confidence      REAL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,
    last_offered_at TIMESTAMPTZ,
    CONSTRAINT memories_source_chk
        CHECK (source IN ('explicit', 'implicit', 'manual')),
    CONSTRAINT memories_status_chk
        CHECK (status IN ('active', 'pending', 'rejected'))
);
CREATE INDEX idx_memories_person_status ON memories (person_id, status);
CREATE INDEX idx_memories_pending ON memories (person_id, created_at)
    WHERE status = 'pending';


-- ─── Playlists ───────────────────────────────────────────────────────────

CREATE TABLE playlists (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    cover_color     TEXT,                -- oklch/hex swatch for the UI
    cover_emoji     TEXT,
    resume_position INT,                 -- ordered-mode resume; NULL = none
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_playlists_name_ci ON playlists (LOWER(name));

CREATE TABLE playlist_tracks (
    id          SERIAL PRIMARY KEY,
    playlist_id INT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id    INT NOT NULL REFERENCES library_tracks(id) ON DELETE CASCADE,
    position    INT NOT NULL,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (playlist_id, track_id)
);
-- No UNIQUE on (playlist_id, position) on purpose: gaps after delete are
-- fine and "insert after X" shifts positions without transient collisions.
CREATE INDEX idx_playlist_tracks_playlist ON playlist_tracks (playlist_id, position);
CREATE INDEX idx_playlist_tracks_track ON playlist_tracks (track_id);


-- ─── Client greetings (reference/seed data) ──────────────────────────────

-- Satellite wake-word greeting bank, edited from the dashboard's Greetings
-- page. `{name}` is substituted with the configured bot name at render time.
-- Reference data: NOT truncated per test.
CREATE TABLE client_greetings (
    id          SERIAL       PRIMARY KEY,
    text        TEXT         NOT NULL UNIQUE,
    category    TEXT         NOT NULL,
    enabled     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT client_greetings_category_chk CHECK (category IN ('generic', 'funny'))
);
CREATE INDEX idx_client_greetings_enabled ON client_greetings (enabled) WHERE enabled;

INSERT INTO client_greetings (text, category) VALUES
    ('Hey!', 'generic'),
    ('What''s up?', 'generic'),
    ('Yeah?', 'generic'),
    ('Yes?', 'generic'),
    ('I''m listening.', 'generic'),
    ('Go ahead.', 'generic'),
    ('How can I help?', 'generic'),
    ('Right here.', 'generic'),
    ('Talk to me.', 'generic'),
    ('I''m all ears.', 'generic'),
    ('Ready.', 'generic'),
    ('What do you need?', 'generic'),
    ('Hi there!', 'generic'),
    ('Mm-hmm?', 'generic'),
    ('What can I do for you?', 'generic'),
    ('You''ve got me.', 'generic'),
    ('Hello!', 'generic'),
    ('Hey there!', 'generic'),
    ('Yep?', 'generic'),
    ('What''s going on?', 'generic'),
    ('I''m here.', 'generic'),
    ('Go for it.', 'generic'),
    ('Standing by.', 'generic'),
    ('Need something?', 'generic'),
    ('What''s on your mind?', 'generic'),
    ('Fire away.', 'generic'),
    ('How''s it going?', 'generic'),
    ('What''ll it be?', 'generic'),
    ('Whatcha need?', 'generic'),
    ('Right with you.', 'generic'),
    ('{name} here.', 'generic'),
    ('It''s {name}.', 'generic'),
    ('Yo!', 'generic'),
    ('Hiya!', 'generic'),
    ('Hey you.', 'generic'),
    ('Howdy!', 'generic'),
    ('How can I assist?', 'generic'),
    ('How can I be of service?', 'generic'),
    ('What would you like?', 'generic'),
    ('What can I get you?', 'generic'),
    ('Need a hand?', 'generic'),
    ('What''s the plan?', 'generic'),
    ('What''s happening?', 'generic'),
    ('What''re you after?', 'generic'),
    ('Say the word.', 'generic'),
    ('Just say it.', 'generic'),
    ('Let''s hear it.', 'generic'),
    ('Lay it on me.', 'generic'),
    ('Hit me.', 'generic'),
    ('Shoot.', 'generic'),
    ('Go on.', 'generic'),
    ('Whenever you''re ready.', 'generic'),
    ('I''m ready.', 'generic'),
    ('Ready when you are.', 'generic'),
    ('All set.', 'generic'),
    ('Tuned in.', 'generic'),
    ('Listening in.', 'generic'),
    ('I read you.', 'generic'),
    ('On the line.', 'generic'),
    ('Here to help.', 'generic'),
    ('Here for you.', 'generic'),
    ('Anything you need.', 'generic'),
    ('Help is here.', 'generic'),
    ('Ready to help.', 'generic'),
    ('You rang?', 'funny'),
    ('At your service.', 'funny'),
    ('Sup, boss?', 'funny'),
    ('{name}, reporting for duty.', 'funny'),
    ('Oh, it''s you again.', 'funny'),
    ('Make it quick.', 'funny'),
    ('Beep boop. How can I help?', 'funny'),
    ('You summoned me?', 'funny'),
    ('I was just thinking about you.', 'funny'),
    ('Back so soon?', 'funny'),
    ('Ah, a familiar voice.', 'funny'),
    ('I''m awake, I''m awake.', 'funny'),
    ('{name}, online and ready.', 'funny'),
    ('You''ve reached {name}.', 'funny');


-- ─── Voices (reference/seed-ish data) ────────────────────────────────────

-- First-class TTS voice registry: user-facing `name`, the `engine` that
-- renders it, `model_ref` locating the model. `engine` is an OPEN enum in
-- the DB (registered_values domain 'tts_engine'; core values edge / piper /
-- system) but app-validation is locked to the core set in v1 — TTS-engine
-- plugins are a documented v2 hook, not a v1 surface.
CREATE TABLE voices (
    id          SERIAL       PRIMARY KEY,
    name        TEXT         NOT NULL UNIQUE,
    engine      TEXT         NOT NULL,
    model_ref   TEXT         NOT NULL,
    is_default  BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
-- At most one default voice; partial unique lets set_default flip
-- transactionally (clear all, set one).
CREATE UNIQUE INDEX idx_voices_one_default ON voices (is_default) WHERE is_default;


-- ─── Media play history ──────────────────────────────────────────────────

-- Per-room "recently played" history: one row per play-start from every
-- site that emits music_action="start". `source` is an OPEN enum
-- (registered_values domain 'media_play_source'): core stamps library /
-- playlist / spoken_audio; media plugins stamp their own slug.
-- `video_id` is an opaque external-item reference used by provider plugins;
-- `library_track_id` is a nullable back-pointer so history survives a
-- track delete.
CREATE TABLE media_plays (
    id                BIGSERIAL    PRIMARY KEY,
    room_id           TEXT         NOT NULL,
    source            TEXT         NOT NULL,
    title             TEXT,
    artist            TEXT,
    channel           TEXT,
    video_id          TEXT,
    url               TEXT,
    stream_url        TEXT,
    library_track_id  INT          REFERENCES library_tracks(id) ON DELETE SET NULL,
    duration_sec      INT,
    started_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_media_plays_room_time ON media_plays (room_id, started_at DESC);
CREATE INDEX idx_media_plays_video_id ON media_plays (video_id) WHERE video_id IS NOT NULL;


-- ─── Drop-in calls ───────────────────────────────────────────────────────

-- Audit trail for two-way drop-in calls (live pairing is ephemeral in
-- app.state; relayed audio is NEVER persisted).
CREATE TABLE dropin_calls (
    id              BIGSERIAL    PRIMARY KEY,
    initiator_room  TEXT         NOT NULL,
    target_room     TEXT         NOT NULL,
    status          TEXT         NOT NULL,
    started_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    ended_by        TEXT,
    duration_sec    INT,
    CONSTRAINT dropin_calls_status_chk
        CHECK (status IN ('active', 'ended', 'declined', 'failed', 'timed_out'))
);
CREATE INDEX idx_dropin_calls_started ON dropin_calls (started_at DESC);


-- ─── Wake words (reference/seed-ish data) ────────────────────────────────

-- Custom wake-word registry. The `slug` is load-bearing: the served
-- `<slug>.onnx`, the satellite's effective wake_word, the openWakeWord
-- prediction-dict key, and the model file stem must ALL equal it.
CREATE TABLE wake_words (
    id             SERIAL       PRIMARY KEY,
    name           TEXT         NOT NULL UNIQUE,
    slug           TEXT         NOT NULL UNIQUE,
    phrase         TEXT         NOT NULL,
    threshold      REAL         NOT NULL DEFAULT 0.5,
    model_ref      TEXT,
    is_default     BOOLEAN      NOT NULL DEFAULT FALSE,
    status         TEXT         NOT NULL DEFAULT 'recording',
    source_room_id TEXT,
    clip_count     INT          NOT NULL DEFAULT 0,
    error          TEXT,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT wake_words_status_chk CHECK (status IN ('recording', 'training', 'ready', 'failed'))
);
CREATE UNIQUE INDEX idx_wake_words_one_default ON wake_words (is_default) WHERE is_default;


-- ─── Office suite editor locks ───────────────────────────────────────────

-- Per-file editor lock so two engines can't clobber the same document.
CREATE TABLE document_sessions (
    id            BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rel_path      TEXT         NOT NULL,
    engine        TEXT         NOT NULL CHECK (engine IN ('onlyoffice', 'collabora')),
    opened_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    editor_key    TEXT,
    UNIQUE (rel_path)
);
CREATE INDEX idx_document_sessions_last_seen ON document_sessions (last_seen_at);
CREATE INDEX idx_document_sessions_editor_key ON document_sessions (editor_key);


-- ─── Spoken audio: podcasts + audiobooks + resume ────────────────────────

-- Per-(device × person × item) resume position. Positions deliberately do
-- NOT sync across devices.
CREATE TABLE playback_positions (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    item_type    TEXT        NOT NULL CHECK (item_type IN ('podcast_episode', 'audiobook')),
    item_id      BIGINT      NOT NULL,
    device_id    TEXT        NOT NULL,
    person_id    BIGINT      REFERENCES people(id) ON DELETE CASCADE,
    position_sec INTEGER     NOT NULL DEFAULT 0,
    speed        REAL        NOT NULL DEFAULT 1.0,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Two partial unique indexes (not one plain UNIQUE): Postgres treats NULLs
-- as distinct, so a plain UNIQUE would let two "unidentified" rows coexist
-- for the same item+device. The pair also gives ON CONFLICT concrete
-- targets for both upsert paths.
CREATE UNIQUE INDEX idx_playback_positions_person
    ON playback_positions (item_type, item_id, device_id, person_id)
    WHERE person_id IS NOT NULL;
CREATE UNIQUE INDEX idx_playback_positions_anon
    ON playback_positions (item_type, item_id, device_id)
    WHERE person_id IS NULL;
CREATE INDEX idx_playback_positions_device_person
    ON playback_positions (device_id, person_id, updated_at DESC);

CREATE TABLE podcast_subscriptions (
    id             SERIAL       PRIMARY KEY,
    feed_url       TEXT         NOT NULL UNIQUE,
    title          TEXT,
    author         TEXT,
    artwork        TEXT,
    description    TEXT,
    keep_n         INTEGER      NOT NULL DEFAULT 5,
    last_polled_at TIMESTAMPTZ,
    added_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- download_status lifecycle: pending → downloading → downloaded | failed;
-- 'skipped' for episodes past the keep-N window (also the LRU-evicted state).
CREATE TABLE podcast_episodes (
    id              SERIAL       PRIMARY KEY,
    subscription_id INTEGER      NOT NULL REFERENCES podcast_subscriptions(id) ON DELETE CASCADE,
    guid            TEXT         NOT NULL,
    title           TEXT,
    description     TEXT,
    enclosure_url   TEXT,
    published_at    TIMESTAMPTZ,
    duration_sec    INTEGER,
    chapters        JSONB,
    file_path       TEXT,
    download_status TEXT         NOT NULL DEFAULT 'pending',
    downloaded_at   TIMESTAMPTZ,
    error           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT podcast_episodes_status_chk
        CHECK (download_status IN ('pending', 'downloading', 'downloaded', 'failed', 'skipped')),
    CONSTRAINT podcast_episodes_sub_guid_uniq UNIQUE (subscription_id, guid)
);
CREATE INDEX idx_podcast_episodes_sub_published
    ON podcast_episodes (subscription_id, published_at DESC);
CREATE INDEX idx_podcast_episodes_status
    ON podcast_episodes (download_status, created_at);

-- A book is a single .m4b OR a per-chapter folder; `chapters` carries the
-- ordered chapter list either way.
CREATE TABLE audiobooks (
    id           SERIAL       PRIMARY KEY,
    title        TEXT         NOT NULL,
    author       TEXT,
    narrator     TEXT,
    artwork      TEXT,
    chapters     JSONB,
    file_path    TEXT         NOT NULL UNIQUE,
    is_folder    BOOLEAN      NOT NULL DEFAULT FALSE,
    duration_sec INTEGER,
    added_via    TEXT         NOT NULL DEFAULT 'index',
    added_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT audiobooks_added_via_chk CHECK (added_via IN ('index', 'import'))
);
CREATE INDEX idx_audiobooks_title ON audiobooks (LOWER(title));
CREATE INDEX idx_audiobooks_author ON audiobooks (LOWER(author));


-- ─── News ────────────────────────────────────────────────────────────────

CREATE TABLE news_topics (
    id         SERIAL       PRIMARY KEY,
    person_id  BIGINT       NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    kind       TEXT         NOT NULL CHECK (kind IN ('category', 'freeform')),
    topic      TEXT         NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT news_topics_person_kind_topic_uniq UNIQUE (person_id, kind, topic)
);
CREATE INDEX idx_news_topics_person ON news_topics (person_id);

-- Deduped RSS feed registry. `scope` is set only for the three house-scope
-- geographic feeds; topic feeds leave it NULL.
CREATE TABLE news_feeds (
    id             SERIAL       PRIMARY KEY,
    url            TEXT         NOT NULL UNIQUE,
    title          TEXT,
    source         TEXT,
    discovered_via TEXT         NOT NULL DEFAULT 'manual'
                   CHECK (discovered_via IN ('default', 'searxng', 'manual')),
    scope          TEXT         CHECK (scope IN ('local', 'national', 'global')),
    valid          BOOLEAN      NOT NULL DEFAULT TRUE,
    last_polled_at TIMESTAMPTZ,
    last_error     TEXT,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_news_feeds_scope ON news_feeds (scope) WHERE scope IS NOT NULL;
CREATE INDEX idx_news_feeds_valid_polled ON news_feeds (valid, last_polled_at);

CREATE TABLE topic_feeds (
    topic_id INTEGER NOT NULL REFERENCES news_topics(id) ON DELETE CASCADE,
    feed_id  INTEGER NOT NULL REFERENCES news_feeds(id)  ON DELETE CASCADE,
    PRIMARY KEY (topic_id, feed_id)
);
CREATE INDEX idx_topic_feeds_feed ON topic_feeds (feed_id);

-- Fetched articles. Exactly one owner: per-person (person_id, no scope) or
-- house-scoped (scope, no person).
CREATE TABLE news_items (
    id           SERIAL       PRIMARY KEY,
    person_id    BIGINT       REFERENCES people(id) ON DELETE CASCADE,
    scope        TEXT         CHECK (scope IN ('local', 'national', 'global')),
    topic_id     INTEGER      REFERENCES news_topics(id) ON DELETE SET NULL,
    feed_id      INTEGER      NOT NULL REFERENCES news_feeds(id) ON DELETE CASCADE,
    guid         TEXT         NOT NULL,
    url          TEXT,
    title        TEXT,
    source       TEXT,
    summary      TEXT,
    published_at TIMESTAMPTZ,
    fetched_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    favorited    BOOLEAN      NOT NULL DEFAULT FALSE,
    read_at      TIMESTAMPTZ,
    CONSTRAINT news_items_owner_chk CHECK (
        (person_id IS NOT NULL AND scope IS NULL)
        OR (person_id IS NULL AND scope IS NOT NULL)
    )
);
-- NULLs collapse via coalesce so dedup holds for both ownership shapes.
CREATE UNIQUE INDEX idx_news_items_dedup
    ON news_items (COALESCE(person_id, 0), COALESCE(scope, ''), guid);
CREATE INDEX idx_news_items_person_published
    ON news_items (person_id, published_at DESC) WHERE person_id IS NOT NULL;
CREATE INDEX idx_news_items_scope_published
    ON news_items (scope, published_at DESC) WHERE scope IS NOT NULL;
CREATE INDEX idx_news_items_retention
    ON news_items (fetched_at) WHERE NOT favorited;

CREATE TABLE news_briefings (
    person_id    BIGINT       PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
    briefing     TEXT,
    generated_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);


-- ─── Model pull jobs ─────────────────────────────────────────────────────

-- Durable tracking for long-streamed Ollama model downloads (Models page).
CREATE TABLE model_jobs (
    id           BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model        TEXT         NOT NULL,
    status       TEXT         NOT NULL DEFAULT 'pending',
    pct          INTEGER,
    status_text  TEXT,
    error        TEXT,
    requested_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT model_jobs_status_chk
        CHECK (status IN ('pending', 'running', 'done', 'failed', 'cancelled')),
    CONSTRAINT model_jobs_pct_chk
        CHECK (pct IS NULL OR (pct >= 0 AND pct <= 100))
);
CREATE INDEX idx_model_jobs_status ON model_jobs (status, requested_at DESC);
-- One live pull per model; a duplicate "install X" attaches to the running job.
CREATE UNIQUE INDEX idx_model_jobs_active_model
    ON model_jobs (model)
    WHERE status IN ('pending', 'running');


-- ─── Plugins registry ────────────────────────────────────────────────────

-- One row per known plugin. The `manifest` JSONB is load-bearing: it is how
-- the web process and the capability-manifest endpoint learn about plugins
-- without importing plugin code. Every mutation of this table fires
-- pg_notify('plugins_changed', <slug>) in the same transaction.
-- `install_source` and `status` are app-validated (no CHECK):
--   install_source ∈ 'bundled' | 'zip' | 'github' | 'dev'
--   status         ∈ 'ok' | 'degraded' | 'load_error' | 'uninstalled'
-- 'uninstalled' is the tombstone for a bundled plugin the user removed; it
-- blocks boot-time discovery from auto-re-registering the plugin.
CREATE TABLE plugins (
    slug            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    version         TEXT NOT NULL,
    publisher       TEXT,
    license         TEXT,
    domovoi_api     TEXT NOT NULL,           -- declared SDK compat range
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    bundled         BOOLEAN NOT NULL DEFAULT FALSE,
    install_source  TEXT NOT NULL,
    source_ref      TEXT,                    -- github url@ref / original zip filename
    install_dir     TEXT NOT NULL,           -- absolute path
    manifest        JSONB NOT NULL,          -- full parsed manifest
    pip_report      JSONB,                   -- durable install-time pip record:
                                             -- newly-installed vs pre-existing dists
                                             -- (uninstall refcount / upgrade math
                                             -- consult this after staging is gone)
    status          TEXT NOT NULL DEFAULT 'ok',
    last_error      TEXT,
    installed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ─── Registered values (informational open-enum mirror) ──────────────────

-- The open enums above are validated in app code against in-process
-- registries populated at core boot + plugin registration. This table is
-- the informational mirror of those registries (written at registration
-- time) so DBAs/analytics can see the live vocabulary. It is NOT consulted
-- for validation and carries no FKs.
CREATE TABLE registered_values (
    domain TEXT NOT NULL,
    value  TEXT NOT NULL,
    owner  TEXT,
    PRIMARY KEY (domain, value)
);

-- Core seed vocabulary. ('llm' / 'llm_offline' are the current tool-dispatch
-- stamps; the 'tool' / 'tool_offline' spellings are seeded alongside for the
-- planned rename so analytics tooling can key on either during the cutover.)
INSERT INTO registered_values (domain, value, owner) VALUES
    ('matched_path', 'fast',           'core'),
    ('matched_path', 'fast_offline',   'core'),
    ('matched_path', 'llm',            'core'),
    ('matched_path', 'llm_offline',    'core'),
    ('matched_path', 'tool',           'core'),
    ('matched_path', 'tool_offline',   'core'),
    ('matched_path', 'qa',             'core'),
    ('matched_path', 'qa_offline',     'core'),
    ('matched_path', 'error',          'core'),
    ('matched_path', 'confirmation',   'core'),
    ('matched_path', 'auto_search',    'core'),
    ('matched_path', 'volatile_offer', 'core'),
    ('matched_path', 'chat',           'core'),
    ('library_source', 'manual',       'core'),
    ('library_source', 'indexed',      'core'),
    ('library_source', 'upload',       'core'),
    ('media_play_source', 'library',      'core'),
    ('media_play_source', 'playlist',     'core'),
    ('media_play_source', 'spoken_audio', 'core'),
    ('tts_engine', 'edge',   'core'),
    ('tts_engine', 'piper',  'core'),
    ('tts_engine', 'system', 'core'),
    ('acquisition_status', 'pending',       'core'),
    ('acquisition_status', 'claimed',       'core'),
    ('acquisition_status', 'done',          'core'),
    ('acquisition_status', 'failed',        'core'),
    ('acquisition_status', 'unfulfillable', 'core'),
    ('acquisition_status', 'cancelled',     'core');


-- ─── Media acquisition queue ─────────────────────────────────────────────

-- Core service: a structured request to obtain media into the local
-- library, produced by voice handlers, the web UI, chat, and plugins —
-- fulfilled by whichever media-provider plugin registers as a fulfiller.
-- No provider wire formats anywhere: `kind='query'` carries plain search
-- text, `kind='url'` a caller-supplied URL. Enqueue always succeeds; with
-- no fulfiller enabled rows sit 'pending' and drain when a provider is
-- installed later. `attach_to_playlist_id` is a soft ref (NO FK): the
-- completion path re-checks the playlist and skips the attach with a log
-- if it died meanwhile. `status` is app-validated (registered_values
-- domain 'acquisition_status').
CREATE TABLE media_acquisitions (
    id                    BIGSERIAL PRIMARY KEY,
    kind                  TEXT NOT NULL,              -- 'query' | 'url' (app-validated)
    text                  TEXT NOT NULL,              -- search text OR URL — never a provider wire format
    metadata              JSONB NOT NULL DEFAULT '{}',-- producer hints: {artist, title, station_name, ...}
    requested_by          TEXT NOT NULL,              -- 'voice:<handler>' | 'web' | 'chat' | 'plugin:<slug>'
    origin_ref            TEXT,                       -- soft ref, e.g. 'plugin_<slug>:<table>:<id>'
    attach_to_playlist_id BIGINT,                     -- soft ref to playlists (NO FK)
    dedup_key             TEXT,                       -- provider-namespaced identity
    status                TEXT NOT NULL DEFAULT 'pending',
    claimed_by            TEXT,                       -- fulfiller plugin slug
    claimed_at            TIMESTAMPTZ,
    attempts              INT NOT NULL DEFAULT 0,
    next_attempt_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    result                JSONB,                      -- {"library_track_id": ..., "file_path": ...}
    error                 TEXT,
    requested_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at          TIMESTAMPTZ
);
CREATE INDEX ma_queue_idx ON media_acquisitions (status, next_attempt_at);
-- Live-queue dedup: one in-flight acquisition per provider-namespaced
-- identity. INSERT ... ON CONFLICT DO NOTHING against this partial index
-- is the in-flight dedup.
CREATE UNIQUE INDEX ma_dedup_live_idx ON media_acquisitions (dedup_key)
    WHERE dedup_key IS NOT NULL AND status IN ('pending', 'claimed');


-- ─── Admin auth ──────────────────────────────────────────────────────────

-- Single-row admin credential. `password_hash` is argon2id; the password is
-- chosen on the dashboard's first-run setup screen (which requires the
-- one-time setup code written to ~/.domovoi/setup-code.txt — proof of
-- possession of the server). Server-side hashing only.
CREATE TABLE admin_auth (
    id            INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Admin bearer sessions. Only the sha256 of the 256-bit token is stored;
-- 30-day sliding expiry via `expires_at` refreshed on use. `label` is the
-- user-facing session name for the settings page's revoke list.
CREATE TABLE admin_sessions (
    token_hash   TEXT PRIMARY KEY,
    label        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    last_used_at TIMESTAMPTZ
);
CREATE INDEX idx_admin_sessions_expires ON admin_sessions (expires_at);
