-- V081 — Plugin repositories' pinned identity (M6, security review 2026-11-23).
--
-- Number note: V081 per the 1.5.0 coordinator (2026-11-23): V079 and V080
-- belong to other branches of the same release; this file stays the next
-- one after them when they merge.
--
-- A plugin installed from GitHub names its repository as owner/repo, and
-- that name can pass to someone else (a rename, a transfer, a deleted repo
-- registered again). GitHub's numeric repository id and owner id don't. One
-- row per plugin keeps the ids its repository had when it was pinned; the
-- update check and the update download (domovoi/plugins_runtime/
-- update_check.py) refuse a repository at that address with other ids.
--
--   slug        the plugin.
--   repo        owner/repo the ids were read for. An install from another
--               address pins that one afresh.
--   repo_id, owner_id   GitHub's numeric ids.
--   pinned_by   'install' (read when the plugin was installed or updated
--               from GitHub) or 'check' (a plugin installed before pins
--               were taken at install: its first update check), or
--               'import' (from the ~/.domovoi/plugin_repo_pins.json file
--               that held them before this table).
--   pinned_at   when.
--
-- An install from GitHub (the admin's choice on the trust screen) writes the
-- row afresh; an update from the pinned address is held to it.

CREATE TABLE IF NOT EXISTS plugin_repo_pins (
    slug        TEXT        PRIMARY KEY,
    repo        TEXT        NOT NULL,
    repo_id     BIGINT      NOT NULL,
    owner_id    BIGINT      NOT NULL,
    pinned_by   TEXT        NOT NULL CHECK (pinned_by IN ('install', 'check', 'import')),
    pinned_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
