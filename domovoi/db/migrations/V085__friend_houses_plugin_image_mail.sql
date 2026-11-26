-- V085 — Friend Houses: image mail between plugins (1.5.0, SDK 1.18).
--
-- A plugin mail is data (body version 1) or an image (body version 2, SDK
-- 1.18 send_image: a tag and an encoded image). The outbox keeps which one
-- it sends; the inbox keeps which one it received, so the dispatcher sends
-- an image to the core's image helper (never to the plugin's decoder) and
-- the handler gets the core's own PNG (design note FRIEND-HOUSES-PLUGINS,
-- D1: the PNG is encoded on the trusted side, at most 768 px a side).
-- domovoi/friend_houses/plugin_mail.py and plugin_sender.py are the only
-- writers.
ALTER TABLE friend_houses_plugin_outbox
    ADD COLUMN IF NOT EXISTS kind SMALLINT NOT NULL DEFAULT 1 CHECK (kind IN (1, 2));

ALTER TABLE friend_houses_plugin_inbox
    ADD COLUMN IF NOT EXISTS kind SMALLINT NOT NULL DEFAULT 1 CHECK (kind IN (1, 2));
