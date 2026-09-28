"""Migration refusals and failures reach the admin API as InstallError
(a 422 naming the file and the reason), not as an unhandled 500.

The runner raises its own family — MigrationChecksumError (an applied
file changed on disk), MigrationSandboxError, MigrationError (a file
failed, wrapping the driver's error) — and SqlLintError. The install
pipeline and enable used to let those escape: ``api_enable`` only
catches InstallError, so drift found on enable was a 500. The installer
now maps them at its call sites: ``migration_drift``, ``migration_lint``
and ``migration_failed``. DB-free; the end-to-end drift case on Postgres
is in ``test_plugin_installer.py``.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException

from domovoi.plugins_runtime import installer
from domovoi.plugins_runtime.installer import (
    InstallError,
    StagedInstall,
    confirm_install,
    hash_tree_excluding,
)
from domovoi.plugins_runtime.manifest import parse_manifest
from domovoi.plugins_runtime.migrations import (
    MigrationChecksumError,
    MigrationError,
    MigrationSandboxError,
    SqlLintError,
)

FIXTURE = Path(__file__).parent / "fixtures" / "compliments"
SLUG = "compliments"
DRIFT = (
    f"{SLUG}: applied migration V001__compliments_schema.sql differs from "
    "the file on disk (ledger 0123456789ab…, file ba9876543210…) — "
    "migrations are append-only; refusing to load"
)

CASES = [
    (MigrationChecksumError(DRIFT), "migration_drift"),
    (SqlLintError("V002__x.sql: transaction control … is forbidden"), "migration_lint"),
    (MigrationSandboxError(f"{SLUG}: V002__x.sql left the migration sandbox"), "migration_failed"),
    (MigrationError(f"{SLUG}: V002__x.sql failed on domovoi_test: syntax error"), "migration_failed"),
]


def _runner_raising(err: Exception):
    class Failing:
        def __init__(self, slug: str, migrations_dir: Path, **_: Any) -> None:
            self.database_urls = ["postgresql+asyncpg://u:p@h/domo"]

        async def missing_schema_urls(self) -> list[str]:
            return []

        async def apply_all(self) -> dict[str, list[str]]:
            raise err

        async def drop_schema(self, url: str | None = None, *, only=None) -> None:
            pass

    return Failing


@pytest.fixture
def installed(tmp_path: Path, monkeypatch):
    """A disabled, installed compliments plugin as enable_plugin sees it,
    with the registry replaced. Yields the list of set_enabled calls."""
    plugin_dir = tmp_path / SLUG
    shutil.copytree(FIXTURE, plugin_dir, ignore=shutil.ignore_patterns("__pycache__"))
    row = SimpleNamespace(slug=SLUG, status="ok", enabled=False, install_dir=str(plugin_dir))
    enabled_calls: list[bool] = []

    async def get_plugin(slug: str, **_: Any):
        return row if slug == SLUG else None

    async def set_enabled(slug: str, value: bool, **_: Any) -> None:
        enabled_calls.append(value)

    monkeypatch.setattr(installer.reg, "get_plugin", get_plugin)
    monkeypatch.setattr(installer.reg, "set_enabled", set_enabled)
    return enabled_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("err,code", CASES)
async def test_enable_maps_migration_errors_to_install_errors(
    installed: list[bool], monkeypatch, err: Exception, code: str
) -> None:
    monkeypatch.setattr(installer, "PluginMigrationRunner", _runner_raising(err))
    with pytest.raises(InstallError) as exc:
        await installer.enable_plugin(SLUG)
    assert exc.value.code == code
    assert str(exc.value) == str(err)
    assert exc.value.__cause__ is err
    assert installed == []                        # never marked enabled


@pytest.mark.asyncio
async def test_api_enable_answers_drift_with_a_422(installed: list[bool], monkeypatch) -> None:
    monkeypatch.setattr(
        installer, "PluginMigrationRunner", _runner_raising(MigrationChecksumError(DRIFT))
    )
    with pytest.raises(HTTPException) as exc:
        await installer.api_enable(SLUG)
    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["code"] == "migration_drift"
    assert "V001__compliments_schema.sql" in exc.value.detail["error"]["message"]


@pytest.mark.asyncio
async def test_an_unrelated_error_is_not_relabelled(installed: list[bool], monkeypatch) -> None:
    """Only the runner's own family is mapped; anything else (a database
    that can't be reached, say) still surfaces as what it is."""
    monkeypatch.setattr(
        installer, "PluginMigrationRunner", _runner_raising(ConnectionRefusedError("no db"))
    )
    with pytest.raises(ConnectionRefusedError):
        await installer.enable_plugin(SLUG)


@pytest_asyncio.fixture
async def staged(tmp_path: Path):
    root = tmp_path / "staged"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = parse_manifest((root / "domovoi-plugin.toml").read_text(encoding="utf-8"))
    st = StagedInstall(
        staged_id="migerr-staged", root=root, manifest=manifest,
        tree_hash=hash_tree_excluding(root, {".domovoi-staging.json"}),
        source_ref="compliments.zip", install_source="zip",
    )
    installer._STAGED[st.staged_id] = st
    yield st
    installer._STAGED.pop(st.staged_id, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("err,code", CASES)
async def test_confirm_install_names_the_migration_problem(
    staged: StagedInstall, monkeypatch, err: Exception, code: str
) -> None:
    monkeypatch.setattr(installer, "PluginMigrationRunner", _runner_raising(err))
    with pytest.raises(InstallError) as exc:
        await confirm_install(staged.staged_id)
    assert exc.value.code == code
    assert str(exc.value) == str(err)
    assert not staged.root.exists()              # rolled back as before
