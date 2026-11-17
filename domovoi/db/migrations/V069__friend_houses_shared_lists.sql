-- V069 — Friend Houses: shared lists (2026-11-17).
--
-- Friend Houses F4 (domovoi/friend_houses/spaces.py, shared_lists.py). A
-- household list can be shared with one friend house. Both houses keep
-- their own copy, in their own Lists, and send each other signed, ordered
-- changes; each house checks and applies every change itself, the way a
-- turn-based activity will later (one small shared-state layer, owner
-- decision 9).
--
-- friend_houses_spaces, one row per shared thing:
--   id            16 random bytes chosen by the house that shared it.
--   kind          'list' (more kinds later).
--   house_key     the friend house it is shared with.
--   friend_id     that friend while it is one; NULL once removed.
--   state         'invited' (they shared it; an admin here hasn't
--                 accepted), 'active', 'left' (either side stopped
--                 sharing: the copy here stays an ordinary list).
--   origin        'us' | 'them'.
--   title         what the sharing house called it (shown as theirs).
--   local_list_id the list it is linked to here. Changes from the friend
--                 only ever touch this one list (finding 33: remote ids
--                 map through a local table, never straight to lists.id).
--   clock         the Lamport clock: one past the highest seen or sent.
--   synced_at     the list's updated_at when this house last compared it.
--
-- friend_houses_space_ops, every change in or out (the log):
--   author        the house key that made it; with op_id it de-duplicates.
--   lamport       its clock; changes apply in (lamport, author) order.
--   op            'share' | 'add' | 'text' | 'done' | 'remove' | 'leave'.
--   item_uid      the shared item it is about (16 random bytes).
--   value         the text, or 'true'/'false' for done.
--   envelope      out: the signed envelope (a batch of changes) to send.
--   state         out: 'queued' | 'sent'.  in: 'applied' | 'waiting'
--                 (arrived before an admin accepted the share).
--
-- friend_houses_space_items, the shared state of each item (last writer
-- wins per field, by (lamport, author)), and which item here it is:
--   uid, local_item_id, text, done, removed,
--   text_at/text_by, done_at/done_by, removed_at: each field's winning stamp.
--
-- list_items.added_via gains 'friend': an item a friend house added.
--
-- IF NOT EXISTS throughout. No semicolon or double dash inside a
-- statement: the test kits split this file naively.

ALTER TABLE list_items DROP CONSTRAINT IF EXISTS list_items_added_via_check;
ALTER TABLE list_items ADD CONSTRAINT list_items_added_via_check
    CHECK (added_via IN ('voice', 'screen', 'friend'));

CREATE TABLE IF NOT EXISTS friend_houses_spaces (
    id             BYTEA PRIMARY KEY CHECK (octet_length(id) = 16),
    kind           TEXT NOT NULL CHECK (kind IN ('list')),
    house_key      BYTEA NOT NULL CHECK (octet_length(house_key) = 32),
    friend_id      TEXT REFERENCES friend_houses_friends (id) ON DELETE SET NULL,
    state          TEXT NOT NULL CHECK (state IN ('invited', 'active', 'left')),
    origin         TEXT NOT NULL CHECK (origin IN ('us', 'them')),
    title          TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 60),
    local_list_id  INTEGER REFERENCES lists (id) ON DELETE SET NULL,
    clock          BIGINT NOT NULL DEFAULT 1,
    synced_at      TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS friend_houses_spaces_list_idx
    ON friend_houses_spaces (local_list_id) WHERE local_list_id IS NOT NULL AND state = 'active';

CREATE TABLE IF NOT EXISTS friend_houses_space_ops (
    id          BIGSERIAL PRIMARY KEY,
    space_id    BYTEA NOT NULL REFERENCES friend_houses_spaces (id) ON DELETE CASCADE,
    direction   TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    author      BYTEA NOT NULL CHECK (octet_length(author) = 32),
    op_id       BYTEA NOT NULL CHECK (octet_length(op_id) = 16),
    lamport     BIGINT NOT NULL,
    op          TEXT NOT NULL CHECK (op IN ('share', 'add', 'text', 'done', 'remove', 'leave')),
    item_uid    BYTEA CHECK (item_uid IS NULL OR octet_length(item_uid) = 16),
    value       TEXT,
    envelope    BYTEA,
    msg_id      BYTEA,
    state       TEXT NOT NULL CHECK (state IN ('queued', 'sent', 'applied', 'waiting')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (space_id, author, op_id)
);

CREATE INDEX IF NOT EXISTS friend_houses_space_ops_queued_idx
    ON friend_houses_space_ops (state) WHERE state IN ('queued', 'waiting');

CREATE TABLE IF NOT EXISTS friend_houses_space_items (
    space_id       BYTEA NOT NULL REFERENCES friend_houses_spaces (id) ON DELETE CASCADE,
    uid            BYTEA NOT NULL CHECK (octet_length(uid) = 16),
    local_item_id  INTEGER REFERENCES list_items (id) ON DELETE SET NULL,
    text           TEXT NOT NULL,
    done           BOOLEAN NOT NULL DEFAULT FALSE,
    removed        BOOLEAN NOT NULL DEFAULT FALSE,
    text_at        BIGINT NOT NULL,
    text_by        BYTEA NOT NULL,
    done_at        BIGINT NOT NULL DEFAULT 0,
    done_by        BYTEA NOT NULL DEFAULT '\x'::bytea,
    removed_at     BIGINT,
    PRIMARY KEY (space_id, uid)
);
