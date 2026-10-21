-- V035 — The help desk: whose phone this is, and steps sent to it.
--
-- Voice "how do I…" (part 9 of the assistant plan, owner decisions
-- 2026-10-21) gives a short spoken answer, then offers "Want me to send
-- the steps to your phone?". The steps go to the ASKER only: the person
-- voice identification recognised, on the phones marked "This phone
-- belongs to <person>" in the app (Settings → Connection). Never a
-- broadcast to every phone.
--
-- ── devices.owner_person_id ────────────────────────────────────────────
--
-- The app has kept "This phone belongs to" on the phone itself, per
-- server. To know whether a person HAS a phone (and to say "there's no
-- phone marked as yours" instead of guessing), the server needs it too:
-- the app now sends it with its device registration. NULL: nobody in
-- particular (every existing row). Self-asserted, like everything else on
-- the row: it decides where help steps are offered, nothing more. ON
-- DELETE SET NULL: forgetting a person unmarks their phones.
--
-- ── help_deliveries ────────────────────────────────────────────────────
--
-- One row per "send the steps to my phone": the person they were for and
-- the home-server chat ("Help: …", household-shared like every server
-- chat, so the steps stay findable) that holds them. A phone whose owner
-- is that person posts a notification for each row it has not seen
-- (`help_deliveries.changed` on the live socket, and a catch-up read).
-- The row carries no text of its own beyond the chat's title. Deleting
-- the chat or the person removes the row.
--
-- IF NOT EXISTS throughout, so a test lane patched by hand takes it again
-- cleanly. No semicolon or double dash inside a statement: the test kits
-- split this file naively.

ALTER TABLE devices
    ADD COLUMN IF NOT EXISTS owner_person_id BIGINT REFERENCES people(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_devices_owner_person
    ON devices (owner_person_id) WHERE owner_person_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS help_deliveries (
    id          BIGSERIAL    PRIMARY KEY,
    person_id   BIGINT       NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    thread_id   BIGINT       NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    title       TEXT         NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT help_deliveries_title_chk CHECK (char_length(title) BETWEEN 1 AND 200)
);

CREATE INDEX IF NOT EXISTS idx_help_deliveries_person
    ON help_deliveries (person_id, id);
