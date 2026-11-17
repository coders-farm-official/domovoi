-- V068 — Friend Houses: messages and per-friend alerts (2026-11-17).
--
-- Friend Houses F3, messages (domovoi/friend_houses/messages.py). One inbox
-- for the whole house (owner decision 16: no per-person sign-in yet).
--
-- friend_houses_friends.alert, what a new message from that friend does
-- (owner decision 15: a per-friend setting, least intrusive by default,
-- turned up per friend by an admin):
--   'inbox'   nothing but the inbox (the default after accepting)
--   'sender'  the rooms hear who it's from ("A message from the Smiths")
--   'read'    the rooms hear who it's from and the message itself
-- Announcements respect each room's quiet hours.
--
-- friend_houses_messages, one row per message in or out:
--   house_key     the friend house's key. Kept with the row, and with
--   friend_name   what the household called it, so a removed friend's
--                 messages can still be read and exported (finding 31).
--   friend_id     the friend while it is one; NULL once removed.
--   direction     'in' | 'out'.
--   msg_id        16 random bytes chosen by the sender; with house_key and
--                 direction it de-duplicates (a re-sent message is stored
--                 once, finding 19).
--   epoch, seq    the sender's epoch and sequence: display order.
--   sent_at       the sender's clock (unix seconds): shown as "sent at …
--                 (their clock)" only, never used for expiry.
--   created_at    this house's clock: when it was received or written.
--   body          the text.
--   envelope      the signed envelope exactly as sent or received: the
--                 proof of what was said (owner decision 12).
--   state         in: 'unread' | 'read'.  out: 'queued' | 'sent' | 'failed'.
--   delivered_at  out: when the friend acknowledged storing it.
--   expires_at    out: when a message still not delivered fails (7 days),
--                 and the sender is told it wasn't delivered (finding 32).
--   attempts, last_error   out: delivery tries and the last reason.
--
-- The text is the household's own correspondence: kept on this server only,
-- admin-read like the rest of Friend Houses, deleted with its row. It is
-- encrypted between houses, not end to end: both houses' admins and
-- software can read it (docs/SECURITY_PRIVACY.md, "Friend Houses").
--
-- IF NOT EXISTS throughout. No semicolon or double dash inside a
-- statement: the test kits split this file naively.

ALTER TABLE friend_houses_friends
    ADD COLUMN IF NOT EXISTS alert TEXT NOT NULL DEFAULT 'inbox'
    CHECK (alert IN ('inbox', 'sender', 'read'));

CREATE TABLE IF NOT EXISTS friend_houses_messages (
    id            BIGSERIAL PRIMARY KEY,
    house_key     BYTEA NOT NULL CHECK (octet_length(house_key) = 32),
    friend_id     TEXT REFERENCES friend_houses_friends (id) ON DELETE SET NULL,
    friend_name   TEXT NOT NULL,
    direction     TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    msg_id        BYTEA NOT NULL CHECK (octet_length(msg_id) = 16),
    epoch         BYTEA NOT NULL CHECK (octet_length(epoch) = 8),
    seq           BIGINT NOT NULL,
    sent_at       BIGINT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    body          TEXT NOT NULL,
    envelope      BYTEA NOT NULL,
    state         TEXT NOT NULL CHECK (state IN ('unread', 'read', 'queued', 'sent', 'failed')),
    delivered_at  TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ,
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    UNIQUE (house_key, direction, msg_id)
);

CREATE INDEX IF NOT EXISTS friend_houses_messages_friend_idx
    ON friend_houses_messages (house_key, created_at);
CREATE INDEX IF NOT EXISTS friend_houses_messages_outbox_idx
    ON friend_houses_messages (state) WHERE state = 'queued';
