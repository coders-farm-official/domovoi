"""Dependency floors and the committed lock (OPS-5).

Pure file checks against ``pyproject.toml`` and ``requirements.lock`` plus
the versions actually importable here — no DB, no network, never skips.

The floors below are the first releases of each package's fixed line for
the code paths Domovoi exposes to uploads and outbound fetches; they are
one-way (raise freely, never lower). The lock pins the exact, hashed set a
deployment installs, and it must satisfy the same floors.
"""

from __future__ import annotations

import re
import tomllib
from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCK = REPO_ROOT / "requirements.lock"

# package → lowest acceptable floor. Keep in step with the pyproject comment.
FLOORS = {
    "python-multipart": Version("0.0.18"),
    "starlette": Version("0.40"),
    "requests": Version("2.32"),
    "pillow": Version("10.3"),
}


def _core_requirements() -> dict[str, Requirement]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    reqs = [Requirement(r) for r in data["project"]["dependencies"]]
    return {r.name.lower(): r for r in reqs}


def _lower_bound(req: Requirement) -> Version | None:
    for spec in req.specifier:
        if spec.operator in (">=", "=="):
            return Version(spec.version)
    return None


def _locked_versions() -> dict[str, Version]:
    out: dict[str, Version] = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", line)
        if m:
            out[m.group(1).lower()] = Version(m.group(2))
    return out


# ─── pyproject floors ─────────────────────────────────────────────────────

@pytest.mark.parametrize("package", sorted(FLOORS))
def test_pyproject_declares_the_package_as_a_direct_core_dependency(package: str) -> None:
    """starlette in particular used to arrive only transitively via fastapi,
    whose own floor sits below the fixed line."""
    assert package in _core_requirements(), f"{package} is not a direct core dependency"


@pytest.mark.parametrize("package", sorted(FLOORS))
def test_pyproject_floor_is_at_or_above_the_fixed_line(package: str) -> None:
    req = _core_requirements()[package]
    floor = _lower_bound(req)
    assert floor is not None, f"{package} has no >= floor: {req}"
    assert floor >= FLOORS[package], f"{package} floor {floor} is below {FLOORS[package]}"


# ─── the lock ─────────────────────────────────────────────────────────────

def test_lock_file_exists_and_is_hash_pinned() -> None:
    assert LOCK.is_file(), "requirements.lock missing at the repo root"
    text = LOCK.read_text(encoding="utf-8")
    assert "--hash=sha256:" in text
    assert "pip-compile" in text        # regeneration recipe in the header
    assert "--require-hashes" in text   # install recipe in the header


def test_lock_pins_every_core_dependency() -> None:
    locked = _locked_versions()
    missing = [name for name in _core_requirements() if name not in locked]
    assert missing == [], f"core dependencies absent from requirements.lock: {missing}"


@pytest.mark.parametrize("package", sorted(FLOORS))
def test_lock_satisfies_the_floor(package: str) -> None:
    locked = _locked_versions()
    assert package in locked, f"{package} not in requirements.lock"
    assert locked[package] >= FLOORS[package], (
        f"requirements.lock pins {package}=={locked[package]}, below {FLOORS[package]}"
    )


def test_lock_pins_satisfy_every_pyproject_specifier() -> None:
    """A regenerated lock that drifts below a raised floor fails here."""
    locked = _locked_versions()
    for name, req in _core_requirements().items():
        assert req.specifier.contains(str(locked[name]), prereleases=True), (
            f"{name}=={locked[name]} does not satisfy {req}"
        )


# ─── this interpreter ─────────────────────────────────────────────────────

@pytest.mark.parametrize("package", sorted(FLOORS))
def test_installed_version_meets_the_floor(package: str) -> None:
    """The environment running the suite is on the fixed line too."""
    installed = Version(metadata.version(package))
    assert installed >= FLOORS[package], f"{package} {installed} installed, floor {FLOORS[package]}"
