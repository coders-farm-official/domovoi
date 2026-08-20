-- LTL Remote Database Initialization Script
-- PostgreSQL
--
-- Conventions follow the Scooped database: BIGSERIAL primary keys,
-- VARCHAR(255) for identifiers, TIMESTAMP NOT NULL DEFAULT NOW() for
-- audit columns, snake_case names, and idx_<table>_<column> indexes.
-- The Spring entities in ltl-backend/ map onto exactly these columns.
--
-- One rule shapes the whole schema, and it is worth stating before the
-- first table: LTL never stores anything it could use to read a
-- household's traffic. Every key column here holds a PUBLIC key. There
-- is no column for a private key, a session key, or a request body,
-- because the relay never possesses one.
--
-- This file contains DDL only. Creating the database, the role, and the
-- grants lives in bootstrap.sql, because Postgres's docker entrypoint
-- runs everything in /docker-entrypoint-initdb.d with ON_ERROR_STOP=1
-- against a database it has ALREADY created — a CREATE DATABASE here
-- would error and abort the whole initialization.

-- ============================================================
-- Users — one row per Lazy Thumb Labs account
-- ============================================================
CREATE TABLE users (
    id                  BIGSERIAL       PRIMARY KEY,
    stytch_user_id      VARCHAR(255)    UNIQUE,
    email               VARCHAR(255)    NOT NULL UNIQUE,
    display_name        VARCHAR(255),
    stripe_customer_id  VARCHAR(255)    UNIQUE,
    disabled            BOOLEAN         NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP       NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Plans — rows, not enum constants, so pricing changes without a deploy
-- ============================================================
CREATE TABLE plans (
    id                  BIGSERIAL       PRIMARY KEY,
    code                VARCHAR(50)     NOT NULL UNIQUE,
    name                VARCHAR(255)    NOT NULL,
    description         TEXT,
    -- NULL for the free plan, which has no Stripe price at all.
    stripe_price_id     VARCHAR(255),
    -- The meaningful axis: relaying bytes is the only real marginal cost.
    monthly_bytes       BIGINT          NOT NULL,
    device_limit        INTEGER         NOT NULL DEFAULT 2,
    household_limit     INTEGER         NOT NULL DEFAULT 1,
    price_cents         INTEGER         NOT NULL DEFAULT 0,
    currency            VARCHAR(3)      NOT NULL DEFAULT 'usd',
    active              BOOLEAN         NOT NULL DEFAULT TRUE,
    sort_order          INTEGER         NOT NULL DEFAULT 0,
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP       NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Subscriptions — one active subscription per account
-- ============================================================
CREATE TABLE subscriptions (
    id                      BIGSERIAL       PRIMARY KEY,
    user_id                 BIGINT          NOT NULL UNIQUE
                                            REFERENCES users(id) ON DELETE CASCADE,
    plan_id                 BIGINT          NOT NULL REFERENCES plans(id),
    -- NULL while a user is on the free plan.
    stripe_subscription_id  VARCHAR(255)    UNIQUE,
    -- trialing | active | past_due | canceled — mirrors Stripe's vocabulary
    -- so a webhook maps across without a translation table.
    status                  VARCHAR(20)     NOT NULL DEFAULT 'active',
    cancel_at_period_end    BOOLEAN         NOT NULL DEFAULT FALSE,
    current_period_start    TIMESTAMP,
    current_period_end      TIMESTAMP,
    -- Set when an invoice fails. The tunnel keeps working until this
    -- passes, so a card that expires on holiday does not lock someone
    -- out of their own house.
    grace_until             TIMESTAMP,
    created_at              TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMP       NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Households — one Domovoi server, claimed by one account
-- ============================================================
CREATE TABLE households (
    id                  BIGSERIAL       PRIMARY KEY,
    user_id             BIGINT          NOT NULL
                                        REFERENCES users(id) ON DELETE CASCADE,
    -- Opaque public identifier the agent and clients address it by.
    household_uid       VARCHAR(64)     NOT NULL UNIQUE,
    name                VARCHAR(255)    NOT NULL,
    -- Reported by the server at enrollment; purely cosmetic, so a
    -- customer with two houses can tell them apart.
    hostname            VARCHAR(255),
    -- PUBLIC keys only, base64url of the uncompressed SEC1 points.
    -- dh_public_key is what clients agree with end to end;
    -- sig_public_key is what proves an agent may occupy this slot.
    dh_public_key       VARCHAR(255)    NOT NULL,
    sig_public_key      VARCHAR(255)    NOT NULL,
    -- The string a human compares between their dashboard and their
    -- phone. Stored so the web app can show it; it is derived from the
    -- two keys above, never trusted from the client.
    fingerprint         VARCHAR(64)     NOT NULL,
    -- SHA-256 of the relay bearer token. The token itself is shown to
    -- the household exactly once, at claim time, and never stored.
    relay_token_hash    VARCHAR(64)     NOT NULL UNIQUE,
    online              BOOLEAN         NOT NULL DEFAULT FALSE,
    last_seen_at        TIMESTAMP,
    agent_version       VARCHAR(50),
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP       NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Pending enrollments — the pairing window
-- ============================================================
-- The home server mints an eight-word code and sends only its HASH.
-- That ordering is the point: a dump of this table cannot be replayed
-- into a claim, because nothing here reverses to the words a user types.
CREATE TABLE pending_enrollments (
    id                  BIGSERIAL       PRIMARY KEY,
    enrollment_uid      VARCHAR(64)     NOT NULL UNIQUE,
    code_hash           VARCHAR(64)     NOT NULL UNIQUE,
    -- SHA-256 of the token the agent polls this enrollment with.
    poll_token_hash     VARCHAR(64)     NOT NULL,
    dh_public_key       VARCHAR(255)    NOT NULL,
    sig_public_key      VARCHAR(255)    NOT NULL,
    fingerprint         VARCHAR(64)     NOT NULL,
    hostname            VARCHAR(255),
    -- pending | claimed | expired
    status              VARCHAR(20)     NOT NULL DEFAULT 'pending',
    household_id        BIGINT          REFERENCES households(id) ON DELETE SET NULL,
    -- The relay token, held here only between the moment a user claims
    -- the code and the agent's next poll, then cleared. It exists
    -- because the two halves of pairing are asynchronous: the person
    -- typing the code and the server waiting for it are not in the same
    -- request, so the token has to be parked somewhere for a few
    -- seconds. It is cleared on handoff and on expiry.
    claimed_secret      VARCHAR(255),
    source_ip           VARCHAR(64),
    expires_at          TIMESTAMP       NOT NULL,
    claimed_at          TIMESTAMP,
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Devices — registered here, but APPROVED on the household's dashboard
-- ============================================================
-- `approved` in this table is a mirror for display, never an authority.
-- The home server decides, against its own database, which public key it
-- will complete a handshake with. Flipping this column grants nothing.
CREATE TABLE devices (
    id                  BIGSERIAL       PRIMARY KEY,
    household_id        BIGINT          NOT NULL
                                        REFERENCES households(id) ON DELETE CASCADE,
    device_uid          VARCHAR(64)     NOT NULL UNIQUE,
    label               VARCHAR(255)    NOT NULL,
    -- PUBLIC key, base64url uncompressed SEC1.
    public_key          VARCHAR(255)    NOT NULL,
    fingerprint         VARCHAR(64)     NOT NULL,
    platform            VARCHAR(50),
    approved            BOOLEAN         NOT NULL DEFAULT FALSE,
    registered_at       TIMESTAMP       NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMP,
    last_seen_country   VARCHAR(2),
    revoked_at          TIMESTAMP,
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP       NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Usage — one row per household per billing period
-- ============================================================
CREATE TABLE usage_periods (
    id                  BIGSERIAL       PRIMARY KEY,
    household_id        BIGINT          NOT NULL
                                        REFERENCES households(id) ON DELETE CASCADE,
    period_start        TIMESTAMP       NOT NULL,
    period_end          TIMESTAMP       NOT NULL,
    bytes_used          BIGINT          NOT NULL DEFAULT 0,
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_usage_household_period UNIQUE (household_id, period_start)
);

-- ============================================================
-- Relay sessions — connection audit, metadata only
-- ============================================================
-- Byte counts and country. No paths, no headers, no bodies: the relay
-- never parses them, so there is nothing to retain even by accident.
CREATE TABLE relay_sessions (
    id                  BIGSERIAL       PRIMARY KEY,
    household_id        BIGINT          NOT NULL
                                        REFERENCES households(id) ON DELETE CASCADE,
    -- NULL for an agent session; set for a client link.
    device_uid          VARCHAR(64),
    kind                VARCHAR(10)     NOT NULL,      -- agent | client
    ip_country          VARCHAR(2),
    bytes_in            BIGINT          NOT NULL DEFAULT 0,
    bytes_out           BIGINT          NOT NULL DEFAULT 0,
    close_reason        VARCHAR(50),
    started_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    ended_at            TIMESTAMP
);

-- ============================================================
-- Stripe events — webhook idempotency
-- ============================================================
-- Stripe retries. A retried subscription.deleted must not cancel a
-- subscription the customer has since restarted, so every event id is
-- recorded and a repeat is acknowledged without being re-applied.
CREATE TABLE stripe_events (
    id                  BIGSERIAL       PRIMARY KEY,
    event_id            VARCHAR(255)    NOT NULL UNIQUE,
    event_type          VARCHAR(100)    NOT NULL,
    status              VARCHAR(20)     NOT NULL DEFAULT 'processed',
    error_message       TEXT,
    received_at         TIMESTAMP       NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Enrollment attempts — rate limiting an unauthenticated endpoint
-- ============================================================
-- /api/v1/enroll takes no credentials by design (the server calling it
-- has not been claimed yet). Persisted rather than in-memory so a
-- restart does not reset an attacker's budget.
CREATE TABLE enrollment_attempts (
    id                  BIGSERIAL       PRIMARY KEY,
    source_ip           VARCHAR(64)     NOT NULL,
    attempted_at        TIMESTAMP       NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Indexes
-- ============================================================
CREATE INDEX idx_subscriptions_user_id          ON subscriptions(user_id);
CREATE INDEX idx_subscriptions_status           ON subscriptions(status);
CREATE INDEX idx_subscriptions_stripe_sub       ON subscriptions(stripe_subscription_id);
CREATE INDEX idx_households_user_id             ON households(user_id);
CREATE INDEX idx_households_online              ON households(online);
CREATE INDEX idx_pending_enrollments_status     ON pending_enrollments(status);
CREATE INDEX idx_pending_enrollments_expires    ON pending_enrollments(expires_at);
CREATE INDEX idx_devices_household_id           ON devices(household_id);
CREATE INDEX idx_devices_approved               ON devices(approved);
CREATE INDEX idx_usage_periods_household_id     ON usage_periods(household_id);
CREATE INDEX idx_relay_sessions_household_id    ON relay_sessions(household_id, started_at DESC);
CREATE INDEX idx_enrollment_attempts_ip         ON enrollment_attempts(source_ip, attempted_at DESC);

-- ============================================================
-- Seed plans
-- ============================================================
-- Byte allowances are the axis customers actually hit. Dashboard control
-- traffic is trivial; streaming an album from the library on a commute
-- is not, which is why the free tier is sized to prove the tunnel works
-- rather than to live on.
INSERT INTO plans (code, name, description, stripe_price_id, monthly_bytes,
                   device_limit, household_limit, price_cents, sort_order)
VALUES
  ('free', 'Free', 'Prove the tunnel works before you pay for it.',
   NULL, 2147483648, 2, 1, 0, 10),
  ('home', 'Home', 'The normal household: every device, plenty of streaming.',
   NULL, 107374182400, 10, 1, 500, 20),
  ('home_plus', 'Home Plus', 'Heavy remote media use, or more than one property.',
   NULL, 536870912000, 25, 3, 1200, 30);
