-- Friend Houses ("serverless messaging and friend system between
-- households"), pairing v2 (1.5.0): the code both houses compute at
-- friending is 6 digits. Friends and requests made with pairing v1 keep
-- their 4-digit code, so both lengths are allowed.
ALTER TABLE friend_houses_friends DROP CONSTRAINT friend_houses_friends_code_check;
ALTER TABLE friend_houses_friends ADD CONSTRAINT friend_houses_friends_code_check
    CHECK (code IS NULL OR code ~ '^([0-9]{4}|[0-9]{6})$');

ALTER TABLE friend_houses_requests DROP CONSTRAINT friend_houses_requests_code_check;
ALTER TABLE friend_houses_requests ADD CONSTRAINT friend_houses_requests_code_check
    CHECK (code IS NULL OR code ~ '^([0-9]{4}|[0-9]{6})$');
