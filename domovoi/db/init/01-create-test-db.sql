-- Postgres initdb hook: create the test database alongside the prod one
-- on first boot of a fresh data volume.
--
-- The official postgres image runs every .sql / .sh file in
-- /docker-entrypoint-initdb.d/ exactly once per volume, AS the configured
-- POSTGRES_USER, in the POSTGRES_DB. By the time this runs the prod DB
-- already exists; we just add `domovoi_test`.
--
-- Existing installations (pre-existing pgdata volume) skip this step —
-- the entrypoint only fires on initdb. To bootstrap an existing cluster,
-- run the one-liner from domovoi/README.md ("Test database setup").

CREATE DATABASE domovoi_test;
GRANT ALL PRIVILEGES ON DATABASE domovoi_test TO domovoi;
