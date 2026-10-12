-- V027 — the owner's "say it like" list (2026-10-12, the permissive voice).
--
-- Domovoi's voices are fed by its own text-to-phoneme converter
-- (domovoi/phonemizer). An owner can tell it how to say a word: a respelling
-- in plain English ("Nguyen" = "win"), read back through the converter, or
-- the voice's own phonemes between slashes. The shipped defaults live in
-- domovoi/phonemizer/data/pronunciations_default.txt, and this table holds only
-- the owner's changes on top of them, so the effective list is the defaults
-- overlaid with these rows:
--
--   say_like set    the word is said that way (a new word, or a changed default)
--   say_like NULL   a shipped default the owner removed (only a default gets
--                   such a row: removing the owner's own word deletes its row)
--
-- `key` is the word as the converter matches it (accents stripped, lower
-- case, as domovoi.phonemizer.pronunciation_key gives it), and `word` keeps the owner's
-- spelling for display. Putting the defaults back deletes the rows of the
-- default words, so a later release's better defaults reach the box. The
-- table holds at most 500 words of the owner's plus a row per default
-- (web/backend/api/voice.py).
--
-- pronunciations_changed is one row: when the list last changed, rows
-- deleted included (the phone caches the list by it, GET /api/voice).
--
-- IF NOT EXISTS throughout, so a lane patched by hand takes it again
-- cleanly. No semicolon or double dash inside a statement: the test kits
-- split this file naively.

CREATE TABLE IF NOT EXISTS pronunciations (
    key         TEXT         PRIMARY KEY
        CONSTRAINT pronunciations_key_chk CHECK (length(key) BETWEEN 1 AND 48),
    word        TEXT         NOT NULL
        CONSTRAINT pronunciations_word_chk CHECK (length(word) BETWEEN 1 AND 48),
    say_like    TEXT
        CONSTRAINT pronunciations_say_like_chk CHECK (say_like IS NULL OR length(say_like) BETWEEN 1 AND 100),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pronunciations_changed (
    id          SMALLINT     PRIMARY KEY DEFAULT 1
        CONSTRAINT pronunciations_changed_one_row CHECK (id = 1),
    changed_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
