-- V041 — Fonts an admin added for the whole house ("More fonts").
--
-- The document and sheet editors offer a bundled set of open-licence font
-- families (web/static/fonts, listed in its manifest.json). An admin can
-- add any other family from the google/fonts repository's ofl/ and
-- apache/ folders (web/backend/api/fonts.py): the server checks the
-- family's METADATA.pb and licence file (OFL-1.1 or Apache-2.0 only),
-- downloads its files into FONTS_DIR/<slug>/ unchanged, with the licence
-- file beside them, and records the family here. Removing it deletes the
-- row and the folder.
--
--   family         the family name from METADATA.pb ("Source Sans 3")
--   slug           its google/fonts folder name ("sourcesans3"); the files
--                  are served at /fonts/<slug>/<file>
--   category       sans | serif | mono | display | handwriting
--   licence        OFL-1.1 | Apache-2.0 (nothing else is ever stored)
--   licence_file   OFL.txt | LICENSE.txt, stored beside the fonts
--   source_path    the folder it came from ("ofl/sourcesans3")
--   source_commit  the google/fonts commit it was read at
--   faces          [{file, style, weight: [lo, hi], format, bytes}]
--   bytes          the files' total size
--   fetched_at     when it was added
--
-- No person or device is recorded: a font is house-wide.
-- (V040 room battery came before this; V042 co-editing follows.)
-- IF NOT EXISTS throughout, so a test lane patched by hand takes it again
-- cleanly.

CREATE TABLE IF NOT EXISTS fonts (
    id            BIGSERIAL PRIMARY KEY,
    family        TEXT NOT NULL,
    slug          TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]{1,64}$'),
    category      TEXT NOT NULL CHECK (category IN ('sans', 'serif', 'mono', 'display', 'handwriting')),
    licence       TEXT NOT NULL CHECK (licence IN ('OFL-1.1', 'Apache-2.0')),
    licence_file  TEXT NOT NULL,
    source_path   TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    faces         JSONB NOT NULL DEFAULT '[]'::jsonb,
    bytes         BIGINT NOT NULL DEFAULT 0,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS fonts_family_lower_idx ON fonts (lower(family));
