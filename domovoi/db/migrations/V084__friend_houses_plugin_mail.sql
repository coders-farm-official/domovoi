-- V084 — Friend Houses: plugin mail, "apps with friends" (1.5.0, SDK 1.18).
--
-- Mail between two houses' copies of one plugin (design note
-- FRIEND-HOUSES-PLUGINS section 6, build slice 4): the per-friend toggle,
-- the per-(friend, plugin) pairs and their breaker, the outbox and the
-- inbox, and the sender's persisted caps. domovoi/friend_houses/plugin_mail.py
-- and plugin_sender.py are the only writers.
--
-- friend_houses_friends gains:
--   apps_on, apps_on_at, apps_on_by   "Apps with this friend", set by an admin
--   caps_needed        this house's CAPS must reach the friend again (cleared
--                      only when a CAPS was written on a connection that then
--                      answered a PING)
--   caps_sent_at       when that last happened (the 3-hour refresh)
--   peer_proto         null unknown, 1 a house before 1.5.0, 2+ plugin mail
--   peer_caps, peer_caps_at, peer_caps_epoch   the friend's last CAPS
--   probe_at, probe_closes, first_close_at     the legacy probe: a friend is
--                      recorded as legacy only after two closes 5+ min apart
--   peer_epoch         the friend's current sending epoch
--   peer_epochs_retired  at most 16 earlier ones, never accepted again
--
-- friend_houses_plugin_pairs, one row per (friend, plugin), NEVER deleted
-- while the friend row lives: uninstall and reinstall keep out_seq and
-- in_mark, and a closure only sets allowed = false and bumps generation.
--   fault_events   at most 64 [unix_ts, hundredths, kind] of the last hour
--                  (kind d decoder, h handler, r restart, s slow decode)
--   refusal_times  at most 160 unix timestamps of the last hour
--
-- friend_houses_plugin_outbox / _inbox: one row per mail. ACK means the
-- receiver committed the row. The inbox's id is the decoder's item key.
--
-- friend_houses_plugin_sender, one row per friend: the dial log (at most
-- 30 timestamps of the last hour), the friend backoff and the sender's
-- per-friend buckets, so a core restart refills none of them.
-- friend_houses_state.plugin_buckets holds the two house-wide sender
-- buckets (count and bytes) for the same reason.
--
-- Every new table cascades from the friend row: removing or blocking a
-- friend deletes its pairs, mail and sender state with it.
--
-- IF NOT EXISTS throughout. No semicolon or double dash inside a
-- statement: the test kits split this file naively.

ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS apps_on BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS apps_on_at TIMESTAMPTZ;
ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS apps_on_by TEXT;
ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS caps_needed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS caps_sent_at TIMESTAMPTZ;
ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS peer_proto SMALLINT;
ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS peer_caps TEXT[];
ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS peer_caps_at TIMESTAMPTZ;
ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS peer_caps_epoch BYTEA;
ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS probe_at TIMESTAMPTZ;
ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS probe_closes SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS first_close_at TIMESTAMPTZ;
ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS peer_epoch BYTEA;
ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS peer_epochs_retired BYTEA[] NOT NULL DEFAULT '{}';

ALTER TABLE friend_houses_state ADD COLUMN IF NOT EXISTS plugin_buckets JSONB NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS friend_houses_plugin_pairs (
    friend_id        TEXT NOT NULL REFERENCES friend_houses_friends (id) ON DELETE CASCADE,
    slug             TEXT NOT NULL CHECK (slug ~ '^[a-z][a-z0-9_]{1,31}$'),
    allowed          BOOLEAN NOT NULL DEFAULT FALSE,
    confirmed_at     TIMESTAMPTZ,
    confirmed_by     TEXT,
    generation       BIGINT NOT NULL DEFAULT 1,
    out_seq          BIGINT NOT NULL DEFAULT 0 CHECK (out_seq >= 0),
    in_epoch         BYTEA,
    in_mark          BIGINT NOT NULL DEFAULT 0,
    fault_events     JSONB NOT NULL DEFAULT '[]',
    refusal_times    JSONB NOT NULL DEFAULT '[]',
    paused_until     TIMESTAMPTZ,
    pause_cause      TEXT CHECK (pause_cause IS NULL OR pause_cause IN ('faults', 'restart', 'versions', 'handler')),
    next_pause_sec   INT NOT NULL DEFAULT 3600,
    last_pause_at    TIMESTAMPTZ,
    pair_retry_at    TIMESTAMPTZ,
    pair_backoff_sec INT NOT NULL DEFAULT 0,
    na_hold_until    TIMESTAMPTZ,
    na_first_at      TIMESTAMPTZ,
    decode_ms_hour   INT NOT NULL DEFAULT 0,
    PRIMARY KEY (friend_id, slug)
);

CREATE TABLE IF NOT EXISTS friend_houses_plugin_outbox (
    id           BIGSERIAL PRIMARY KEY,
    friend_id    TEXT NOT NULL REFERENCES friend_houses_friends (id) ON DELETE CASCADE,
    slug         TEXT NOT NULL,
    generation   BIGINT NOT NULL,
    mail_id      BYTEA NOT NULL CHECK (octet_length(mail_id) = 16),
    seq          BIGINT NOT NULL,
    replace_key  TEXT CHECK (replace_key IS NULL OR replace_key ~ '^[a-z0-9_.:-]{1,64}$'),
    payload      BYTEA NOT NULL,
    size         INT NOT NULL,
    ttl_s        INT NOT NULL CHECK (ttl_s BETWEEN 30 AND 259200),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    state        TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'inflight')),
    attempts     INT NOT NULL DEFAULT 0,
    last_error   TEXT
);

CREATE INDEX IF NOT EXISTS friend_houses_plugin_outbox_pair_idx
    ON friend_houses_plugin_outbox (friend_id, slug, id);
CREATE UNIQUE INDEX IF NOT EXISTS friend_houses_plugin_outbox_replace_idx
    ON friend_houses_plugin_outbox (friend_id, slug, replace_key)
    WHERE state = 'queued' AND replace_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS friend_houses_plugin_inbox (
    id           BIGSERIAL PRIMARY KEY,
    friend_id    TEXT NOT NULL REFERENCES friend_houses_friends (id) ON DELETE CASCADE,
    slug         TEXT NOT NULL,
    generation   BIGINT NOT NULL,
    mail_id      BYTEA NOT NULL CHECK (octet_length(mail_id) = 16),
    epoch        BYTEA NOT NULL,
    seq          BIGINT NOT NULL,
    payload      BYTEA,
    canonical    BYTEA,
    image_png    BYTEA CHECK (image_png IS NULL OR octet_length(image_png) <= 2621440),
    size         INT NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    state        TEXT NOT NULL DEFAULT 'stored' CHECK (state IN ('stored', 'decoding', 'decoded', 'handling')),
    attempt_id   UUID,
    started_at   TIMESTAMPTZ,
    faults       SMALLINT NOT NULL DEFAULT 0,
    next_try_at  TIMESTAMPTZ,
    UNIQUE (friend_id, slug, mail_id)
);

CREATE INDEX IF NOT EXISTS friend_houses_plugin_inbox_state_idx
    ON friend_houses_plugin_inbox (state, id);

CREATE TABLE IF NOT EXISTS friend_houses_plugin_sender (
    friend_id    TEXT PRIMARY KEY REFERENCES friend_houses_friends (id) ON DELETE CASCADE,
    dials        JSONB NOT NULL DEFAULT '[]',
    buckets      JSONB NOT NULL DEFAULT '{}',
    backoff_sec  INT NOT NULL DEFAULT 0,
    retry_at     TIMESTAMPTZ
);
