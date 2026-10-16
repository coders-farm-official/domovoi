"""Every copy of the project's version says the same thing.

The server's version is written in pyproject.toml (PEP 440: ``1.0.0rc1``)
and copied by hand into the places that show or send it; the Android app's
versionName is the same version in SemVer spelling (``1.0.0-rc1``), and the
GitHub release is tagged ``v<versionName>`` (domovoi.android_app.tag_for).
Until they all read one source (design-notes V1-RELEASE-PLAN 1.1), this
test fails the moment one is bumped without the others. Pure file reads:
no DB, no network, never skips. The Windows Setup says the same SemVer
version (``AppVersion``), and its file versions are the numeric X.Y.Z.0.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _pyproject_version() -> str:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def _semver(pep440: str) -> str:
    """``1.0.0rc1`` -> ``1.0.0-rc1``; ``1.0.0`` stays."""
    m = re.fullmatch(r"(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?", pep440)
    assert m, f"pyproject's version {pep440!r} is not X.Y.Z or X.Y.Z(a|b|rc)N"
    base, pre, n = m.groups()
    if not pre:
        return base
    return f"{base}-{ {'a': 'alpha', 'b': 'beta', 'rc': 'rc'}[pre] }{n}"


def _one(path: str, pattern: str) -> str:
    text = (REPO / path).read_text(encoding="utf-8")
    found = re.findall(pattern, text, flags=re.MULTILINE)
    assert len(found) == 1, f"{path}: expected one match of {pattern!r}, found {found!r}"
    return found[0]


def test_the_server_copies_match_pyproject() -> None:
    version = _pyproject_version()
    copies = {
        "web/backend/api/capabilities.py SERVER_VERSION":
            _one("web/backend/api/capabilities.py", r'^SERVER_VERSION = "([^"]+)"'),
        "web/backend/api/config.py WEB_VERSION":
            _one("web/backend/api/config.py", r'^WEB_VERSION = "([^"]+)"'),
        "domovoi/lyrics/__init__.py fallback":
            _one("domovoi/lyrics/__init__.py", r'^\s+return "(\d[^"]*)"\s*$'),
        "domovoi/clients/musicbrainz.py USER_AGENT":
            _one("domovoi/clients/musicbrainz.py", r'USER_AGENT = "domovoi/([^ ]+) '),
        "domovoi/plugins_runtime/installer.py User-Agent":
            _one("domovoi/plugins_runtime/installer.py", r'"User-Agent": "domovoi/([^ ]+) '),
    }
    assert {k: v for k, v in copies.items() if v != version} == {}, f"pyproject says {version}"


def test_the_android_version_name_is_the_same_version() -> None:
    name = _one("android/app/build.gradle.kts", r'^\s*versionName = "([^"]+)"\s*$')
    assert name == _semver(_pyproject_version())


def test_the_release_tag_is_v_and_the_version_name() -> None:
    from domovoi import android_app

    name = _one("android/app/build.gradle.kts", r'^\s*versionName = "([^"]+)"\s*$')
    assert android_app.tag_for(name) == f"v{name}"
    for script in ("android/scripts/release.ps1", "android/scripts/release.sh"):
        text = (REPO / script).read_text(encoding="utf-8")
        assert re.search(r'^\$?tag ?= ?"v\$(versionName|version_name)"\s*$', text, flags=re.MULTILINE), script
        assert "android-v" not in text, script


def test_the_windows_setup_says_the_same_version() -> None:
    """DomovoiSetup.exe and its two programs: the SemVer spelling where text
    is shown (the wizard, Installed apps), X.Y.Z.0 where Windows wants four
    numbers (a pre-release has no place there)."""
    semver = _semver(_pyproject_version())
    numeric = re.match(r"\d+\.\d+\.\d+", semver).group(0) + ".0"
    iss = "windows/installer/DomovoiSetup.iss"
    assert _one(iss, r'^#define AppVersion "([^"]+)"$') == semver
    assert _one(iss, r"^VersionInfoVersion=(\S+)$") == numeric
    assert _one(iss, r"^VersionInfoProductTextVersion=(\S+)$") == "{#AppVersion}"
    asm = "windows/common/AssemblyInfo.cs"
    assert _one(asm, r'^\[assembly: AssemblyInformationalVersion\("([^"]+)"\)\]$') == semver
    assert _one(asm, r'^\[assembly: AssemblyVersion\("([^"]+)"\)\]$') == numeric
    assert _one(asm, r'^\[assembly: AssemblyFileVersion\("([^"]+)"\)\]$') == numeric
