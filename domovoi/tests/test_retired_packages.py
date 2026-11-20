"""scripts/linux/apply-update.sh's RETIRED_PACKAGES stays in step with the locks.

An update uninstalls every package on that list (drop_retired_packages),
because installing from the locks never removes a package that left them.
So nothing on the list may be in a lock or requirements file the server
installs from, or imported by the code the server runs; and every
copyleft package test_dependency_licences.py keeps out (all of them were
server dependencies before 2026-10-15) must be on it, so upgraded houses
lose them too. Pure file checks: no DB, no network, never skips.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from domovoi.tests.test_dependency_licences import REPLACED, _imported_roots

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "linux" / "apply-update.sh"

#: Every file a server's venv is installed from (apply-update.sh sync_deps,
#: docs/LINUX_HOST.md), besides pyproject.toml.
SERVER_LOCKS = ("requirements.lock", "requirements-linux-py314.lock",
                "requirements-linux-py314-no-deps.lock")
SERVER_INS = ("requirements-linux-py314.in", "requirements-no-deps.in")

#: distribution -> the import name it provides, for each retired package.
IMPORT_NAME = {
    "piper-tts": "piper",
    "piper-phonemize": "piper_phonemize",
    "edge-tts": "edge_tts",
    "torch": "torch",
    "resemblyzer": "resemblyzer",
    "mutagen": "mutagen",
    "python-mpd2": "mpd",
    "lameenc": "lameenc",
    "librosa": "librosa",
    "soxr": "soxr",
    "soundfile": "soundfile",
    "numba": "numba",
    "llvmlite": "llvmlite",
    "lazy-loader": "lazy_loader",
    "pooch": "pooch",
    "scikit-learn": "sklearn",
    "joblib": "joblib",
    "threadpoolctl": "threadpoolctl",
    "av": "av",
}

#: The code that runs in the server's venv: core, dashboard, bundled plugins.
SERVER_CODE = ("domovoi", "web", "plugins")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def retired_packages(text: str | None = None) -> list[str]:
    text = SCRIPT.read_text(encoding="utf-8") if text is None else text
    m = re.search(r"^RETIRED_PACKAGES=\(([^)]*)\)", text, re.M)
    assert m, f"RETIRED_PACKAGES not found in {SCRIPT.name}"
    return m.group(1).split()


def _lock_pins(path: Path) -> set[str]:
    return {_norm(m.group(1)) for m in
            re.finditer(r"^([A-Za-z0-9_.-]+)==", path.read_text(encoding="utf-8"), re.M)}


def _requirement_lines(path: Path) -> set[str]:
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "-")):
            out.add(_norm(re.split(r"[\s<>=!~\[;@]", line, maxsplit=1)[0]))
    return out


def _server_requirements() -> dict[str, set[str]]:
    found = {name: _lock_pins(REPO / name) for name in SERVER_LOCKS}
    found |= {name: _requirement_lines(REPO / name) for name in SERVER_INS}
    for lock in sorted(REPO.glob("plugins/*/requirements*.lock")):
        found[str(lock.relative_to(REPO))] = _lock_pins(lock)
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    reqs = list(project["dependencies"])
    for extra in project.get("optional-dependencies", {}).values():
        reqs += extra
    found["pyproject.toml"] = {_norm(re.split(r"[\s<>=!~\[;@]", r.strip(), maxsplit=1)[0]) for r in reqs}
    return found


def test_the_list_parses() -> None:
    names = retired_packages()
    assert "piper-tts" in names and "av" in names
    assert len(names) == len({_norm(n) for n in names}), "a package is listed twice"


def test_no_retired_package_is_still_installed_on_purpose() -> None:
    retired = {_norm(n) for n in retired_packages()}
    hits = sorted(f"{where} has {pkg}" for where, names in _server_requirements().items()
                  for pkg in names & retired)
    assert hits == [], "an update would uninstall what the server installs: " + "; ".join(hits)


def test_every_retired_package_has_its_import_name() -> None:
    missing = {_norm(n) for n in retired_packages()} - IMPORT_NAME.keys()
    assert missing == set(), f"add the import name of {sorted(missing)} to IMPORT_NAME"


def test_no_server_code_imports_a_retired_package() -> None:
    imports = {IMPORT_NAME[_norm(n)]: n for n in retired_packages() if _norm(n) in IMPORT_NAME}
    hits = []
    for top in SERVER_CODE:
        for path in (REPO / top).rglob("*.py"):
            if "node_modules" in path.parts or ".venv" in path.parts or "tests" in path.parts:
                continue
            for root in _imported_roots(path) & imports.keys():
                hits.append(f"{path.relative_to(REPO)} imports {root} ({imports[root]})")
    assert hits == []


def test_every_licence_banned_package_is_retired() -> None:
    """All of test_dependency_licences.REPLACED were server dependencies
    until 2026-10-15, so upgraded houses still have them unless the update
    removes them."""
    retired = {_norm(n) for n in retired_packages()}
    missing = sorted({_norm(dist) for dist, _, _ in REPLACED.values()} - retired)
    assert missing == [], f"add {missing} to RETIRED_PACKAGES in {SCRIPT.name}"


def test_the_parser_reads_a_multi_line_list() -> None:
    text = "x=1\nRETIRED_PACKAGES=(a b\n  c-d e)\nfoo() {\n"
    assert retired_packages(text) == ["a", "b", "c-d", "e"]
