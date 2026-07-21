"""The core-owned plugin migration runner (design §6.2).

Core keeps Flyway for its own chain; plugin migrations run through this
small Python runner instead (Flyway multi-history config is fragile):

* Ledger: ``plugin_<slug>.schema_history(version, filename, checksum,
  applied_at)`` — created by the runner alongside the schema.
* Per file: one transaction wrapping ``SET LOCAL search_path =
  plugin_<slug>, public;`` + the file's SQL + the ledger insert.
* **Both-DB application** (locked 5): prod first, then the derived
  ``_test`` DB; on a fresh install a ``_test`` failure drops the brand
  new schema on both (both-or-neither).
* **Checksum validation**: sha256 per file; an already-applied version
  whose file checksum differs refuses the catch-up (append-only, no
  down-migrations — the same discipline as core).
* **Install-time SQL lint** (:func:`sql_lint`) — regex-grade TRIPWIRE,
  not a security boundary (§7.6): rejects ``CREATE SCHEMA``,
  ``CREATE EXTENSION``, DDL naming ``public.`` or a foreign
  ``plugin_*`` schema, and cross-schema ``REFERENCES``.

Multi-statement SQL files are executed through the raw asyncpg
connection's simple-query protocol (SQLAlchemy's prepared-statement
path can't run scripts).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from domovoi.config import settings
from domovoi.plugins_runtime.manifest import MIGRATION_FILE_RE

log = logging.getLogger(__name__)


class MigrationError(RuntimeError):
    """A migration failed to apply."""


class MigrationChecksumError(MigrationError):
    """An already-applied migration file changed on disk (drift)."""


class SqlLintError(ValueError):
    """A migration file tripped the §6.2 install-time SQL lint."""


@dataclass(frozen=True)
class MigrationFile:
    version: int
    filename: str
    path: Path
    checksum: str
    sql: str


# ─── SQL lint (§6.2) ────────────────────────────────────────────────────────

_CREATE_SCHEMA_RE = re.compile(r"\bcreate\s+schema\b", re.I)
_CREATE_EXTENSION_RE = re.compile(r"\bcreate\s+extension\b", re.I)
_PUBLIC_DDL_RE = re.compile(
    r"\b(create|alter|drop)\s+(?:unique\s+|or\s+replace\s+)?"
    r"(table|index|view|materialized\s+view|sequence|type|function|trigger)\s+"
    r"(?:concurrently\s+)?(?:if\s+(?:not\s+)?exists\s+)?public\.",
    re.I,
)
_PLUGIN_SCHEMA_REF_RE = re.compile(r"\bplugin_([a-z0-9_]+)\s*\.", re.I)
_SET_SEARCH_PATH_RE = re.compile(
    r"\bset\s+(?:local\s+|session\s+)?search_path\b", re.I
)
_REFERENCES_RE = re.compile(
    r"\breferences\s+((?:\"[^\"]+\"|[a-z0-9_]+)\s*\.)", re.I
)


def _strip_sql_noise(sql: str) -> str:
    """Drop comments and string literals so the lint doesn't false-positive
    on prose (e.g. a COMMENT ON saying 'public API')."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return sql


def sql_lint(sql: str, slug: str) -> list[str]:
    """Return lint violations for one migration file (empty = clean)."""
    text = _strip_sql_noise(sql)
    errors: list[str] = []
    if _CREATE_SCHEMA_RE.search(text):
        errors.append(
            "CREATE SCHEMA is forbidden — the migration runner owns schema "
            f"plugin_{slug}"
        )
    if _CREATE_EXTENSION_RE.search(text):
        errors.append(
            "CREATE EXTENSION is forbidden — extensions are core-only "
            "(pg_trgm ships in core V001)"
        )
    if _PUBLIC_DDL_RE.search(text):
        errors.append("DDL against the public schema is forbidden")
    if _SET_SEARCH_PATH_RE.search(text):
        errors.append(
            "SET search_path is forbidden — the migration runner owns "
            f"search_path (it presets plugin_{slug}, public per file); a "
            "migration that resets it can slip unqualified DDL into another "
            "schema"
        )
    for m in _PLUGIN_SCHEMA_REF_RE.finditer(text):
        if m.group(1) != slug:
            errors.append(
                f"reference to foreign plugin schema plugin_{m.group(1)} "
                f"is forbidden"
            )
    for m in _REFERENCES_RE.finditer(text):
        target_schema = m.group(1).rstrip(". \t").strip('"')
        if target_schema != f"plugin_{slug}":
            errors.append(
                f"cross-schema REFERENCES {target_schema}.* is forbidden — "
                f"use soft refs + events (design §6.1, locked 5)"
            )
    return errors


# ─── discovery ──────────────────────────────────────────────────────────────

def discover_migrations(migrations_dir: Path) -> list[MigrationFile]:
    """Read + order a plugin's migration files. Validates filename lint
    and the gapless-from-V001 rule (also enforced at manifest-dir
    validation; re-checked here because the runner is callable directly)."""
    if not migrations_dir.is_dir():
        return []
    files: list[MigrationFile] = []
    for f in sorted(migrations_dir.iterdir()):
        if f.is_dir():
            continue
        m = MIGRATION_FILE_RE.match(f.name)
        if not m:
            raise MigrationError(
                f"migration filename {f.name!r} must match V###__name.sql"
            )
        sql = f.read_text(encoding="utf-8")
        files.append(
            MigrationFile(
                version=int(m.group(1)),
                filename=f.name,
                path=f,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                sql=sql,
            )
        )
    files.sort(key=lambda mf: mf.version)
    if files and [mf.version for mf in files] != list(range(1, len(files) + 1)):
        raise MigrationError(
            "migration versions must be gapless from V001 — found "
            + ", ".join(mf.filename for mf in files)
        )
    return files


def default_database_urls() -> list[str]:
    """The design's both-DB target list: [prod, prod's ``_test`` sibling].
    When DATABASE_URL already points at a ``_test`` DB (the pytest
    harness pins it there), that single URL is the whole list — there is
    no ``_test_test`` sibling."""
    prod = settings.database_url
    head, _, dbname = prod.rpartition("/")
    dbname_only, _, query = dbname.partition("?")
    if dbname_only.endswith("_test"):
        return [prod]
    test = f"{head}/{dbname_only}_test" + (f"?{query}" if query else "")
    return [prod, test]


# ─── the runner ─────────────────────────────────────────────────────────────

class PluginMigrationRunner:
    """Applies one plugin's chain to one or more databases."""

    def __init__(
        self,
        slug: str,
        migrations_dir: Path,
        *,
        database_urls: list[str] | None = None,
    ) -> None:
        self.slug = slug
        self.schema = f"plugin_{slug}"
        self.migrations_dir = migrations_dir
        self.database_urls = database_urls or default_database_urls()

    # -- public API ---------------------------------------------------------

    def lint_all(self) -> None:
        """Run the §6.2 SQL lint over every file; raise on any violation."""
        for mf in discover_migrations(self.migrations_dir):
            violations = sql_lint(mf.sql, self.slug)
            if violations:
                raise SqlLintError(
                    f"{mf.filename}: " + "; ".join(violations)
                )

    async def schema_exists(self, url: str | None = None) -> bool:
        engine = create_async_engine(
            url or self.database_urls[0], poolclass=NullPool
        )
        try:
            async with engine.connect() as conn:
                raw = await conn.get_raw_connection()
                row = await raw.driver_connection.fetchrow(
                    "SELECT 1 FROM information_schema.schemata "
                    "WHERE schema_name = $1",
                    self.schema,
                )
                return row is not None
        finally:
            await engine.dispose()

    async def apply_all(self) -> dict[str, list[str]]:
        """Apply pending migrations to every target DB (prod first, then
        ``_test``). Fresh-install both-or-neither (§3.2 step 10): when the
        schema did not exist on the PRIMARY DB before this run, any
        failure anywhere drops the brand-new schema on every target (no
        user data can exist in it). On catch-up (schema pre-existed —
        possibly with user data) failures abort without dropping:
        already-applied files stay applied (append-only discipline).
        Returns {url: [applied filenames]}."""
        files = discover_migrations(self.migrations_dir)
        if not files:
            return {}
        self.lint_all()
        primary_fresh = not await self.schema_exists(self.database_urls[0])
        applied: dict[str, list[str]] = {}
        try:
            for url in self.database_urls:
                applied[url] = await self._apply_to(url, files)
        except Exception:
            if primary_fresh:
                for url in self.database_urls:
                    try:
                        await self.drop_schema(url)
                    except Exception as drop_err:  # pragma: no cover
                        log.error(
                            "rollback DROP SCHEMA %s on %s failed: %s",
                            self.schema, url, drop_err,
                        )
            raise
        return applied

    async def ledger_max_version(self, url: str | None = None) -> int:
        """Highest applied version on the (primary) DB; 0 when the schema
        or ledger doesn't exist. The §3.6 downgrade hard barrier reads this."""
        engine = create_async_engine(
            url or self.database_urls[0], poolclass=NullPool
        )
        try:
            async with engine.connect() as conn:
                raw = await conn.get_raw_connection()
                driver = raw.driver_connection
                row = await driver.fetchrow(
                    "SELECT to_regclass($1)", f"{self.schema}.schema_history"
                )
                if row is None or row[0] is None:
                    return 0
                row = await driver.fetchrow(
                    f'SELECT COALESCE(MAX(version), 0) FROM "{self.schema}".schema_history'
                )
                return int(row[0])
        finally:
            await engine.dispose()

    async def drop_schema(self, url: str | None = None) -> None:
        """``DROP SCHEMA IF EXISTS plugin_<slug> CASCADE`` — uninstall-purge
        and fresh-install rollback. Applied to one URL, or all targets."""
        urls = [url] if url else self.database_urls
        for u in urls:
            engine = create_async_engine(u, poolclass=NullPool)
            try:
                async with engine.connect() as conn:
                    raw = await conn.get_raw_connection()
                    driver = raw.driver_connection
                    # Raw-driver execute runs in autocommit (no BEGIN issued).
                    await driver.execute(
                        f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE'
                    )
            finally:
                await engine.dispose()

    # -- internals ----------------------------------------------------------

    async def _apply_to(self, url: str, files: list[MigrationFile]) -> list[str]:
        """Apply pending files to one DB. Returns the applied filenames.
        Raises MigrationChecksumError on drift."""
        engine = create_async_engine(url, poolclass=NullPool)
        applied_names: list[str] = []
        try:
            async with engine.connect() as conn:
                raw = await conn.get_raw_connection()
                driver = raw.driver_connection

                # Schema + ledger, idempotent, own transaction.
                await driver.execute(
                    f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"'
                )
                await driver.execute(
                    f'CREATE TABLE IF NOT EXISTS "{self.schema}".schema_history ('
                    "version INT PRIMARY KEY, filename TEXT NOT NULL, "
                    "checksum TEXT NOT NULL, "
                    "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                )

                ledger = {
                    int(r["version"]): (r["filename"], r["checksum"])
                    for r in await driver.fetch(
                        f'SELECT version, filename, checksum FROM '
                        f'"{self.schema}".schema_history'
                    )
                }

                for mf in files:
                    if mf.version in ledger:
                        _, applied_checksum = ledger[mf.version]
                        if applied_checksum != mf.checksum:
                            raise MigrationChecksumError(
                                f"{self.slug}: applied migration {mf.filename} "
                                f"differs from the file on disk (ledger "
                                f"{applied_checksum[:12]}…, file "
                                f"{mf.checksum[:12]}…) — migrations are "
                                f"append-only; refusing to load"
                            )
                        continue
                    # One transaction per file: search_path + script + ledger.
                    await driver.execute("BEGIN")
                    try:
                        await driver.execute(
                            f'SET LOCAL search_path = "{self.schema}", public'
                        )
                        await driver.execute(mf.sql)
                        await driver.execute(
                            f'INSERT INTO "{self.schema}".schema_history '
                            f"(version, filename, checksum) VALUES ($1, $2, $3)",
                            mf.version, mf.filename, mf.checksum,
                        )
                        await driver.execute("COMMIT")
                    except BaseException:
                        await driver.execute("ROLLBACK")
                        raise
                    applied_names.append(mf.filename)
                    log.info(
                        "plugin %s: applied %s on %s",
                        self.slug, mf.filename, url.rsplit("/", 1)[-1],
                    )
            return applied_names
        finally:
            await engine.dispose()
