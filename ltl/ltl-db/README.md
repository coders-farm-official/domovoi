# ltl-db

Schema for the LTL Remote control plane, in the same style as
`scooped-db`: plain SQL, BIGSERIAL keys, `idx_<table>_<column>` indexes,
and a `postgres:16-alpine` image that applies it at first boot.

| File | What it is |
|---|---|
| `init.sql` | All DDL, plus the seed plan rows. Applied automatically by the Docker image. |
| `bootstrap.sql` | `CREATE DATABASE` / `CREATE USER` / grants, for pointing the backend at a Postgres you already run. **Not** used by the Docker image — see the note below. |
| `Dockerfile` | Postgres 16 with `init.sql` mounted into `/docker-entrypoint-initdb.d`. |

## Why the bootstrap is a separate file

Scooped's `init.sql` opens with `CREATE DATABASE` / `CREATE USER`, which
works when you run it by hand against an existing server. It does not
work inside the official Postgres image: that entrypoint has already
created the database and role from `POSTGRES_DB` / `POSTGRES_USER`, and
it runs everything in `/docker-entrypoint-initdb.d` with
`ON_ERROR_STOP=1` — so `CREATE DATABASE ltldb` fails with "already
exists" and aborts the entire initialization, leaving an empty database.

Splitting them means the image path and the hand-run path both work:

```bash
# Docker (what deploy/docker-compose.yml uses)
docker compose up -d postgres

# Against a Postgres you already operate
psql -U postgres -f bootstrap.sql
psql -U ltluser -d ltldb -f init.sql
```

## Later changes

Follow the Scooped convention: one `db_update_<topic>.sql` per change,
additive, never edited after it ships. `init.sql` stays the from-scratch
definition and gains the same columns, so a fresh database and an
upgraded one end up identical.

## What is deliberately absent

No column here holds a private key, a session key, a request path, a
header, or a body. The relay forwards sealed frames and never parses
them, so there is nothing to retain — which is the point, and is why
adding such a column would be a design change rather than a schema
change. See [`../docs/SECURITY.md`](../docs/SECURITY.md).

The passwords in `bootstrap.sql` and the `Dockerfile` are local-development
defaults, chosen to match `deploy/docker-compose.yml` so a fresh checkout
runs unmodified. Override them everywhere else.
