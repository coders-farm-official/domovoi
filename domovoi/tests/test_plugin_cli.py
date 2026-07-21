"""``domovoi plugin`` CLI — pack staging hygiene.

Focus: ``domovoi plugin pack`` must not stage dev/build detritus (a
virtualenv, build/dist trees, node_modules) into the installable zip.
On Windows an in-tree ``.venv`` is thousands of deep-path entries that
both bloat the package and trip MAX_PATH (WinError 206) during staging.
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

from domovoi.plugins_runtime.cli import _cmd_new, _cmd_pack


def _scaffold(tmp_path: Path, slug: str = "packdemo") -> Path:
    rc = _cmd_new(argparse.Namespace(slug=slug, dir=str(tmp_path)))
    assert rc == 0
    return tmp_path / slug


def test_pack_excludes_venv_and_build_dirs(tmp_path: Path) -> None:
    root = _scaffold(tmp_path)

    # An in-tree virtualenv (deep nested files) + other build detritus.
    venv_deep = root / ".venv" / "Lib" / "site-packages" / "somedep"
    venv_deep.mkdir(parents=True)
    (venv_deep / "__init__.py").write_text("x = 1", encoding="utf-8")
    (root / ".venv" / "pyvenv.cfg").write_text("home = /python", encoding="utf-8")
    for junk in ("venv", "env", ".tox", "dist", "build", "node_modules"):
        d = root / junk
        d.mkdir()
        (d / "junk.txt").write_text("junk", encoding="utf-8")

    out = tmp_path / "packdemo.zip"
    rc = _cmd_pack(argparse.Namespace(path=str(root), output=str(out)))
    assert rc == 0
    assert out.is_file()

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()

    # None of the excluded dirs contributed any entry.
    for junk in (".venv", "venv", "env", ".tox", "dist", "build", "node_modules"):
        assert not any(
            n == junk or n.startswith(f"{junk}/") for n in names
        ), f"{junk}/ leaked into the pack: {[n for n in names if junk in n]}"

    # The real plugin payload IS packed.
    assert "domovoi-plugin.toml" in names
    assert "domovoi_plugin_packdemo/core.py" in names
