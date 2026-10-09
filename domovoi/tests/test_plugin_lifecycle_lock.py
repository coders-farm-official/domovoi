"""Plugin installs, upgrades, uninstalls and dry runs happen one at a time
(REV-13, 2026-10-08).

A7-04 moved pip into worker threads so a minutes-long install no longer
froze the core's event loop. That also removed the only thing that kept
two pip runs apart: the blocked loop. With nothing else serialising them,
a double click on Install ran the same staged plugin twice, two plugins
installed side by side, and an uninstall's pip could run inside another
plugin's install. pip_install's ``newly_installed`` is a before/after diff
of the WHOLE environment, so each concurrent run claimed the other's
dists and the loser's rollback pip-uninstalled them from under the winner.

Now confirm, upgrade, uninstall and the stage-time dry run share one lock
(``installer.lifecycle_lock``), and a confirm claims its staged id before
it waits for the lock, so a second confirm of the same id answers 409.

DB-free: the registry, the migration runner and the loader are faked, and
the pip functions are replaced with slow stand-ins that record overlap.
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from domovoi.plugins_runtime import installer
from domovoi.plugins_runtime import registry as reg
from domovoi.plugins_runtime.installer import InstallError, StagedInstall

FIXTURE = Path(__file__).parent / "fixtures" / "compliments"
PIP_SECONDS = 0.3


class _Pip:
    """Slow, thread-run stand-ins for pip_install / pip_uninstall /
    pip_dry_run that record what ran and whether two ever overlapped."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.events: list[tuple[str, str]] = []

    def _run(self, what: str, ret: Any) -> Any:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.events.append(("start", what))
        time.sleep(PIP_SECONDS)
        with self._lock:
            self.active -= 1
            self.events.append(("end", what))
        return ret

    def install(self, lockfile: Path) -> dict[str, Any]:
        slug = lockfile.parent.name
        return self._run(f"install:{slug}", {"newly_installed": [{"name": f"dep-{slug}"}]})

    def uninstall(self, dist_names: list[str]) -> None:
        self._run(f"uninstall:{','.join(dist_names)}", None)

    def dry_run(self, lockfile: Path) -> dict[str, Any]:
        return self._run("dry-run", {"resolved": []})

    def runs(self, prefix: str) -> list[str]:
        return [w for kind, w in self.events if kind == "start" and w.startswith(prefix)]


class _Runner:
    def __init__(self, slug: str, path: Path) -> None:
        self.slug = slug
        self.database_urls: list[str] = []

    async def missing_schema_urls(self) -> list[str]:
        return []

    async def apply_all(self) -> None:
        return None

    async def drop_schema(self, only=None) -> None:
        return None

    async def ledger_max_version(self, url=None) -> int:
        return 0


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The installer's world in tmp_path: fake registry, runner and loader,
    slow pip. Returns a namespace with the registry rows and the pip
    recorder."""
    home = tmp_path / "home" / ".domovoi" / "plugins"
    installed = home / "installed"
    staging = home / ".staging"
    for d in (installed, staging):
        d.mkdir(parents=True)
    monkeypatch.setattr(installer, "installed_root", lambda: installed)
    monkeypatch.setattr(installer, "staging_root", lambda: staging)
    monkeypatch.setattr(installer, "previous_root", lambda: home / ".previous")
    monkeypatch.setattr(installer, "data_root", lambda: home / "data")

    rows: dict[str, Any] = {}

    async def get_plugin(slug):
        return rows.get(slug)

    async def list_plugins():
        return list(rows.values())

    async def insert_plugin(**kw):
        rows[kw["slug"]] = SimpleNamespace(status="ok", **kw)

    async def delete_plugin(slug):
        rows.pop(slug, None)

    async def update_plugin(slug, **kw):
        rows[slug].__dict__.update(kw)

    async def plugin_schema_exists(slug):
        return False

    async def nothing(*a, **kw):
        return None

    monkeypatch.setattr(reg, "get_plugin", get_plugin)
    monkeypatch.setattr(reg, "list_plugins", list_plugins)
    monkeypatch.setattr(reg, "insert_plugin", insert_plugin)
    monkeypatch.setattr(reg, "delete_plugin", delete_plugin)
    monkeypatch.setattr(reg, "update_plugin", update_plugin)
    monkeypatch.setattr(reg, "plugin_schema_exists", plugin_schema_exists)
    monkeypatch.setattr(installer.LOADER, "load_plugin", nothing)
    monkeypatch.setattr(installer.LOADER, "unload_plugin", nothing)
    monkeypatch.setattr(installer, "PluginMigrationRunner", _Runner)
    monkeypatch.setattr(installer, "_best_effort_resync", nothing)
    monkeypatch.setattr(installer, "_reprime_router", lambda: None)
    from domovoi.plugins_runtime import letta_resync

    monkeypatch.setattr(letta_resync, "resync_tools", nothing)

    pip = _Pip()
    monkeypatch.setattr(installer, "pip_install", pip.install)
    monkeypatch.setattr(installer, "pip_uninstall", pip.uninstall)
    monkeypatch.setattr(installer, "pip_dry_run", pip.dry_run)
    yield SimpleNamespace(rows=rows, pip=pip, installed=installed, staging=staging)
    installer._STAGED.clear()
    installer._CONFIRMING.clear() if hasattr(installer, "_CONFIRMING") else None


def _stage(world, slug: str) -> str:
    """A staged plugin with one pinned requirement, registered in _STAGED
    exactly as stage_zip leaves it."""
    staged_id = f"staged-{slug}"
    root = world.staging / slug
    root.mkdir()
    (root / "domovoi-plugin.toml").write_text(f'[plugin]\nslug = "{slug}"\n', encoding="utf-8")
    (root / "requirements.lock").write_text("x==1\n", encoding="utf-8")
    (root / ".domovoi-staging.json").write_text("{}", encoding="utf-8")
    manifest = SimpleNamespace(
        slug=slug, name=slug.title(), version="1.0.0", publisher="Coders Farm",
        license="MIT", domovoi_api=">=1.0,<2.0", migrations_dir="migrations",
        python_requirements=("x==1",), lockfile="requirements.lock", raw={},
    )
    installer._STAGED[staged_id] = StagedInstall(
        staged_id=staged_id, root=root, manifest=manifest,  # type: ignore[arg-type]
        tree_hash=installer.hash_tree_excluding(root, {".domovoi-staging.json"}),
        source_ref=f"{slug}.zip", install_source="zip",
    )
    return staged_id


def _installed_row(world, slug: str) -> None:
    d = world.installed / slug
    d.mkdir()
    world.rows[slug] = SimpleNamespace(
        slug=slug, status="ok", bundled=False, install_source="zip",
        install_dir=str(d), manifest={}, version="1.0.0",
        pip_report={"newly_installed": [{"name": f"dep-{slug}"}]},
    )


def _no_overlap(events: list[tuple[str, str]]) -> bool:
    """True when every pip run ended before the next one started."""
    open_runs = 0
    for kind, _ in events:
        open_runs += 1 if kind == "start" else -1
        if open_runs > 1:
            return False
    return True


async def test_a_double_confirm_installs_once_and_the_second_answers_409(world) -> None:
    """The double click: the same staged id confirmed twice while the first
    is still in pip."""
    staged_id = _stage(world, "alpha")
    first, second = await asyncio.gather(
        installer.confirm_install(staged_id),
        installer.confirm_install(staged_id),
        return_exceptions=True,
    )
    results = [first, second]
    done = [r for r in results if isinstance(r, dict)]
    refused = [r for r in results if isinstance(r, InstallError)]
    assert len(done) == 1 and done[0]["installed"] is True, results
    assert len(refused) == 1, results
    assert refused[0].code == "confirm_in_progress"
    assert installer._install_error_response(refused[0]).status_code == 409
    # pip ran once, and nothing was rolled back out from under the winner.
    assert world.pip.runs("install:") == ["install:alpha"]
    assert world.pip.runs("uninstall:") == []
    assert (world.installed / "alpha").is_dir()
    assert "alpha" in world.rows


async def test_the_double_confirm_through_the_endpoint_is_a_409(world) -> None:
    from fastapi import HTTPException

    staged_id = _stage(world, "alpha")
    results = await asyncio.gather(
        installer.api_confirm(staged_id), installer.api_confirm(staged_id),
        return_exceptions=True,
    )
    statuses = sorted(
        r.status_code if isinstance(r, HTTPException) else 200 for r in results
    )
    assert statuses == [200, 409], results


async def test_two_installs_never_run_pip_side_by_side(world) -> None:
    a, b = _stage(world, "alpha"), _stage(world, "bravo")
    results = await asyncio.gather(installer.confirm_install(a), installer.confirm_install(b))
    assert all(r["installed"] for r in results)
    assert sorted(world.pip.runs("install:")) == ["install:alpha", "install:bravo"]
    assert world.pip.max_active == 1, world.pip.events
    assert _no_overlap(world.pip.events)


async def test_an_uninstall_waits_for_an_install_that_is_running(world) -> None:
    """An uninstall's pip uninstall inside another plugin's pip install is
    the diff-of-the-environment race again, from the other side."""
    _installed_row(world, "charlie")
    staged_id = _stage(world, "alpha")
    installed, uninstalled = await asyncio.gather(
        installer.confirm_install(staged_id),
        installer.uninstall_plugin("charlie"),
    )
    assert installed["installed"] is True
    assert uninstalled["uninstalled"] is True
    assert world.pip.runs("uninstall:") == ["uninstall:dep-charlie"]
    assert world.pip.max_active == 1, world.pip.events
    assert _no_overlap(world.pip.events)


async def test_the_lock_is_free_again_after_a_failed_confirm(world, monkeypatch) -> None:
    """A confirm that fails still lets go of the lock and its claim."""
    staged_id = _stage(world, "alpha")

    def broken(lockfile):
        raise RuntimeError("index unreachable")

    monkeypatch.setattr(installer, "pip_install", broken)
    with pytest.raises(InstallError):
        await installer.confirm_install(staged_id)
    assert not installer.lifecycle_lock().locked()
    assert staged_id not in installer._CONFIRMING


# ─── the stage-time dry run ─────────────────────────────────────────────


def _requirements_zip() -> bytes:
    files = {
        f.relative_to(FIXTURE).as_posix(): f.read_bytes()
        for f in sorted(FIXTURE.rglob("*"))
        if f.is_file() and "__pycache__" not in f.parts
    }
    manifest = files["domovoi-plugin.toml"].decode("utf-8")
    manifest += '\n[requirements]\npython = ["httpx==0.28.1"]\n'
    files["domovoi-plugin.toml"] = manifest.encode("utf-8")
    files["requirements.lock"] = (
        "httpx==0.28.1 \\\n    --hash=sha256:" + "0" * 64 + "\n"
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


async def test_the_dry_run_waits_while_an_install_holds_the_lock(world) -> None:
    lock = installer.lifecycle_lock()
    await lock.acquire()
    try:
        task = asyncio.create_task(installer.stage_zip(_requirements_zip()))
        await asyncio.sleep(PIP_SECONDS * 2)
        assert world.pip.runs("dry-run") == [], "the dry run did not wait for the lock"
        assert not task.done()
    finally:
        lock.release()
    staged = await task
    assert staged.manifest.slug == "compliments"
    assert world.pip.runs("dry-run") == ["dry-run"]
