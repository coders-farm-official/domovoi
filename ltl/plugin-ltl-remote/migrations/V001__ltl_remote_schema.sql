-- ltl_remote V001 — the plugin's whole schema, in plugin_ltl_remote.
--
-- The migration runner executes this with
-- ``SET LOCAL search_path = plugin_ltl_remote, public``, so every
-- unqualified name below lands in the plugin's own schema. Rules honored
-- here (Domovoi design §6.1/§6.2):
--
--   * NO foreign keys into core (public) tables — nothing here needs one.
--   * Intra-schema FKs are fine (remote_access_log → remote_devices is
--     deliberately NOT one; see the note on that table).
--   * Closed, plugin-owned value sets keep their CHECK constraints.
--   * Private key material is NEVER stored in Postgres. The household's
--     two private keys live in ~/.domovoi/plugins/data/ltl_remote/keys/
--     at mode 0600. Only public keys and fingerprints appear here.

-- ─────────────────────────────────────────────────────────────────────
-- Single-row link state. One household per Domovoi server, so this is a
-- singleton table with a CHECK rather than a settings blob — it wants
-- transactions and NOTIFY, which an .env file cannot give it.
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE link_state (
    id                   SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),

    -- Identity. The fingerprint is the string a human compares between
    -- this dashboard and their phone; it covers BOTH public keys.
    fingerprint          TEXT NOT NULL DEFAULT '',
    dh_public_key        BYTEA,
    sig_public_key       BYTEA,

    -- Pairing window. ``pairing_code`` is the plaintext the dashboard
    -- renders; it is cleared the moment the claim lands, so a claimed
    -- household is not sitting on a live code. Only its hash was ever
    -- sent to LTL.
    pairing_code         TEXT,
    pairing_code_hash    TEXT,
    pairing_expires_at   TIMESTAMPTZ,

    -- The in-flight enrollment at LTL, so a pairing survives a core
    -- restart instead of forcing the admin to re-read a fresh code.
    -- ``enrollment_token`` is short-lived (it dies with the pairing
    -- window) and is only ever used to poll one enrollment's status.
    enrollment_id        TEXT,
    enrollment_token     TEXT,

    -- Claim result.
    household_id         TEXT,
    account_label        TEXT,
    claimed_at           TIMESTAMPTZ,

    -- Live link status, written by the relay worker.
    -- App-validated rather than CHECKed: these are progress states that
    -- will grow, and a CHECK on a churning vocabulary is a migration tax.
    connection_state     TEXT NOT NULL DEFAULT 'idle',
    last_connected_at    TIMESTAMPTZ,
    last_disconnected_at TIMESTAMPTZ,
    last_error           TEXT,

    -- Mirrored from the relay's ``quota`` control frames so the
    -- dashboard can show usage without calling LTL.
    plan_code            TEXT,
    quota_used_bytes     BIGINT NOT NULL DEFAULT 0,
    quota_limit_bytes    BIGINT,
    quota_period_end     TIMESTAMPTZ,

    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The singleton exists from migration time, so every read is a plain
-- SELECT and no code path has to handle "not yet initialized".
INSERT INTO link_state (id) VALUES (1);

-- ─────────────────────────────────────────────────────────────────────
-- Devices approved to reach this house. This table is the trust anchor:
-- LTL can register a device, but only a local approval writes
-- status='approved', and only an approved row's public key is ever fed
-- to the handshake.
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE remote_devices (
    id                   BIGSERIAL PRIMARY KEY,
    device_id            TEXT NOT NULL UNIQUE,
    label                TEXT NOT NULL,
    -- Uncompressed SEC1 P-256 point (65 bytes). Public half only.
    public_key           BYTEA NOT NULL,
    fingerprint          TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'approved', 'revoked')),
    registered_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at          TIMESTAMPTZ,
    revoked_at           TIMESTAMPTZ,
    last_seen_at         TIMESTAMPTZ,
    -- Coarse only. The relay reports a country, never a precise location.
    last_seen_country    TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX remote_devices_status_idx ON remote_devices (status);

-- ─────────────────────────────────────────────────────────────────────
-- What remote devices did. Deliberately metadata-only: method, path,
-- status, byte counts. No bodies, no headers, no query strings.
--
-- ``device_id`` is a SOFT reference with no FK, on purpose: revoking and
-- deleting a device must not erase the record of what it did, which is
-- the entire reason an access log exists.
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE remote_access_log (
    id             BIGSERIAL PRIMARY KEY,
    device_id      TEXT,
    at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    method         TEXT NOT NULL,
    -- Query strings are stripped before insert (they carry search terms
    -- and ids that are nobody's business after the fact).
    path           TEXT NOT NULL,
    status         INTEGER,
    outcome        TEXT NOT NULL CHECK (outcome IN ('ok', 'denied', 'error')),
    denial_code    TEXT,
    bytes_in       BIGINT NOT NULL DEFAULT 0,
    bytes_out      BIGINT NOT NULL DEFAULT 0,
    duration_ms    INTEGER
);

CREATE INDEX remote_access_log_at_idx ON remote_access_log (at DESC);
CREATE INDEX remote_access_log_device_idx ON remote_access_log (device_id, at DESC);
