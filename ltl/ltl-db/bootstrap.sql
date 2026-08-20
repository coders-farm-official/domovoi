-- LTL Remote — one-time bootstrap for an EXISTING PostgreSQL server.
--
-- Run this by hand, as a superuser, when pointing the backend at a
-- Postgres you already operate:
--
--   psql -U postgres -f bootstrap.sql
--   psql -U ltluser -d ltldb -f init.sql
--
-- The docker image in this directory does NOT run this file. Its
-- entrypoint already creates the database and role from POSTGRES_DB /
-- POSTGRES_USER / POSTGRES_PASSWORD, and it runs init.sql with
-- ON_ERROR_STOP=1 — so a CREATE DATABASE in that path would abort the
-- whole initialization rather than being harmlessly skipped.
--
-- Change the password before running this anywhere real. The value
-- below matches the compose default so a local checkout works
-- unmodified, which is exactly why it must not survive to production.

CREATE DATABASE ltldb;

CREATE USER ltluser WITH ENCRYPTED PASSWORD 'ltlpass';

GRANT ALL PRIVILEGES ON DATABASE ltldb TO ltluser;

-- Postgres 15+ removed the implicit CREATE on the public schema, so the
-- database-level grant above is not enough on its own. Run this part
-- while connected to ltldb:
--
--   \c ltldb
--   GRANT ALL ON SCHEMA public TO ltluser;
