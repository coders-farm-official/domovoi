-- V070 — Friend Houses: presence and blocking (2026-11-17).
--
-- Owner decision 18: presence means only whether a friend's HOUSE is online
-- (nothing about people, rooms or activity); blocking ends a friendship for
-- good and moves this house to a new address.
--
-- friend_houses_friends gains:
--   last_seen_at     the last time this house heard from that friend's house
--                    (any connection either way, or the light check every
--                    10 minutes). "Online" is "heard from in the last 15".
--   announce_needed  this house moved to a new address (after a block) and
--                    hasn't told this friend yet; the retry pass keeps trying.
--
-- friend_houses_blocked, one row per blocked house: its key, what the
-- household called it, and when. A request or an accept from a blocked
-- house is dropped without a word; an admin can unblock.
--
-- IF NOT EXISTS throughout. No semicolon or double dash inside a
-- statement: the test kits split this file naively.

ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
ALTER TABLE friend_houses_friends ADD COLUMN IF NOT EXISTS announce_needed BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS friend_houses_blocked (
    house_key   BYTEA PRIMARY KEY CHECK (octet_length(house_key) = 32),
    name        TEXT NOT NULL,
    blocked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
