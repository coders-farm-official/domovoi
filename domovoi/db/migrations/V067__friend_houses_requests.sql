-- V067 — Friend Houses: friend files and friend requests (2026-11-17).
--
-- Friend Houses F2, friending (domovoi/friend_houses/friending.py). House A
-- makes a friend file; house B imports it, which sends a request to A's
-- pairing onion; an admin at A accepts, and A tells B over B's onion.
--
-- friend_houses_invites, one row per friend file this house made:
--   id            the invite id (32 hex characters). The invite SECRET is
--                 not stored anywhere: it is derived from the house key and
--                 this id (identity.HouseKeys.invite_secret), so no dump of
--                 this table lets anyone use a file.
--   label         an admin's note ("for the Smiths"); never sent.
--   expires_at    1 to 30 days after it was made (owner decision 11).
--   used_at       when a request proved the secret: single use, even if
--                 the request is then declined.
--   cancelled_at  an admin cancelled it before it was used.
--   created_by    which admin device made it.
--
-- friend_houses_requests, one row per friend request in either direction:
--   direction     'in' (someone used our file) | 'out' (we used theirs).
--   state         in:  'waiting' (for an admin here)
--                      'accepted' (accepted; telling them is still to do)
--                 out: 'unsent' (their house hasn't been reached yet)
--                      'sent' (they have it; waiting for their accept)
--   house_key, session_key   the other house's keys.
--   statement     in: their signed address statement (their onion).
--   their_name    what the other house calls itself (shown as such only).
--   local_name    what this household calls them (an admin chose it).
--   code          the 4-digit code both houses computed (owner decision 14).
--   invite_id     in: our invite they used. out: the file's invite id.
--   reach_private in: the key they gave us to reach their onion.
--   reach_public  the key we authorized for them on our onion.
--   reach_gift    in, accepted: the private half of reach_public, kept only
--                 until the accept reaches them, then the row is deleted.
--   friend_file   out, unsent: the file, kept only to retry; cleared once sent.
--   expires_at    when the request is dropped if nothing happens.
--   attempts, last_error   tries to reach the other house, and the last
--                 failure in plain words.
--
-- IF NOT EXISTS throughout. No semicolon or double dash inside a
-- statement: the test kits split this file naively.

CREATE TABLE IF NOT EXISTS friend_houses_invites (
    id            TEXT PRIMARY KEY CHECK (id ~ '^[0-9a-f]{32}$'),
    label         TEXT CHECK (label IS NULL OR length(label) <= 80),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    used_at       TIMESTAMPTZ,
    cancelled_at  TIMESTAMPTZ,
    created_by    TEXT
);

CREATE TABLE IF NOT EXISTS friend_houses_requests (
    id            TEXT PRIMARY KEY CHECK (id ~ '^[a-z0-9]{1,32}$'),
    direction     TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    state         TEXT NOT NULL CHECK (state IN ('waiting', 'accepted', 'unsent', 'sent')),
    house_key     BYTEA NOT NULL CHECK (octet_length(house_key) = 32),
    session_key   BYTEA NOT NULL CHECK (octet_length(session_key) = 32),
    statement     BYTEA,
    their_name    TEXT NOT NULL DEFAULT '' CHECK (length(their_name) <= 60),
    local_name    TEXT CHECK (local_name IS NULL OR length(local_name) BETWEEN 1 AND 60),
    code          TEXT CHECK (code IS NULL OR code ~ '^[0-9]{4}$'),
    invite_id     TEXT,
    reach_private BYTEA CHECK (reach_private IS NULL OR octet_length(reach_private) = 32),
    reach_public  BYTEA CHECK (reach_public IS NULL OR octet_length(reach_public) = 32),
    reach_gift    BYTEA CHECK (reach_gift IS NULL OR octet_length(reach_gift) = 32),
    friend_file   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    UNIQUE (direction, house_key)
);
