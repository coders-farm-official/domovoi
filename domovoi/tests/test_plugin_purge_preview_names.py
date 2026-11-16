"""The uninstall dialog's purge preview reads table names a plugin chose.

``GET /api/plugins/{slug}/purge-preview`` counts the rows of every table in
``plugin_<slug>``, and those names come from the plugin's own migrations.
It used to splice each into ``SELECT COUNT(*) FROM "<schema>"."<name>"``
as written, so a table named with a double quote ended the identifier and
the rest of its name ran as SQL — outside the migration runner's role and
search path, as the dashboard's own database user, the moment an owner
opened the dialog (CodeQL py/sql-injection, 2026-11). The names are now
quoted as identifiers, and the crafted one is counted like any other.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi.db.session import engine
from domovoi.tests.conftest import requires_db
from web.backend.api import plugins as plugins_api

SLUG = "purgeprobe"
SCHEMA = f"plugin_{SLUG}"
# Ends the quoted name, drops COUNT's one row, and answers 4242 instead.
CRAFTED = 't" HAVING false UNION ALL SELECT 4242 --'
# A name text() would read a parameter in, and a percent sign.
ODD = "rate:limit 100%"


@pytest_asyncio.fixture
async def crafted_schema(monkeypatch):
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
        await conn.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
        await conn.execute(text(f'CREATE TABLE "{SCHEMA}".t (id int)'))
        await conn.execute(text(f'INSERT INTO "{SCHEMA}".t VALUES (1), (2)'))
        quoted = CRAFTED.replace('"', '""')
        await conn.execute(text(f'CREATE TABLE "{SCHEMA}"."{quoted}" (id int)'))
        await conn.execute(text(f'CREATE TABLE "{SCHEMA}"."rate\\:limit 100%" (id int)'))
        await conn.execute(text(f'INSERT INTO "{SCHEMA}"."rate\\:limit 100%" VALUES (7)'))
    monkeypatch.setitem(plugins_api.HOST.rows, SLUG, {"slug": SLUG})
    yield
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))


@requires_db
@pytest.mark.asyncio
async def test_a_table_name_with_a_quote_is_counted_not_run(crafted_schema) -> None:
    out = await plugins_api.purge_preview(SLUG)
    rows = {t["table"]: t["rows"] for t in out["tables"]}
    assert rows == {"t": 2, CRAFTED: 0, ODD: 1}, rows
