-- V019 — "Also called": other names for library artists, albums and songs,
-- and the record of which artists have been looked up on MusicBrainz.
--
-- Spoken requests are matched against the library by how names SOUND
-- (domovoi/handlers/shared/spoken_names.py, library_match.py). Some names no
-- rule can reach: SBTRKT is said "subtract", ALLEYCVT "alley cat", and
-- Whisper consistently hears one artist as something else entirely. The
-- household fixes those by teaching a name — on the track drawer's "also
-- called" lists, or by voice ("when I say subtract I mean SBTRKT") — and,
-- if it opts in, Domovoi fetches spoken aliases from MusicBrainz.
--
-- Two tables:
--
-- * library_aliases — one row per (name → thing it means). A target can
--   have ANY number of names (owner decision 2026-10-02: "there may be many
--   names a band could be called"); a NAME means exactly one thing in the
--   household, so `alias_key` is unique across every source. alias_key is
--   spoken_names.alias_key(alias) — "Subtract", "subtract!" and "SUBTRACT"
--   are one alias — and `key_version` records which version of that
--   function computed it (a future change re-keys the rows that are behind).
--   A household alias (manual = dashboard / app, voice) that would take a
--   key someone else holds is a question, never a silent second meaning:
--   the API answers 409 and the dashboard or the voice turn asks "replace
--   it?". MusicBrainz rows are written with ON CONFLICT (alias_key) DO
--   NOTHING, so they never displace a household alias; a household member
--   who removes one leaves it `suppressed` (kept so the next top-up does
--   not add it back), and a household alias may later take its key over.
--
--   Targets: an artist or album by the alias_key of its library name
--   (`target_key`; an album may also be pinned to its artist with
--   `target_artist_key`), a song by its library_tracks row
--   (`target_track_id`, deleted with the track). An artist target whose key
--   matches no single performer but a whole multi-artist credit ("Earth,
--   Wind & Fire") means that credit. A target that has left the library
--   keeps its rows; they match nothing until it comes back.
--
--   Who added it: `created_by_kind` admin (an admin Bearer), device (a
--   paired dashboard or phone, `created_by_device` = the self-asserted
--   devices.device_id when it sent one), voice (`created_by_room`, and
--   `created_by_person` when the speaker was recognized), or system (the
--   MusicBrainz fetch). Any paired household device may add; only an admin
--   may remove or replace a household alias that someone else added. Device
--   ids are self-asserted on a trusted LAN, so this is household policy,
--   not a security boundary — the same posture as queue_device_blocks (V010).
--
-- * library_alias_lookups — one row per library artist (by alias_key of its
--   name) the MusicBrainz fetch has asked about, and how that went. Counts
--   only: the names the real-name filter dropped are never stored.
--
-- Written by the web process (dashboard / app adds and removals) and the
-- core (voice adds, the MusicBrainz worker); read by the core's resolver.
-- IF NOT EXISTS throughout so a database patched by hand ahead of Flyway (a
-- test lane) still takes this migration cleanly.

CREATE TABLE IF NOT EXISTS library_aliases (
    id                 BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    alias              TEXT        NOT NULL,              -- as typed or heard ("subtract")
    alias_key          TEXT        NOT NULL,              -- spoken_names.alias_key(alias)
    key_version        SMALLINT    NOT NULL DEFAULT 1,    -- spoken_names.KEY_VERSION used
    target_type        TEXT        NOT NULL,              -- artist | album | track
    target_key         TEXT,                              -- artist / album: alias_key(library name)
    target_name        TEXT        NOT NULL,              -- the library spelling, for display
    target_artist_key  TEXT,                              -- album only: alias_key(its artist); NULL = any
    target_track_id    INTEGER     REFERENCES library_tracks(id) ON DELETE CASCADE,  -- track only
    source             TEXT        NOT NULL,              -- manual | voice | musicbrainz
    created_by_kind    TEXT        NOT NULL,              -- admin | device | voice | system
    created_by_device  TEXT,                              -- devices.device_id (soft ref, like V010)
    created_by_room    TEXT,                              -- voice: the room it was heard in
    created_by_person  INTEGER     REFERENCES people(id) ON DELETE SET NULL,  -- voice: who said it
    mb_artist_id       TEXT,                              -- musicbrainz: the artist it came from
    mb_alias_type      TEXT,                              -- musicbrainz: Artist name | Search hint | sort name | NULL
    suppressed         BOOLEAN     NOT NULL DEFAULT FALSE,  -- a removed musicbrainz alias
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT library_aliases_target_type_chk
        CHECK (target_type IN ('artist', 'album', 'track')),
    CONSTRAINT library_aliases_source_chk
        CHECK (source IN ('manual', 'voice', 'musicbrainz')),
    CONSTRAINT library_aliases_created_by_kind_chk
        CHECK (created_by_kind IN ('admin', 'device', 'voice', 'system')),
    CONSTRAINT library_aliases_target_chk CHECK (
        (target_type = 'track' AND target_track_id IS NOT NULL AND target_key IS NULL)
        OR (target_type <> 'track' AND target_track_id IS NULL AND target_key IS NOT NULL)
    ),
    CONSTRAINT library_aliases_album_artist_chk
        CHECK (target_artist_key IS NULL OR target_type = 'album'),
    CONSTRAINT library_aliases_length_chk
        CHECK (char_length(alias) BETWEEN 1 AND 120 AND char_length(alias_key) BETWEEN 1 AND 240),
    CONSTRAINT library_aliases_suppressed_chk
        CHECK (NOT suppressed OR source = 'musicbrainz')
);

-- One meaning per name, whatever its source.
CREATE UNIQUE INDEX IF NOT EXISTS uq_library_aliases_alias_key
    ON library_aliases (alias_key);
-- "What else is <band> called?" and the drawer's lists.
CREATE INDEX IF NOT EXISTS idx_library_aliases_target
    ON library_aliases (target_type, target_key);
CREATE INDEX IF NOT EXISTS idx_library_aliases_track
    ON library_aliases (target_track_id)
    WHERE target_track_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS library_alias_lookups (
    artist_key       TEXT        PRIMARY KEY,           -- alias_key of the library artist name
    artist_name      TEXT        NOT NULL,              -- the library spelling that was searched
    status           TEXT        NOT NULL,              -- matched | no_match | ambiguous | error
    mb_artist_id     TEXT,
    mb_name          TEXT,
    mb_type          TEXT,                              -- MusicBrainz artist type (Person, Group, ...)
    aliases_added    INTEGER     NOT NULL DEFAULT 0,
    aliases_dropped  INTEGER     NOT NULL DEFAULT 0,    -- by the real-name filter; counts only
    attempts         INTEGER     NOT NULL DEFAULT 1,
    last_error       TEXT,                              -- short reason code, never a payload
    looked_up_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT library_alias_lookups_status_chk
        CHECK (status IN ('matched', 'no_match', 'ambiguous', 'error'))
);

-- The worker's "what is due" scan and the dashboard's progress counts.
CREATE INDEX IF NOT EXISTS idx_library_alias_lookups_status
    ON library_alias_lookups (status, looked_up_at);
