"""Copyleft packages that Domovoi stopped depending on stay gone.

The project takes permissively licensed dependencies only (MIT, BSD, ISC,
Apache-2.0, public domain; MPL-2.0 for unmodified third-party code), no
GPL, and avoids LGPL where its own code does the job (docs/CONTRIBUTING.md,
"Dependencies and licences"). Each package below was replaced; this test
fails if one comes back as an import in the shipped code, a requirement in
pyproject.toml, or a pin in either lock. Pure file checks: no DB, no
network, never skips.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: import name -> (distribution name, licence, what replaced it)
REPLACED = {
    "mutagen": ("mutagen", "GPL-2.0-or-later", "domovoi/media_tags, Domovoi's own tag reader (2026-10-15)"),
    "mpd": ("python-mpd2", "LGPL-3.0-or-later", "domovoi/clients/mpd_protocol.py (2026-10-15)"),
    "lameenc": ("lameenc", "LGPL-3.0, carrying LAME", "the system ffmpeg, run as a program (canned_sounds, 2026-10-15)"),
}

#: The code a Domovoi install runs: core, dashboard, satellite, bundled plugins.
SHIPPED = ("domovoi", "web", "satellite", "plugins")


def _python_files() -> list[Path]:
    out = []
    for top in SHIPPED:
        for p in (REPO / top).rglob("*.py"):
            if "node_modules" in p.parts or ".venv" in p.parts:
                continue
            out.append(p)
    return out


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_no_shipped_module_imports_a_replaced_package() -> None:
    hits = []
    for path in _python_files():
        for root in _imported_roots(path) & REPLACED.keys():
            hits.append(f"{path.relative_to(REPO)} imports {root} ({REPLACED[root][1]})")
    assert hits == []


def _requirement_names(reqs: list[str]) -> set[str]:
    return {re.split(r"[\s<>=!~\[;]", r.strip(), maxsplit=1)[0].lower() for r in reqs}


def test_pyproject_requires_none_of_them() -> None:
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = _requirement_names(project["dependencies"])
    for extra, reqs in project.get("optional-dependencies", {}).items():
        names |= _requirement_names(reqs)
    assert names & {d for d, _, _ in REPLACED.values()} == set()


@pytest.mark.parametrize("lock", ["requirements.lock", "requirements-linux-py314.lock"])
def test_the_locks_pin_none_of_them(lock: str) -> None:
    pins = {m.group(1).lower() for m in re.finditer(r"^([A-Za-z0-9_.-]+)==", (REPO / lock).read_text(encoding="utf-8"), re.M)}
    assert pins, lock
    assert pins & {d for d, _, _ in REPLACED.values()} == set()


def test_the_guard_sees_an_import_when_there_is_one(tmp_path) -> None:
    p = tmp_path / "x.py"
    p.write_text("import os\nfrom mutagen.id3 import ID3\nfrom . import mutagen\n", encoding="utf-8")
    assert _imported_roots(p) == {"os", "mutagen"}
