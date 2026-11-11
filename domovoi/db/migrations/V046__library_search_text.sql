-- V046 — The Music search reads names the way people type them.
--
-- GET /api/music/library?q= (the dashboard's and the app's library search)
-- used to be one ILIKE substring: "em know" missed "Let Em' Know", "ti"
-- missed T.I. It now matches every word of the query against one
-- normalized string per track, so the normalization lives here, in the
-- database, where an index can use it:
--
--   library_search_norm(text)  lower-case; Latin accents folded (a fixed
--                              translate() map of U+00C0-U+017F, so it is
--                              IMMUTABLE and needs no extension, whatever
--                              the database's locale); apostrophes, dots
--                              and hyphens dropped ("T.I." -> "ti",
--                              "Em'" -> "em", "Jay-Z" -> "jayz"); every
--                              other run of non-[a-z0-9] one space.
--   library_tracks.search_text the title, artist and album, each so
--                              normalized, joined by spaces. A STORED
--                              generated column: every writer of
--                              library_tracks keeps it current for free.
--   library_tracks.search_title
--                              the title alone, so normalized: ranking
--                              ("the title IS the query") reads it rather
--                              than normalizing every matching title again.
--   idx_library_tracks_search_trgm
--                              a trigram GIN index on it (pg_trgm ships in
--                              V001): word LIKE '%..%' filters and the
--                              typo fallback's word-similarity (<%) use it.
--
-- web/backend/api/music_search.py mirrors the function in Python for the
-- query side (domovoi/tests/test_music_search.py checks they agree).
-- (V045 household settings came before this.)

CREATE OR REPLACE FUNCTION library_search_norm(t text) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$
    SELECT btrim(regexp_replace(
        regexp_replace(
            lower(translate(coalesce(t, ''),
                'ÀÁÂÃÄÅÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝàáâãäåçèéêëìíîïðñòóôõöøùúûüýÿĀāĂăĄąĆćĈĉĊċČčĎďĐđĒēĔĕĖėĘęĚěĜĝĞğĠġĢģĤĥĦħĨĩĪīĬĭĮįİıĴĵĶķĸĹĺĻļĽľĿŀŁłŃńŅņŇňŌōŎŏŐőŔŕŖŗŘřŚśŜŝŞşŠšŢţŤťŦŧŨũŪūŬŭŮůŰűŲųŴŵŶŷŸŹźŻżŽžſ',
                'aaaaaaceeeeiiiidnoooooouuuuyaaaaaaceeeeiiiidnoooooouuuuyyaaaaaaccccccccddddeeeeeeeeeegggggggghhhhiiiiiiiiiijjkkqllllllllllnnnnnnoooooorrrrrrssssssssttttttuuuuuuuuuuuuwwyyyzzzzzzs')),
            '[''`´‘’.\-]', '', 'g'),
        '[^a-z0-9]+', ' ', 'g'))
$$;

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS search_text TEXT
    GENERATED ALWAYS AS (
        library_search_norm(title) || ' ' || library_search_norm(artist)
        || ' ' || library_search_norm(album)
    ) STORED;

ALTER TABLE library_tracks
    ADD COLUMN IF NOT EXISTS search_title TEXT
    GENERATED ALWAYS AS (library_search_norm(title)) STORED;

CREATE INDEX IF NOT EXISTS idx_library_tracks_search_trgm
    ON library_tracks
    USING gin (search_text gin_trgm_ops);
