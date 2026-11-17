-- Friend Houses ("serverless messaging and friend system between
-- households"), audit R6 FH-13: a friend's onion address is what it says
-- it is, so two friends could name the same one (or this house's own),
-- and Tor refuses a config with two client keys for one address, taking
-- every friend down at the next reload. The core refuses such a friend;
-- this makes the database refuse it too.
CREATE UNIQUE INDEX friend_houses_friends_onion_key_unique
    ON friend_houses_friends (onion_key);
