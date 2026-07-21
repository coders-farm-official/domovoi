-- Radio plugin V001 — full schema in plugin_radio (design §9.2).
--
-- The migration runner executes this with
-- ``SET LOCAL search_path = plugin_radio, public`` so every unqualified
-- name below lands in the plugin's own schema. Rules honored here
-- (design §6.1/§6.2, locked 5):
--
--   * NO foreign keys into core (public) tables. ``library_track_id``
--     is a SOFT reference (bare BIGINT) cleaned up by the plugin's
--     event subscriptions + the reaper's reconciliation sweep.
--   * Intra-schema FKs are fine (radio_detections → radio_stations).
--   * ``fingerprint_source`` is app-validated ('local' | 'shazam' |
--     'icy'), not a CHECK — a CHECK here churned once already
--     (adding 'icy') and the value set is plugin-owned.
--   * ``source`` keeps its CHECK: 'online' | 'fm' is a closed,
--     plugin-owned set that drives branching logic.

-- Favoritable internet + FM stations. Internet stations come from
-- radio-browser.info searches; FM stations from the FCC FM Query bulk
-- import. One table, `source` distinguishes them — the favorite/sample
-- surfaces are otherwise identical.
CREATE TABLE radio_stations (
    id                     BIGSERIAL PRIMARY KEY,
    name                   TEXT NOT NULL,
    source                 TEXT NOT NULL CHECK (source IN ('online', 'fm')),
    stream_url             TEXT,                 -- NULL for FM without an online simulcast
    frequency_mhz          NUMERIC(5,1),         -- NULL for non-FM; handler SQL casts
                                                 -- bound floats to ::numeric(5,1) to match
    market_city            TEXT,                 -- FCC market for FM
    market_state           TEXT,                 -- 2-letter state code
    call_sign              TEXT,                 -- e.g. 'KEXP'
    external_id            TEXT,                 -- radio-browser stationuuid OR FCC facility id
    country_code           TEXT,
    language               TEXT,
    tags                   TEXT[],
    favorited              BOOLEAN NOT NULL DEFAULT FALSE,
    sample_interval_sec    INTEGER NOT NULL DEFAULT 180,
    last_sampled_at        TIMESTAMPTZ,
    -- ICY metadata cache. ``icy_supported`` is a tristate:
    -- NULL = never probed, TRUE = StreamTitle observed at least once,
    -- FALSE = several consecutive polls without an icy-metaint header.
    -- The poller's in-memory miss counter feeds the flip; see
    -- workers/icy_poller.py for the single-process assumption.
    now_playing            TEXT,
    now_playing_updated_at TIMESTAMPTZ,
    icy_supported          BOOLEAN,
    last_icy_poll_at       TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- What the identify chain (local fingerprints → Shazam) or the ICY
-- metadata channel heard on a station at a given moment.
CREATE TABLE radio_detections (
    id                  BIGSERIAL PRIMARY KEY,
    station_id          BIGINT NOT NULL REFERENCES radio_stations(id) ON DELETE CASCADE,
    artist              TEXT,
    title               TEXT,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fingerprint_source  TEXT NOT NULL,      -- app-validated: 'local' | 'shazam' | 'icy'
    in_library          BOOLEAN NOT NULL DEFAULT FALSE,
    -- SOFT ref → public.library_tracks, stamped by the tier-1 local
    -- fingerprint match at detection time; nulled by the
    -- core.library_track_deleted subscription + reconciliation sweep.
    library_track_id    BIGINT
);

-- Landmark hashes for every library track (owned WHOLLY by this
-- plugin — locked 8).
-- ``library_track_id`` is a SOFT ref → public.library_tracks.
CREATE TABLE track_fingerprints (
    id                BIGSERIAL PRIMARY KEY,
    library_track_id  BIGINT NOT NULL,
    hash              BYTEA NOT NULL,
    offset_ms         INTEGER NOT NULL DEFAULT 0,
    fingerprinted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (library_track_id, hash, offset_ms)
);

-- Indexes for hot query paths.

CREATE INDEX idx_radio_detections_station_time
    ON radio_detections (station_id, detected_at DESC);

-- Retention reaper + cursor-paginated feeds scan by time.
CREATE INDEX idx_radio_detections_detected
    ON radio_detections (detected_at);

-- Partial: the sampler / poller only ever query favorited stations.
CREATE INDEX idx_radio_stations_favorited
    ON radio_stations (favorited, source) WHERE favorited;

-- Partial: external_id is the radio-browser/FCC dedup key on import.
-- NOT unique — radio-browser rows can persist with NULL external_id.
CREATE INDEX idx_radio_stations_external
    ON radio_stations (external_id) WHERE external_id IS NOT NULL;

-- "Play 97.5 fm" — voice-command market lookup path.
CREATE INDEX idx_radio_stations_market_freq
    ON radio_stations (market_city, frequency_mhz) WHERE source = 'fm';

-- ICY poller due-station SELECT. The condition matches the WHERE clause
-- in workers/icy_poller.py::_select_due_stations TEXTUALLY — keep the
-- two in lockstep or the planner stops using this index.
CREATE INDEX idx_radio_stations_icy_due
    ON radio_stations (last_icy_poll_at NULLS FIRST, id)
    WHERE favorited
      AND stream_url IS NOT NULL
      AND (icy_supported IS NULL OR icy_supported = TRUE);

-- HASH index on the fingerprint bytes — the matcher looks up
-- ``WHERE hash = ANY(:hashes)`` (straight equality).
CREATE INDEX idx_track_fingerprints_hash
    ON track_fingerprints USING HASH (hash);

CREATE INDEX idx_track_fingerprints_track
    ON track_fingerprints (library_track_id);
