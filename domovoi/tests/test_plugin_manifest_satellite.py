"""[satellite] manifest section — schema accept/reject matrix + directory
validation + the payload_files enumeration guards."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from domovoi.plugins_runtime.manifest import (
    ManifestError,
    parse_manifest,
    validate_plugin_dir,
)
from domovoi.satellite_payload import payload_files


def _manifest(satellite: str = "", perms: str = "") -> str:
    return f"""
[plugin]
slug = "paytest"
name = "Payload Test"
version = "1.0.0"
publisher = "Coders Farm"
license = "MIT"
description = "Satellite payload manifest tests."
domovoi_api = ">=1.0,<2.0"

[entry_points]
core = "domovoi_plugin_paytest.core"

[permissions]
{perms}

{satellite}
"""


def test_files_only_payload_needs_no_root_permission() -> None:
    m = parse_manifest(_manifest('[satellite]\nfiles_dir = "satellite_payload"'))
    assert m.satellite is not None
    assert m.satellite.files_dir == "satellite_payload"
    assert m.satellite.apt_packages == ()
    assert m.permissions["satellite_root"] is False


def test_full_declaration_parses() -> None:
    m = parse_manifest(_manifest(
        '[satellite]\n'
        'apt_packages = ["libfoo2", "mpg123"]\n'
        'pip_requirements = ["somepkg==1.2.3"]\n'
        'pip_lockfile = "satellite-requirements.lock"\n'
        'files_dir = "satellite_payload"\n'
        'post_install = "satellite_payload/post_install.sh"\n'
        'max_payload_mb = 16\n',
        perms='satellite_root = true\nwarnings = ["Installs libfoo2 on satellites."]',
    ))
    sat = m.satellite
    assert sat is not None
    assert sat.apt_packages == ("libfoo2", "mpg123")
    assert sat.pip_requirements == ("somepkg==1.2.3",)
    assert sat.max_payload_mb == 16


@pytest.mark.parametrize(
    ("satellite", "perms", "match"),
    [
        # apt/post_install without the root permission
        ('[satellite]\napt_packages = ["libfoo2"]', "", "satellite_root"),
        (
            '[satellite]\npost_install = "p.sh"\nfiles_dir = "d"',
            "",
            "satellite_root",
        ),
        # satellite_root without a warnings entry
        (
            '[satellite]\napt_packages = ["libfoo2"]',
            "satellite_root = true",
            "warnings",
        ),
        # bad apt name
        (
            '[satellite]\napt_packages = ["Bad Name!"]',
            'satellite_root = true\nwarnings = ["w"]',
            "Debian package name",
        ),
        # unpinned pip
        (
            '[satellite]\npip_requirements = ["somepkg>=1"]\npip_lockfile = "l.lock"',
            "",
            "exact pin",
        ),
        # pip without lockfile
        (
            '[satellite]\npip_requirements = ["somepkg==1.2.3"]',
            "",
            "pip_lockfile",
        ),
        # empty table
        ("[satellite]", "", "declares nothing"),
        # bad cap
        (
            '[satellite]\nfiles_dir = "d"\nmax_payload_mb = 0',
            "",
            "max_payload_mb",
        ),
    ],
)
def test_rejected_declarations(satellite: str, perms: str, match: str) -> None:
    with pytest.raises(ManifestError, match=match):
        parse_manifest(_manifest(satellite, perms))


def _plugin_dir(tmp_path: Path, manifest_text: str) -> Path:
    root = tmp_path / "plugin"
    pkg = root / "domovoi_plugin_paytest"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("def register(ctx): ...", encoding="utf-8")
    (root / "domovoi-plugin.toml").write_text(manifest_text, encoding="utf-8")
    return root


def test_dir_validation_missing_files_dir(tmp_path) -> None:
    text = _manifest('[satellite]\nfiles_dir = "satellite_payload"')
    root = _plugin_dir(tmp_path, text)
    errors = validate_plugin_dir(root, parse_manifest(text))
    assert any("files_dir" in e for e in errors)


def test_dir_validation_post_install_needs_shebang(tmp_path) -> None:
    text = _manifest(
        '[satellite]\nfiles_dir = "satellite_payload"\n'
        'post_install = "satellite_payload/post_install.sh"',
        perms='satellite_root = true\nwarnings = ["w"]',
    )
    root = _plugin_dir(tmp_path, text)
    fd = root / "satellite_payload"
    fd.mkdir()
    (fd / "post_install.sh").write_text("echo no shebang\n", encoding="utf-8")
    errors = validate_plugin_dir(root, parse_manifest(text))
    assert any("shebang" in e for e in errors)

    (fd / "post_install.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    assert validate_plugin_dir(root, parse_manifest(text)) == []


def test_dir_validation_size_cap(tmp_path) -> None:
    text = _manifest(
        '[satellite]\nfiles_dir = "satellite_payload"\nmax_payload_mb = 1'
    )
    root = _plugin_dir(tmp_path, text)
    fd = root / "satellite_payload"
    fd.mkdir()
    (fd / "big.bin").write_bytes(b"\x00" * (2 * 1024 * 1024))
    errors = validate_plugin_dir(root, parse_manifest(text))
    assert any("payload cap" in e for e in errors)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privilege on Windows")
def test_dir_validation_rejects_symlinks(tmp_path) -> None:
    text = _manifest('[satellite]\nfiles_dir = "satellite_payload"')
    root = _plugin_dir(tmp_path, text)
    fd = root / "satellite_payload"
    fd.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    (fd / "link.txt").symlink_to(outside)
    errors = validate_plugin_dir(root, parse_manifest(text))
    assert any("symlink" in e for e in errors)


# ─── payload_files enumeration (pure function, no DB) ─────────────────────


def test_payload_files_collects_tree_and_named_files(tmp_path) -> None:
    root = tmp_path / "installed"
    fd = root / "satellite_payload"
    (fd / "dtbo").mkdir(parents=True)
    (fd / "dtbo" / "board.dtbo").write_bytes(b"\x01\x02")
    (fd / "tool").write_bytes(b"\x7fELF")
    (root / "post.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    decl = {
        "files_dir": "satellite_payload",
        "post_install": "post.sh",
        "pip_lockfile": None,
        "max_payload_mb": 64,
    }
    files = payload_files(root, decl)
    assert set(files) == {"dtbo/board.dtbo", "tool", "post.sh"}


def test_payload_files_over_cap_serves_nothing(tmp_path) -> None:
    root = tmp_path / "installed"
    fd = root / "payload"
    fd.mkdir(parents=True)
    (fd / "big.bin").write_bytes(b"\x00" * (2 * 1024 * 1024))
    decl = {"files_dir": "payload", "max_payload_mb": 1}
    assert payload_files(root, decl) == {}
