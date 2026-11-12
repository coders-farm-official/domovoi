-- V048 — Household lists: to-do, shopping, and any list the house names.
--
-- "Add tarp the trailer to my to-do list" had nowhere to go: no handler took
-- it, the answer model said it was done, and nothing was kept. These two
-- tables are the lists the voice handler (domovoi/handlers/lists.py), the
-- dashboard's Lists page and the apps (web/backend/api/lists.py) share.
--
-- Lists are the HOUSEHOLD's, like the calendar: every voice and every paired
-- device reads and changes the same lists. "My to-do list" and "the to-do
-- list" are one list. Nobody owns a list; an item remembers who added it
-- when that is known (a recognised voice, or the room it was said in).
--
-- lists
--   name        what the household calls it, as shown ("To-do", "Camping")
--   name_key    the name folded for matching (domovoi/lists.py name_key):
--               lowercase letters and digits only, with "todo"/"to do"/
--               "to-do" and "grocery"/"groceries" folded onto the two
--               built-in lists. Unique, so "Groceries" can't be made beside
--               "Shopping", and voice finds a list by what it heard
--   position    the order the lists are shown in (lowest first)
--
-- list_items
--   text        the item, as said or typed ("Tarp the trailer")
--   done        ticked off; done_at is when (NULL while open)
--   position    the order inside its list (lowest first). New items go to
--               the end. Gaps are fine
--   added_by_person_id  the recognised voice that added it (NULL: unknown,
--               or typed on a screen); SET NULL when the person is deleted
--   added_by_room       the room a voice add came from (a satellite's room
--               id; NULL for the dashboard and the apps)
--   added_via   'voice' or 'screen'
--
-- Deleting a list deletes its items (ON DELETE CASCADE).
--
-- Two lists exist from the start, so "add milk to the shopping list" works
-- on a new house with nothing set up: To-do and Shopping. They are ordinary
-- rows: a household may rename or delete them, and nothing puts them back.
-- (V047 wake synthetic-only came before this.)

CREATE TABLE IF NOT EXISTS lists (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 60),
    name_key    TEXT NOT NULL UNIQUE CHECK (name_key ~ '^[a-z0-9]{1,60}$'),
    position    INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS list_items (
    id                  SERIAL PRIMARY KEY,
    list_id             INT NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    text                TEXT NOT NULL CHECK (char_length(btrim(text)) BETWEEN 1 AND 200),
    done                BOOLEAN NOT NULL DEFAULT FALSE,
    done_at             TIMESTAMPTZ,
    position            INT NOT NULL DEFAULT 0,
    added_by_person_id  INT REFERENCES people(id) ON DELETE SET NULL,
    added_by_room       TEXT,
    added_via           TEXT NOT NULL DEFAULT 'screen'
                        CHECK (added_via IN ('voice', 'screen')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT list_items_done_at_chk CHECK (done OR done_at IS NULL)
);

CREATE INDEX IF NOT EXISTS idx_list_items_list_position ON list_items (list_id, position, id);

INSERT INTO lists (name, name_key, position) VALUES
    ('To-do', 'todo', 1),
    ('Shopping', 'shopping', 2)
ON CONFLICT (name_key) DO NOTHING;
