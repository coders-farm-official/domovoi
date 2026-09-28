"""An upgrade of a plugin this process already imported is staged for the
next restart instead of hot-loaded (installer.confirm_upgrade).

``importlib.import_module`` hands back the cached module however the files
on disk changed, so the old hot load re-ran the OLD ``register()`` against
the NEW manifest: the dashboard said "upgrade complete" while the old code
kept serving, and a version that declared a new worker or handler failed
its contract check against the stale module and was left disabled with
``load_error`` (and its ``.previous`` copy deleted). Now the upgrade applies
migrations, swaps the files, writes the row and answers
``restart_required: true``; a marker the version and plugin APIs report
waits for the restart, and the new code loads at the (simulated) restart.
"""

from __future__ import annotations

import importlib
import io
import sys
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi import bootstrap
from domovoi.db.session import engine
from domovoi.handlers import HANDLER_BY_NAME
from domovoi.plugins_runtime import installer
from domovoi.plugins_runtime import registry as reg
from domovoi.plugins_runtime.installer import (
    confirm_install,
    confirm_upgrade,
    disable_plugin,
    enable_plugin,
    stage_zip,
    uninstall_plugin,
)
from domovoi.plugins_runtime.loader import LOADER, code_imported
from domovoi.plugins_runtime.workers import WORKERS
from domovoi.tests.conftest import requires_db

pytestmark = [pytest.mark.asyncio, requires_db]

FIXTURE = Path(__file__).parent / "fixtures" / "compliments"
SLUG = "compliments"
PKG = f"domovoi_plugin_{SLUG}"

# A later version that declares a second worker and registers it — the
# shape that used to fail the contract check against the cached module.
_NEW_WORKER_TOML = b'[[workers]]\nname = "compliment_counter"\nkind = "poll"\n\n'
_NEW_WORKER_CODE = b'''

class ComplimentCounter(Worker):
    name = "compliment_counter"
    interval_setting = "media_plays_pruner_interval_sec"
    stub_suppressed = True

    async def tick(self) -> None:
        pass


_register_first_version = register


def register(ctx) -> None:
    _register_first_version(ctx)
    ctx.add_worker(ComplimentCounter())
'''


def build_zip(version: str, *, new_worker: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(FIXTURE.rglob("*")):
            if f.is_dir() or "__pycache__" in f.parts:
                continue
            rel = f.relative_to(FIXTURE).as_posix()
            data = f.read_bytes()
            if rel == "domovoi-plugin.toml":
                data = data.replace(b'version = "1.0.0"', f'version = "{version}"'.encode())
                if new_worker:
                    assert b"[permissions]" in data
                    data = data.replace(b"[permissions]", _NEW_WORKER_TOML + b"[permissions]")
            elif rel.endswith("/core.py") and new_worker:
                data += _NEW_WORKER_CODE
            zf.writestr(rel, data)
    return buf.getvalue()


def _ours(name: str) -> bool:
    return name == PKG or name.startswith(PKG + ".")


def _forget_imports() -> None:
    for name in [n for n in sys.modules if _ours(n)]:
        del sys.modules[name]


async def _simulate_restart() -> None:
    """What a freshly started core has: nothing loaded, no plugin module
    imported, no staged marker — then the real boot pass."""
    await LOADER.shutdown()
    _forget_imports()
    LOADER.pending_restart.clear()
    await LOADER.discover_and_load_all()


@pytest_asyncio.fixture(autouse=True)
async def _plugin_env(tmp_path: Path, monkeypatch):
    """Sandbox the install paths, and put the import state back afterwards:
    these tests drop the fixture package from sys.modules, and later tests
    (and modules) expect the copy they imported."""
    bootstrap.register_nvidia_dlls()
    from domovoi.plugins_runtime import loader as loader_mod

    dirs = {n: tmp_path / n for n in ("staging", "installed", "data", "previous", "bundled")}
    for d in dirs.values():
        d.mkdir()
    monkeypatch.setattr(installer, "staging_root", lambda: dirs["staging"])
    monkeypatch.setattr(installer, "installed_root", lambda: dirs["installed"])
    monkeypatch.setattr(installer, "data_root", lambda: dirs["data"])
    monkeypatch.setattr(installer, "previous_root", lambda: dirs["previous"])
    monkeypatch.setattr(loader_mod, "installed_root", lambda: dirs["installed"])
    monkeypatch.setattr(loader_mod, "bundled_root", lambda: dirs["bundled"])
    saved_modules = {n: m for n, m in sys.modules.items() if _ours(n)}
    saved_path = list(sys.path)

    yield

    installer._STAGED.clear()
    await LOADER.shutdown()          # discover_and_load_all loads real rows too
    LOADER.pending_restart.clear()
    await reg.delete_plugin(SLUG)
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "plugin_{SLUG}" CASCADE'))
    _forget_imports()
    sys.modules.update(saved_modules)
    sys.path[:] = saved_path
    importlib.invalidate_caches()


async def _install(version: str = "1.0.0") -> None:
    staged = await stage_zip(build_zip(version))
    result = await confirm_install(staged.staged_id)
    assert result["loaded"] and result["restart_required"] is False


async def test_an_upgrade_of_a_running_plugin_is_staged_for_restart() -> None:
    await _install()
    assert "compliments" in HANDLER_BY_NAME
    old_core = sys.modules[f"{PKG}.core"]

    staged = await stage_zip(build_zip("1.1.0"), upgrade_of=SLUG)
    result = await confirm_upgrade(staged.staged_id)

    assert result["restart_required"] is True
    assert result["loaded"] is False
    assert result["version"] == "1.1.0" and result["upgraded_from"] == "1.0.0"
    # No hot load: nothing re-registered the cached module.
    assert SLUG not in LOADER.loaded
    assert "compliments" not in HANDLER_BY_NAME
    assert sys.modules[f"{PKG}.core"] is old_core
    # Everything else about the upgrade is done.
    row = await reg.get_plugin(SLUG)
    assert row is not None
    assert (row.version, row.enabled, row.status, row.last_error) == ("1.1.0", True, "ok", None)
    installed = installer.installed_root() / SLUG / "domovoi-plugin.toml"
    assert 'version = "1.1.0"' in installed.read_text(encoding="utf-8")
    assert list(installer.previous_root().iterdir()) == []
    marker = LOADER.pending_restart[SLUG]
    assert (marker["from_version"], marker["to_version"]) == ("1.0.0", "1.1.0")

    await _simulate_restart()

    assert LOADER.loaded[SLUG].manifest.version == "1.1.0"
    assert "compliments" in HANDLER_BY_NAME
    assert sys.modules[f"{PKG}.core"] is not old_core
    assert LOADER.pending_restart == {}
    row = await reg.get_plugin(SLUG)
    assert row is not None and row.status == "ok" and row.enabled


async def test_a_new_worker_in_the_upgrade_no_longer_disables_the_plugin() -> None:
    await _install()

    staged = await stage_zip(build_zip("1.1.0", new_worker=True), upgrade_of=SLUG)
    result = await confirm_upgrade(staged.staged_id)

    assert result["restart_required"] is True
    row = await reg.get_plugin(SLUG)
    assert row is not None
    # Before: loaded False, enabled False, status load_error ("declared
    # worker compliment_counter not registered"), and still off after a
    # restart until someone re-enabled it.
    assert row.enabled and row.status == "ok" and row.last_error is None

    await _simulate_restart()

    assert SLUG in LOADER.loaded
    assert WORKERS.worker_names(SLUG) == {
        "compliment_ticker": "poll", "compliment_counter": "poll",
    }
    row = await reg.get_plugin(SLUG)
    assert row is not None and row.status == "ok" and row.enabled


async def test_the_api_answers_restart_required_and_the_marker_is_reported(
    monkeypatch,
) -> None:
    from domovoi import git_version
    from domovoi import main as core_main

    async def sha() -> str:
        return "abc1234"

    monkeypatch.setattr(git_version, "current_sha", sha)
    monkeypatch.setattr(git_version, "_BOOT_SHA", "abc1234", raising=False)
    await _install()
    before = await core_main.admin_version()
    assert before["restart_required"] is False
    assert before["plugins_pending_restart"] == []

    staged = await stage_zip(build_zip("1.1.0"), upgrade_of=SLUG)
    result = await installer.api_confirm(staged.staged_id)
    assert result["restart_required"] is True and result["loaded"] is False

    version = await core_main.admin_version()
    assert version["restart_required"] is True
    assert version["code_restart_required"] is False      # nothing was pulled
    [entry] = version["plugins_pending_restart"]
    assert {k: entry[k] for k in ("slug", "from_version", "to_version", "where")} == {
        "slug": SLUG, "from_version": "1.0.0", "to_version": "1.1.0", "where": ["core"],
    }
    assert entry["since"]
    listed = next(
        p for p in (await core_main.list_plugins())["plugins"] if p["slug"] == SLUG
    )
    assert listed["version"] == "1.1.0"
    assert listed["pending_restart"]["to_version"] == "1.1.0"
    status = await core_main.plugin_status(SLUG)
    assert status["pending_restart"]["from_version"] == "1.0.0"
    assert status["handlers"] == []

    await _simulate_restart()

    after = await core_main.admin_version()
    assert after["restart_required"] is False
    assert after["plugins_pending_restart"] == []
    listed = next(
        p for p in (await core_main.list_plugins())["plugins"] if p["slug"] == SLUG
    )
    assert listed["pending_restart"] is None


async def test_enable_while_the_restart_is_pending_does_not_load_the_old_code() -> None:
    await _install()
    staged = await stage_zip(build_zip("1.1.0"), upgrade_of=SLUG)
    await confirm_upgrade(staged.staged_id)
    await disable_plugin(SLUG)

    out = await enable_plugin(SLUG)

    assert out == {"enabled": True, "slug": SLUG, "restart_required": True}
    assert SLUG not in LOADER.loaded
    assert "compliments" not in HANDLER_BY_NAME
    row = await reg.get_plugin(SLUG)
    assert row is not None and row.enabled


async def test_a_second_upgrade_before_the_restart_keeps_the_first_from_version() -> None:
    await _install()
    first = await stage_zip(build_zip("1.1.0"), upgrade_of=SLUG)
    await confirm_upgrade(first.staged_id)
    second = await stage_zip(build_zip("1.2.0"), upgrade_of=SLUG)
    result = await confirm_upgrade(second.staged_id)

    assert result["restart_required"] is True and result["upgraded_from"] == "1.1.0"
    marker = LOADER.pending_restart[SLUG]
    # The code this process imported is still 1.0.0.
    assert (marker["from_version"], marker["to_version"]) == ("1.0.0", "1.2.0")

    # Uninstalling leaves nothing to finish.
    await uninstall_plugin(SLUG, data="keep")
    assert SLUG not in LOADER.pending_restart


async def test_a_plugin_this_process_never_imported_is_upgraded_in_place() -> None:
    """Nothing is cached — a plugin whose import failed at boot, say — so
    the new code can load right away."""
    await _install()
    await LOADER.shutdown()
    _forget_imports()
    assert not code_imported(SLUG)

    staged = await stage_zip(build_zip("1.1.0", new_worker=True), upgrade_of=SLUG)
    result = await confirm_upgrade(staged.staged_id)

    assert result["restart_required"] is False and result["loaded"] is True
    assert LOADER.loaded[SLUG].manifest.version == "1.1.0"
    assert "compliment_counter" in WORKERS.worker_names(SLUG)
    assert SLUG not in LOADER.pending_restart
