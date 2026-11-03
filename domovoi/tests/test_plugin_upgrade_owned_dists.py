"""An upgrade may move the plugin's own pinned packages (2026-11-02).

The media-provider plugin's 1.2.5 bumped its one tool pin, and the
upgrade refused: the dry run read every changed version as a conflict
with the environment, the plugin's own package included, so no upgrade
that follows its upstream could ever install. Now an upgrade may move
what the installed version owns (brought in, or pins), never what the
core requires or another plugin declares; the real install keeps it
owned, so the plugin's uninstall still removes it.

DB-free: pip, importlib.metadata and the registry are stand-ins.
"""

from __future__ import annotations

import json
import subprocess
from importlib import metadata as importlib_metadata
from pathlib import Path
from types import SimpleNamespace

import pytest

from domovoi.plugins_runtime import installer
from domovoi.plugins_runtime.installer import InstallError

_INSTALLED = {"tool-x": "1.0", "requests": "2.30.0", "fastapi": "0.110.0"}


def _report(*dists: tuple[str, str]) -> str:
    return json.dumps({"install": [
        {"metadata": {"name": n, "version": v}, "download_info": {}}
        for n, v in dists
    ]})


@pytest.fixture
def pip(monkeypatch, tmp_path):
    """The dry run's pip answers ``state["report"]``; installed versions
    come from ``_INSTALLED``."""
    state = {"report": _report()}
    monkeypatch.setattr(installer, "validate_lockfile", lambda p: [])
    monkeypatch.setattr(
        installer.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, state["report"], ""),
    )

    def version(name):
        try:
            return _INSTALLED[installer._dist_key(name)]
        except KeyError:
            raise importlib_metadata.PackageNotFoundError(name) from None

    monkeypatch.setattr(importlib_metadata, "version", version)
    state["lock"] = tmp_path / "requirements.lock"
    return state


def test_a_changed_version_is_still_a_conflict_by_default(pip) -> None:
    pip["report"] = _report(("tool_x", "2.0"))
    with pytest.raises(InstallError) as e:
        installer.pip_dry_run(pip["lock"])
    assert e.value.code == "requirements_conflict"
    assert e.value.details["conflicts"][0]["package"] == "tool_x"


def test_an_owned_package_may_move(pip) -> None:
    # pip reports the name in whatever spelling; ownership is by PEP 503 key.
    pip["report"] = _report(("Tool.X", "2.0"))
    out = installer.pip_dry_run(pip["lock"], frozenset({"tool-x"}))
    assert [d["version"] for d in out["resolved"]] == ["2.0"]


def test_owning_one_package_does_not_free_another(pip) -> None:
    pip["report"] = _report(("tool-x", "2.0"), ("requests", "2.34.2"))
    with pytest.raises(InstallError) as e:
        installer.pip_dry_run(pip["lock"], frozenset({"tool-x"}))
    assert [c["package"] for c in e.value.details["conflicts"]] == ["requests"]


def _row(slug, *, python=(), newly=(), status="ok"):
    return SimpleNamespace(
        slug=slug, status=status,
        manifest={"requirements": {"python": list(python)}},
        pip_report={"newly_installed": [{"name": n} for n in newly]},
    )


async def test_ownership_leaves_out_the_core_its_tree_and_other_plugins(monkeypatch) -> None:
    mine = _row(
        "media", python=["tool-x==1.0", "requests==2.30.0", "fastapi==0.110.0"],
        newly=["tool_x", "helper-lib", "Starlette"],
    )
    rows = [
        mine,
        _row("radio", python=["requests==2.30.0"]),
        # An uninstalled plugin's declarations hold nothing back.
        _row("gone", python=["helper-lib==3"], status="uninstalled"),
    ]

    async def list_plugins():
        return rows

    monkeypatch.setattr(installer.reg, "list_plugins", list_plugins)
    # The core needs starlette only through fastapi: still the core's.
    core = {"domovoi": ["fastapi>=0.100", 'pytest>=8; extra == "dev"'],
            "fastapi": ["starlette<0.40"], "starlette": ["fastapi"]}

    def requires(dist):
        if dist not in core:
            raise importlib_metadata.PackageNotFoundError(dist)
        return core[dist]

    monkeypatch.setattr(importlib_metadata, "requires", requires)
    owned = await installer._upgrade_owned_dists(mine)
    assert owned == {"tool-x", "helper-lib"}


async def test_a_plugin_with_no_packages_owns_nothing(monkeypatch) -> None:
    async def list_plugins():  # pragma: no cover — not reached
        raise AssertionError("no need to ask")

    monkeypatch.setattr(installer.reg, "list_plugins", list_plugins)
    assert await installer._upgrade_owned_dists(_row("plain")) == frozenset()


def test_the_install_keeps_an_owned_package_owned(monkeypatch, tmp_path: Path) -> None:
    """The upgrade's pip report lists the moved package as the plugin's,
    so its uninstall still removes it (the refcount reads newly_installed)."""
    before = [("tool_x", "1.0"), ("requests", "2.30.0")]
    after = [("tool_x", "2.0"), ("requests", "2.30.0"), ("new-dep", "1")]
    snapshots = iter([before, after])

    def distributions():
        return [
            SimpleNamespace(metadata={"Name": n}, version=v)
            for n, v in next(snapshots)
        ]

    monkeypatch.setattr(installer, "validate_lockfile", lambda p: [])
    monkeypatch.setattr(importlib_metadata, "distributions", distributions)
    monkeypatch.setattr(
        installer.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""),
    )
    report = installer.pip_install(tmp_path / "requirements.lock", frozenset({"tool-x"}))
    assert report["newly_installed"] == [
        {"name": "new-dep", "version": "1"}, {"name": "tool_x", "version": "2.0"},
    ]
