-- V066 — Friend Houses: the friend list and the sending epoch (2026-11-17).
--
-- Friend Houses ("serverless messaging and friend system between
-- households", owner decision 2026-11-17): core, off by default, each house
-- reachable only by its friends over a Tor onion service
-- (domovoi/friend_houses/). This migration holds what F1 (identity and
-- transport) needs; friending (F2) and messages (F3) add their own tables.
--
-- The house's private keys are NOT here: they live in a 0600 file beside
-- the server identity (domovoi/friend_houses/identity.py), so the dump the
-- update unit takes before every update never holds them.
--
-- friend_houses_state, one row (id = 1):
--   epoch         8 random bytes: this house's sending epoch. A new one
--                 starts whenever this table and the marker file beside the
--                 keys disagree, i.e. after a rollback (threat-model finding
--                 16), so friends never refuse its messages as replays.
--   marker        the random marker the file must match.
--   next_seq      the next sequence number to send in this epoch.
--
-- friend_houses_friends, one row per friend house:
--   id            short lowercase id, used for Tor's key file names
--                 ("<id>.auth", "<id>.auth_private").
--   house_key     the friend's Ed25519 house key (its identity), unique.
--   session_key   its X25519 Noise static key, from its newest statement.
--   onion_key     its onion service key (the .onion address).
--   statement     its newest signed address statement, as received; a
--                 statement is only ever replaced by a newer one.
--   statement_at  that statement's issue time (unix seconds).
--   reach_public  the key this house authorized for the friend (Tor client
--                 authorization: the friend holds the private half).
--   reach_private the key the friend gave this house to reach it.
--   local_name    what this household calls the friend (set by an admin;
--                 the name a friend gives itself is never trusted alone,
--                 finding 11).
--   state         'pending' (asked, not accepted yet) | 'active' | 'blocked'.
--   verified_at   when someone compared the 4-digit codes (null: "Not
--                 verified", owner decision 14).
--   code          the 4-digit code both houses computed at friending.
--   added_at, added_by   when, and from which device or admin.
--
-- IF NOT EXISTS throughout. No semicolon or double dash inside a
-- statement: the test kits split this file naively.

CREATE TABLE IF NOT EXISTS friend_houses_state (
    id          SMALLINT PRIMARY KEY CHECK (id = 1),
    epoch       BYTEA NOT NULL CHECK (octet_length(epoch) = 8),
    marker      TEXT NOT NULL,
    next_seq    BIGINT NOT NULL DEFAULT 1 CHECK (next_seq >= 1),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS friend_houses_friends (
    id            TEXT PRIMARY KEY CHECK (id ~ '^[a-z0-9]{1,32}$'),
    house_key     BYTEA NOT NULL UNIQUE CHECK (octet_length(house_key) = 32),
    session_key   BYTEA NOT NULL CHECK (octet_length(session_key) = 32),
    onion_key     BYTEA NOT NULL CHECK (octet_length(onion_key) = 32),
    statement     BYTEA NOT NULL,
    statement_at  BIGINT NOT NULL,
    reach_public  BYTEA NOT NULL CHECK (octet_length(reach_public) = 32),
    reach_private BYTEA CHECK (reach_private IS NULL OR octet_length(reach_private) = 32),
    local_name    TEXT NOT NULL CHECK (length(local_name) BETWEEN 1 AND 60),
    state         TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'active', 'blocked')),
    verified_at   TIMESTAMPTZ,
    code          TEXT CHECK (code IS NULL OR code ~ '^[0-9]{4}$'),
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by      TEXT
);

CREATE INDEX IF NOT EXISTS friend_houses_friends_session_idx
    ON friend_houses_friends (session_key);
