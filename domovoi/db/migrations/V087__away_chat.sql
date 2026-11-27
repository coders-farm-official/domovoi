-- V087 — Away chat (1.5.0): a paired phone chats with its own house from
-- anywhere, over a Tor onion service of its own ("hs-away"), reusing the
-- Friend Houses plumbing (domovoi/away/, docs/SECURITY_PRIVACY.md "Away
-- chat").
--
-- away_phones: one row per phone that set away chat up, over the home
-- network only (POST /api/away/enroll), proven by its device key (V086).
--
--   device_id        the device, by the id its device key resolved to; the
--                    row goes with the device (ON DELETE CASCADE)
--   device_key_hash  the device key it was enrolled with: an enrolment
--                    counts only while the device still holds that key
--   tor_auth_pub     the phone's Tor client-authorization public key
--                    (base32, upper case, no padding: 52 characters); it
--                    becomes hs-away/authorized_clients/<id>.auth
--   noise_pub        the phone's Noise static public key (32 bytes); the
--                    listener refuses any handshake whose key isn't a
--                    current row's (phones.json, rewritten on each change)
--   created_at / updated_at   enrolling again replaces both keys
--
-- away_sends: chat.send's idempotency (24 hours): the same device sending
-- the same client_id again gets the first send's answer and nothing is
-- posted twice. Rows older than a day are deleted by the next send.
--
-- The house setting "Chat from away" (on by default) is a household
-- setting (V045, key 'away_chat', {"on": false} when an admin turned it
-- off); no column here.

CREATE TABLE IF NOT EXISTS away_phones (
    device_id        TEXT PRIMARY KEY REFERENCES devices (device_id) ON DELETE CASCADE,
    device_key_hash  TEXT NOT NULL,
    tor_auth_pub     TEXT NOT NULL CHECK (tor_auth_pub ~ '^[A-Z2-7]{52}$'),
    noise_pub        BYTEA NOT NULL UNIQUE CHECK (octet_length(noise_pub) = 32),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS away_sends (
    device_id   TEXT NOT NULL REFERENCES devices (device_id) ON DELETE CASCADE,
    client_id   UUID NOT NULL,
    thread_id   BIGINT NOT NULL,
    message_id  BIGINT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, client_id)
);

CREATE INDEX IF NOT EXISTS idx_away_sends_created ON away_sends (created_at);
