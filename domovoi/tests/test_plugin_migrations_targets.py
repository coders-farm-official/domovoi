"""Both-DB application: a failed fresh install drops the plugin schema
only on the databases where that run created it (design §3.2 step 10).

The runner applies to prod first, then its ``_test`` twin. A ``_test``
schema can already exist when prod's does not — an earlier install's
twin, or a ``_test`` restored on its own — and it may hold data the
earlier install's tests or a developer put there. Before, a failure with
prod fresh dropped the schema on every target, that ``_test`` one
included. Existence is now recorded per database.

The per-database decisions are DB-free (the test harness pins a single
``_test`` database, so two real targets with different histories can't
be built here); dropping a subset against real Postgres is covered at
the end.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from domovoi.plugins_runtime import installer
from domovoi.plugins_runtime.installer import (
    InstallError,
    StagedInstall,
    confirm_install,
    hash_tree_excluding,
)
from domovoi.plugins_runtime.manifest import parse_manifest
from domovoi.plugins_runtime.migrations import PluginMigrationRunner
from domovoi.tests.conftest import requires_db

PROD = "postgresql+asyncpg://u:p@h:6432/domo"
TEST = "postgresql+asyncpg://u:p@h:6432/domo_test"


def _runner(tmp_path: Path, monkeypatch, *, exists: dict[str, bool], fail_on: str | None):
    """A runner over two targets with the DB calls replaced: ``exists``
    says which targets already have the schema, ``fail_on`` which target's
    apply raises. Returns (runner, dropped URLs)."""
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "V001__init.sql").write_text("CREATE TABLE things (id INT);", encoding="utf-8")
    runner = PluginMigrationRunner("pertarget", d, database_urls=[PROD, TEST])
    state = dict(exists)
    dropped: list[str] = []

    async def schema_exists(url: str | None = None) -> bool:
        return state[url or PROD]

    async def apply_to(url: str, files) -> list[str]:
        state[url] = True                     # CREATE SCHEMA IF NOT EXISTS
        if url == fail_on:
            raise RuntimeError(f"V001 failed on {url}")
        return ["V001__init.sql"]

    async def drop_schema(url: str | None = None, *, only=None) -> None:
        dropped.append(url)

    monkeypatch.setattr(runner, "schema_exists", schema_exists)
    monkeypatch.setattr(runner, "_apply_to", apply_to)
    monkeypatch.setattr(runner, "drop_schema", drop_schema)
    return runner, dropped


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exists,fail_on,expect_dropped",
    [
        # The bug: prod fresh, _test left by an earlier install. Only prod's
        # brand-new schema goes; _test's survives.
        ({PROD: False, TEST: True}, TEST, [PROD]),
        # Both fresh: both-or-neither, as before.
        ({PROD: False, TEST: False}, TEST, [PROD, TEST]),
        # Prod fails first: _test was never touched, so nothing to drop there.
        ({PROD: False, TEST: False}, PROD, [PROD]),
        # Catch-up on prod, _test brand new: prod keeps its schema.
        ({PROD: True, TEST: False}, TEST, [TEST]),
        # Catch-up everywhere: nothing is dropped.
        ({PROD: True, TEST: True}, TEST, []),
    ],
)
async def test_a_failed_apply_drops_only_the_schemas_this_run_created(
    tmp_path: Path, monkeypatch, exists: dict[str, bool], fail_on: str, expect_dropped: list[str]
) -> None:
    runner, dropped = _runner(tmp_path, monkeypatch, exists=exists, fail_on=fail_on)
    with pytest.raises(RuntimeError, match="V001 failed"):
        await runner.apply_all()
    assert dropped == expect_dropped


@pytest.mark.asyncio
async def test_missing_schema_urls_is_per_database(tmp_path: Path, monkeypatch) -> None:
    runner, _ = _runner(tmp_path, monkeypatch, exists={PROD: False, TEST: True}, fail_on=None)
    assert await runner.missing_schema_urls() == [PROD]


# ─── the install pipeline's rollback ───────────────────────────────────────

FIXTURE = Path(__file__).parent / "fixtures" / "compliments"


class _FakeRunner:
    """Stands in for PluginMigrationRunner inside confirm_install: prod is
    fresh, _test already holds the schema, and the migrations fail."""

    drops: list[dict[str, Any]] = []

    def __init__(self, slug: str, migrations_dir: Path, **_: Any) -> None:
        self.database_urls = [PROD, TEST]

    async def missing_schema_urls(self) -> list[str]:
        return [PROD]

    async def apply_all(self) -> dict[str, list[str]]:
        raise RuntimeError("V002 failed on domo_test")

    async def drop_schema(self, url: str | None = None, *, only=None) -> None:
        _FakeRunner.drops.append({"url": url, "only": only})


@pytest_asyncio.fixture
async def staged(tmp_path: Path):
    root = tmp_path / "staged"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = parse_manifest((root / "domovoi-plugin.toml").read_text(encoding="utf-8"))
    st = StagedInstall(
        staged_id="pertarget-staged", root=root, manifest=manifest,
        tree_hash=hash_tree_excluding(root, {".domovoi-staging.json"}),
        source_ref="compliments.zip", install_source="zip",
    )
    installer._STAGED[st.staged_id] = st
    yield st
    installer._STAGED.pop(st.staged_id, None)


@pytest.mark.asyncio
async def test_confirm_install_rollback_drops_only_the_fresh_databases(
    staged: StagedInstall, monkeypatch
) -> None:
    _FakeRunner.drops = []
    monkeypatch.setattr(installer, "PluginMigrationRunner", _FakeRunner)
    with pytest.raises(InstallError, match="V002 failed"):
        await confirm_install(staged.staged_id)
    assert _FakeRunner.drops == [{"url": None, "only": [PROD]}]
    assert not staged.root.exists()                  # staging cleaned up as before


@pytest.mark.asyncio
async def test_confirm_install_rollback_drops_nothing_when_every_schema_existed(
    staged: StagedInstall, monkeypatch
) -> None:
    class AllThere(_FakeRunner):
        async def missing_schema_urls(self) -> list[str]:
            return []

    _FakeRunner.drops = []
    monkeypatch.setattr(installer, "PluginMigrationRunner", AllThere)
    with pytest.raises(InstallError):
        await confirm_install(staged.staged_id)
    assert _FakeRunner.drops == []


# ─── dropping a subset, on Postgres ────────────────────────────────────────

SLUG = "migtargets"
SCHEMA = f"plugin_{SLUG}"
ROLE = f"plugin_{SLUG}"


async def _drv_do(fn):
    from domovoi.db.session import engine

    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        return await fn(raw.driver_connection)


async def _state() -> tuple[bool, bool]:
    async def go(drv):
        schema = await drv.fetchval(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = $1", SCHEMA
        )
        role = await drv.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", ROLE)
        return schema is not None, role is not None

    return await _drv_do(go)


@pytest_asyncio.fixture
async def real_migdir(tmp_path: Path):
    async def clean(drv) -> None:
        await drv.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        if await drv.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", ROLE):
            await drv.execute(f'DROP OWNED BY "{ROLE}"')
            await drv.execute(f'DROP ROLE IF EXISTS "{ROLE}"')

    await _drv_do(clean)
    d = tmp_path / "migrations"
    d.mkdir()
    (d / "V001__init.sql").write_text("CREATE TABLE things (id BIGSERIAL PRIMARY KEY);", encoding="utf-8")
    yield d
    await _drv_do(clean)


@requires_db
@pytest.mark.asyncio
async def test_dropping_a_subset_keeps_the_role_the_other_schema_needs(real_migdir: Path) -> None:
    from domovoi.config import settings

    real = settings.database_url
    await PluginMigrationRunner(SLUG, real_migdir, database_urls=[real]).apply_all()
    assert await _state() == (True, True)
    # Two targets, only the real one dropped: the other target (never
    # connected to here) keeps its schema, so the role must stay.
    two = PluginMigrationRunner(SLUG, real_migdir, database_urls=[real, TEST])
    await two.drop_schema(only=[real])
    assert await _state() == (False, True)

    # Every target dropped: the role goes with the last one, as before.
    one = PluginMigrationRunner(SLUG, real_migdir, database_urls=[real])
    await one.apply_all()
    await one.drop_schema(only=[real])
    assert await _state() == (False, False)
