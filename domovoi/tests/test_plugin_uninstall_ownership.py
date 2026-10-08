"""Uninstall and upgrade only ever delete a directory the installer made
(A7-05).

``domovoi plugin dev <path>`` registers a developer's working directory in
place (``install_source='dev'``). Uninstall used to ``rmtree`` any
non-bundled row's ``install_dir`` unless it sat under the checkout's
``plugins/`` dir, so "Uninstall (keep)" on a dev registration elsewhere
deleted the developer's source, tests and ``.git``. An upgrade over one
moved that directory into ``.previous`` and deleted it on success. Now the
installer touches a directory only when it is exactly
``installed/<slug>``.

DB-free: the registry, loader and migration runner the two functions call
are replaced with recorders, and the runtime's roots point into tmp_path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from domovoi.plugins_runtime import installer
from domovoi.plugins_runtime import registry as reg

SLUG = "myplugin"


@dataclass
class _Row:
    slug: str
    install_dir: str
    install_source: str
    bundled: bool = False
    version: str = "1.0.0"
    enabled: bool = True
    status: str = "ok"
    manifest: dict[str, Any] | None = None
    pip_report: dict[str, Any] | None = None


class _Runner:
    dropped: list[str] = []

    def __init__(self, slug: str, path: Path) -> None:
        self.slug = slug

    async def drop_schema(self) -> None:
        _Runner.dropped.append(self.slug)


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    """The installer's world: roots in tmp_path, a one-row registry."""
    installed = tmp_path / "home" / ".domovoi" / "plugins" / "installed"
    installed.mkdir(parents=True)
    data = tmp_path / "home" / ".domovoi" / "plugins" / "data"
    previous = tmp_path / "home" / ".domovoi" / "plugins" / ".previous"
    monkeypatch.setattr(installer, "installed_root", lambda: installed)
    monkeypatch.setattr(installer, "data_root", lambda: data)
    monkeypatch.setattr(installer, "previous_root", lambda: previous)

    state: dict[str, Any] = {
        "row": None, "deleted": [], "tombstoned": [], "previous": previous,
    }

    async def get_plugin(slug):
        return state["row"]

    async def list_plugins():
        return [state["row"]] if state["row"] else []

    async def delete_plugin(slug):
        state["deleted"].append(slug)

    async def tombstone_plugin(slug):
        state["tombstoned"].append(slug)

    async def unload_plugin(slug):
        return None

    async def resync():
        return None

    monkeypatch.setattr(reg, "get_plugin", get_plugin)
    monkeypatch.setattr(reg, "list_plugins", list_plugins)
    monkeypatch.setattr(reg, "delete_plugin", delete_plugin)
    monkeypatch.setattr(reg, "tombstone_plugin", tombstone_plugin)
    monkeypatch.setattr(installer.LOADER, "unload_plugin", unload_plugin)
    monkeypatch.setattr(installer, "PluginMigrationRunner", _Runner)
    monkeypatch.setattr(installer, "_best_effort_resync", resync)
    _Runner.dropped = []
    state["installed"] = installed
    return state


def _working_copy(root: Path) -> Path:
    src = root / "work" / SLUG
    (src / ".git").mkdir(parents=True)
    (src / "domovoi-plugin.toml").write_text("[plugin]\n", encoding="utf-8")
    (src / "tests").mkdir()
    (src / "tests" / "test_x.py").write_text("def test(): pass\n", encoding="utf-8")
    return src


@pytest.mark.parametrize("data", ["keep", "purge"])
async def test_uninstalling_a_dev_registration_leaves_the_working_copy(
    runtime, tmp_path, data
) -> None:
    src = _working_copy(tmp_path)
    runtime["row"] = _Row(slug=SLUG, install_dir=str(src), install_source="dev")

    out = await installer.uninstall_plugin(SLUG, data=data)

    assert out["uninstalled"] is True
    assert runtime["deleted"] == [SLUG]                  # the row goes
    assert (src / ".git").is_dir()                       # the files stay
    assert (src / "tests" / "test_x.py").is_file()
    assert (src / "domovoi-plugin.toml").is_file()
    assert _Runner.dropped == ([SLUG] if data == "purge" else [])


async def test_a_dev_registration_inside_installed_is_still_left_alone(
    runtime,
) -> None:
    """The install source decides, not just the path: a dev registration
    is the developer's, wherever it lives."""
    src = runtime["installed"] / SLUG
    src.mkdir()
    (src / "domovoi-plugin.toml").write_text("[plugin]\n", encoding="utf-8")
    runtime["row"] = _Row(slug=SLUG, install_dir=str(src), install_source="dev")

    await installer.uninstall_plugin(SLUG, data="keep")

    assert (src / "domovoi-plugin.toml").is_file()


async def test_a_row_naming_a_dir_outside_installed_is_never_deleted(
    runtime, tmp_path
) -> None:
    """A zip row whose install_dir was edited (or written by an older
    runtime) to point elsewhere: the row goes, the directory stays."""
    elsewhere = _working_copy(tmp_path)
    runtime["row"] = _Row(slug=SLUG, install_dir=str(elsewhere), install_source="zip")

    await installer.uninstall_plugin(SLUG, data="keep")

    assert runtime["deleted"] == [SLUG]
    assert (elsewhere / ".git").is_dir()


async def test_a_sibling_under_installed_is_not_this_plugins_dir(
    runtime,
) -> None:
    other = runtime["installed"] / "someoneelse"
    other.mkdir()
    (other / "keep.txt").write_text("x", encoding="utf-8")
    runtime["row"] = _Row(slug=SLUG, install_dir=str(other), install_source="zip")

    await installer.uninstall_plugin(SLUG, data="keep")

    assert (other / "keep.txt").is_file()


async def test_a_zip_install_in_its_own_dir_is_still_removed(runtime) -> None:
    mine = runtime["installed"] / SLUG
    (mine / "domovoi_plugin_myplugin").mkdir(parents=True)
    (mine / "domovoi-plugin.toml").write_text("[plugin]\n", encoding="utf-8")
    runtime["row"] = _Row(slug=SLUG, install_dir=str(mine), install_source="zip")

    await installer.uninstall_plugin(SLUG, data="keep")

    assert not mine.exists()
    assert runtime["deleted"] == [SLUG]


async def test_upgrading_over_a_dev_registration_is_refused_before_any_move(
    runtime, tmp_path
) -> None:
    src = _working_copy(tmp_path)
    runtime["row"] = _Row(slug=SLUG, install_dir=str(src), install_source="dev")
    staged_root = tmp_path / "staging" / "abc"
    staged_root.mkdir(parents=True)
    staged = installer.StagedInstall(
        staged_id="abc", root=staged_root, manifest=None, tree_hash="x",
        source_ref=None, install_source="zip", preview={}, upgrade_of=SLUG,
    )
    installer._STAGED["abc"] = staged
    try:
        with pytest.raises(installer.InstallError) as exc:
            await installer.confirm_upgrade("abc")
    finally:
        installer._STAGED.pop("abc", None)

    assert exc.value.code == "dev_registration"
    assert (src / ".git").is_dir() and (src / "tests" / "test_x.py").is_file()
    assert not runtime["previous"].exists()              # nothing moved aside
    assert runtime["deleted"] == []                      # the row was not touched
