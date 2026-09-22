"""The core-owned plugin migration runner (design §6.2).

Core keeps Flyway for its own chain; plugin migrations run through this
small Python runner instead (Flyway multi-history config is fragile):

* Ledger: ``plugin_<slug>.schema_history(version, filename, checksum,
  applied_at)`` — created by the runner alongside the schema.
* **A role per plugin.** The runner creates a ``NOLOGIN`` role named
  ``plugin_<slug>`` (idempotent; the application's database user must be
  a superuser or hold ``CREATEROLE``), grants it ``USAGE`` on ``public``
  and ``ALL`` on the plugin's own schema, and makes the application user
  a member so it can switch to it and, later, use what the migration
  created. Objects a migration creates are owned by that role.
* Per file: one transaction wrapping ``SET LOCAL search_path =
  "plugin_<slug>"`` (the plugin schema ONLY — an unqualified name that
  does not exist there is an error, never a fall-through to ``public``)
  + ``SET LOCAL ROLE plugin_<slug>`` + the file's SQL + ``RESET ROLE`` +
  the ledger insert. The role holds no privilege on core tables, cannot
  ``COPY`` to a program or file, alter the server, or create roles;
  the search path keeps a plugin's own unqualified names honest.
* Objects an earlier runner (or a hand-applied fix) left owned by the
  application user are re-owned to the plugin role before a catch-up
  runs, so ``ALTER TABLE`` on a shipped table keeps working.
* **Both-DB application** (locked 5): prod first, then the derived
  ``_test`` DB; on a fresh install a ``_test`` failure drops the brand
  new schema on both (both-or-neither).
* **Checksum validation**: sha256 per file; an already-applied version
  whose file checksum differs refuses the catch-up (append-only, no
  down-migrations — the same discipline as core).
* **Install-time SQL lint** (:func:`sql_lint`) — regex-grade TRIPWIRE
  in front of the role (§7.6): rejects ``CREATE SCHEMA``,
  ``CREATE EXTENSION``, DDL or DML naming ``public.`` or a foreign
  ``plugin_*`` schema, cross-schema ``REFERENCES``, and the statements
  that would step outside the migration's role or path — ``SET/RESET
  ROLE``, ``SET SESSION AUTHORIZATION``, ``SET search_path`` /
  ``set_config``, ``DO`` blocks, ``COPY``, ``ALTER SYSTEM``,
  ``CREATE/ALTER/DROP ROLE``, ``LOAD``.

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
_PUBLIC = r'(?:public|"public")\.'
_PUBLIC_DDL_RE = re.compile(
    r"\b(create|alter|drop)\s+(?:unique\s+|or\s+replace\s+)?"
    r"(table|index|view|materialized\s+view|sequence|type|function|"
    r"procedure|routine|trigger|policy|rule|domain|aggregate|operator)\s+"
    r"(?:concurrently\s+)?(?:if\s+(?:not\s+)?exists\s+)?(?:only\s+)?" + _PUBLIC,
    re.I,
)
# DML naming a core table explicitly — an unqualified name cannot reach
# public (the runner pins search_path to the plugin schema), and the role
# has no privilege on core tables, but say so up front.
_PUBLIC_DML_RE = re.compile(
    r"\b(insert\s+into|update|delete\s+from|truncate(?:\s+table)?|"
    r"merge\s+into|lock\s+table|copy)\s+(?:only\s+)?" + _PUBLIC,
    re.I,
)
_PLUGIN_SCHEMA_REF_RE = re.compile(r"\bplugin_([a-z0-9_]+)\s*\.", re.I)
_SET_SEARCH_PATH_RE = re.compile(
    r"\bset\s+(?:local\s+|session\s+)?search_path\b", re.I
)
_SET_CONFIG_RE = re.compile(r"\bset_config\s*\(", re.I)
_REFERENCES_RE = re.compile(
    r"\breferences\s+((?:\"[^\"]+\"|[a-z0-9_]+)\s*\.)", re.I
)
# Statements that would step outside the migration's role or its pinned
# path, or reach the server itself. Checked at STATEMENT START (after a
# ';' or at file start) so a column merely named ``copy`` passes.
_FORBIDDEN_STATEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*do\b", re.I),
     "DO blocks are forbidden — a migration is plain DDL/DML in its own "
     "schema, not procedural code"),
    (re.compile(r"^\s*copy\b", re.I),
     "COPY is forbidden — it reads or writes files and programs on the "
     "database host"),
    (re.compile(r"^\s*alter\s+system\b", re.I),
     "ALTER SYSTEM is forbidden — a migration never changes server "
     "configuration"),
    (re.compile(r"^\s*(create|alter|drop)\s+(role|user|group)\b", re.I),
     "CREATE/ALTER/DROP ROLE is forbidden — the migration runner owns the "
     "plugin role"),
    (re.compile(r"^\s*(set|reset)\s+(local\s+|session\s+)?role\b", re.I),
     "SET/RESET ROLE is forbidden — each migration file runs as its own "
     "plugin role and may not switch out of it"),
    (re.compile(r"^\s*(set|reset)\s+(local\s+|session\s+)?session\s+authorization\b", re.I),
     "SET SESSION AUTHORIZATION is forbidden — each migration file runs as "
     "its own plugin role"),
    (re.compile(r"^\s*reset\s+all\b", re.I),
     "RESET ALL is forbidden — it would discard the migration's role and "
     "search_path"),
    (re.compile(r"^\s*load\b", re.I),
     "LOAD is forbidden — a migration never loads server libraries"),
    (re.compile(r"^\s*(create|alter|drop)\s+(database|tablespace)\b", re.I),
     "CREATE/ALTER/DROP DATABASE or TABLESPACE is forbidden in a plugin "
     "migration"),
)


def _strip_sql_noise(sql: str) -> str:
    """Drop comments and string literals so the lint doesn't false-positive
    on prose (e.g. a COMMENT ON saying 'public API')."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return sql


def _statements(text: str) -> list[str]:
    """Split noise-stripped SQL on ';'. Dollar-quoted bodies are split
    too — deliberately: a statement inside a function body is linted like
    a top-level one, which is stricter, never looser."""
    return [s for s in text.split(";") if s.strip()]


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
    if _PUBLIC_DML_RE.search(text):
        errors.append(
            "DML against the public schema is forbidden — a plugin owns "
            f"plugin_{slug} and nothing else"
        )
    if _SET_SEARCH_PATH_RE.search(text) or _SET_CONFIG_RE.search(text):
        errors.append(
            "SET search_path / set_config is forbidden — the migration "
            f"runner owns search_path (it pins plugin_{slug} per file); a "
            "migration that resets it can slip unqualified DDL into another "
            "schema"
        )
    seen: set[str] = set()
    for stmt in _statements(text):
        for pattern, message in _FORBIDDEN_STATEMENTS:
            if pattern.match(stmt) and message not in seen:
                seen.add(message)
                errors.append(message)
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
        # The NOLOGIN role every migration file runs as. Same spelling as
        # the schema (roles and schemas are separate namespaces).
        self.role = f"plugin_{slug}"
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
        and fresh-install rollback. Applied to one URL, or all targets.
        When every target is dropped the plugin's NOLOGIN role goes too
        (best effort: a role still owning objects elsewhere stays, and a
        leftover role is inert — no login, no privilege on anything)."""
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
                    if url is None:
                        # Drop the role's one remaining grant in this DB;
                        # after the last target nothing depends on it.
                        await self._forget_role(driver, last=(u == urls[-1]))
            finally:
                await engine.dispose()

    # -- internals ----------------------------------------------------------

    async def _apply_to(self, url: str, files: list[MigrationFile]) -> list[str]:
        """Apply pending files to one DB. Returns the applied filenames.
        Raises MigrationChecksumError on drift."""
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                raw = await conn.get_raw_connection()
                return await self._apply_files(
                    raw.driver_connection, files, label=url.rsplit("/", 1)[-1]
                )
        finally:
            await engine.dispose()

    async def _apply_files(self, driver, files: list[MigrationFile], *, label: str = "") -> list[str]:
        """The whole per-database procedure against a raw asyncpg
        connection (split from :meth:`_apply_to` so the statement
        sequence is testable without Postgres)."""
        applied_names: list[str] = []

        # Schema + ledger, idempotent, own transaction.
        await driver.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
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

        pending: list[MigrationFile] = []
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
            pending.append(mf)
        if not pending:
            return applied_names

        # The plugin role, its grants, and ownership of whatever an
        # earlier runner left behind — only when there is work to do.
        await self._ensure_role(driver)
        await self._adopt_schema_objects(driver)

        for mf in pending:
            # One transaction per file: pinned search_path (the plugin
            # schema ONLY) + the plugin role + script + back to the
            # application user for the ledger row.
            await driver.execute("BEGIN")
            try:
                await driver.execute(f'SET LOCAL search_path = "{self.schema}"')
                await driver.execute(f'SET LOCAL ROLE "{self.role}"')
                await driver.execute(mf.sql)
                await driver.execute("RESET ROLE")
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
            log.info("plugin %s: applied %s on %s", self.slug, mf.filename, label)
        return applied_names

    async def _ensure_role(self, driver) -> None:
        """Create the plugin's ``NOLOGIN`` role if it is missing, make the
        application user a member (unless it is a superuser, which may
        SET ROLE to anything), and grant the role what a migration needs:
        ``USAGE`` on ``public`` (extension operators, core functions) and
        ``ALL`` on the plugin's own schema. Idempotent; works whether the
        application user is the bootstrap superuser (docker-compose, the
        throwaway harnesses) or a ``CREATEROLE`` account."""
        row = await driver.fetchrow(
            "SELECT 1 FROM pg_roles WHERE rolname = $1", self.role
        )
        if row is None:
            try:
                await driver.execute(f'CREATE ROLE "{self.role}" NOLOGIN')
            except Exception as e:
                name = type(e).__name__
                if name == "DuplicateObjectError":
                    pass  # created concurrently — fine
                elif name == "InsufficientPrivilegeError":
                    raise MigrationError(
                        f"cannot create role {self.role}: the database user "
                        f"needs CREATEROLE (or superuser) so each plugin's "
                        f"migrations can run as their own role"
                    ) from e
                else:
                    raise
            log.info("plugin %s: created migration role %s", self.slug, self.role)
        me = await driver.fetchrow(
            "SELECT current_user AS who, "
            "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS super, "
            "pg_has_role(current_user, $1, 'MEMBER') AS member",
            self.role,
        )
        if me is not None and not me["super"] and not me["member"]:
            try:
                await driver.execute(f'GRANT "{self.role}" TO CURRENT_USER')
            except Exception as e:
                raise MigrationError(
                    f"cannot make {me['who']} a member of {self.role}: {e}"
                ) from e
        await driver.execute(f'GRANT USAGE ON SCHEMA public TO "{self.role}"')
        await driver.execute(f'GRANT ALL ON SCHEMA "{self.schema}" TO "{self.role}"')

    async def _adopt_schema_objects(self, driver) -> None:
        """Re-own every relation, routine and type in the plugin schema
        (except the runner's ledger) to the plugin role, so a catch-up
        migration can ALTER what an earlier runner created as the
        application user. No-op once everything is owned by the role."""
        rels = await driver.fetch(
            "SELECT c.relname, c.relkind::text AS relkind FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = $1 AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') "
            "AND c.relname <> 'schema_history' "
            "AND pg_get_userbyid(c.relowner) <> $2",
            self.schema, self.role,
        )
        kinds = {
            "r": "TABLE", "p": "TABLE", "v": "VIEW", "m": "MATERIALIZED VIEW",
            "S": "SEQUENCE", "f": "FOREIGN TABLE",
        }
        for r in rels:
            await driver.execute(
                f'ALTER {kinds[r["relkind"]]} "{self.schema}"."{r["relname"]}" '
                f'OWNER TO "{self.role}"'
            )
        routines = await driver.fetch(
            "SELECT p.oid::regprocedure::text AS sig FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = $1 AND pg_get_userbyid(p.proowner) <> $2",
            self.schema, self.role,
        )
        for r in routines:
            await driver.execute(f'ALTER ROUTINE {r["sig"]} OWNER TO "{self.role}"')
        types = await driver.fetch(
            "SELECT t.typname, t.typtype::text AS typtype FROM pg_type t "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "LEFT JOIN pg_class c ON c.oid = t.typrelid "
            "WHERE n.nspname = $1 AND t.typtype IN ('e', 'd', 'r', 'c') "
            "AND (t.typrelid = 0 OR c.relkind = 'c') "
            "AND pg_get_userbyid(t.typowner) <> $2",
            self.schema, self.role,
        )
        for r in types:
            keyword = "DOMAIN" if r["typtype"] == "d" else "TYPE"
            await driver.execute(
                f'ALTER {keyword} "{self.schema}"."{r["typname"]}" OWNER TO "{self.role}"'
            )
        if rels or routines or types:
            log.info(
                "plugin %s: re-owned %d relation(s), %d routine(s), %d type(s) to %s",
                self.slug, len(rels), len(routines), len(types), self.role,
            )

    async def _forget_role(self, driver, *, last: bool) -> None:
        """Revoke the role's ``USAGE`` on ``public`` in this DB (the only
        thing that still depends on it once the schema is gone) and, after
        the last target, drop the role. Best effort throughout: a role that
        still owns something elsewhere, or that this user may not drop,
        stays — and a leftover role is inert (no login, no privileges)."""
        row = await driver.fetchrow(
            "SELECT 1 FROM pg_roles WHERE rolname = $1", self.role
        )
        if row is None:
            return
        try:
            await driver.execute(f'REVOKE ALL ON SCHEMA public FROM "{self.role}"')
        except Exception as e:
            log.info("plugin %s: revoke on public for %s skipped (%s)", self.slug, self.role, e)
        if not last:
            return
        try:
            await driver.execute(f'DROP ROLE IF EXISTS "{self.role}"')
        except Exception as e:  # owns objects in another DB, or no privilege
            log.info("plugin %s: role %s kept (%s)", self.slug, self.role, e)
