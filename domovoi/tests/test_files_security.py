"""Containment + registry-core tests for the Files tab (design §1–§4).

Pure-unit (no DB): the security primitives in
``web/backend/api/files_security.py`` are exercised adversarially —
traversal (``..``, absolute, drive-letter, UNC), symlink escape, config-dir /
secret exclusion, root validation, removable detection stubbing, and
plugin-root resolution incl. the denylist.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from web.backend.api import files_security as fs


# ─── safe_join ──────────────────────────────────────────────────────────────
def test_safe_join_accepts_plain_rel(tmp_path):
    root = tmp_path.resolve()
    (root / "sub").mkdir()
    assert fs.safe_join(root, "sub") == (root / "sub")
    assert fs.safe_join(root, "a/b/c") == (root / "a" / "b" / "c").resolve()


def test_safe_join_empty_is_root(tmp_path):
    root = tmp_path.resolve()
    assert fs.safe_join(root, "") == root
    assert fs.safe_join(root, None) == root
    assert fs.safe_join(root, "   ") == root


def test_safe_join_rejects_parent_traversal(tmp_path):
    root = tmp_path.resolve()
    with pytest.raises(HTTPException) as ei:
        fs.safe_join(root, "../etc/passwd")
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException):
        fs.safe_join(root, "sub/../../escape")


def test_safe_join_rejects_drive_absolute(tmp_path):
    root = tmp_path.resolve()
    with pytest.raises(HTTPException) as ei:
        fs.safe_join(root, "C:/Windows/System32")
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException):
        fs.safe_join(root, "d:\\secrets\\x")


def test_safe_join_rejects_unc(tmp_path):
    root = tmp_path.resolve()
    with pytest.raises(HTTPException) as ei:
        fs.safe_join(root, "//attacker/share/x")
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException):
        fs.safe_join(root, "\\\\attacker\\share")


def test_safe_join_backslashes_normalized(tmp_path):
    root = tmp_path.resolve()
    assert fs.safe_join(root, "a\\b") == (root / "a" / "b").resolve()


def _can_symlink(tmp_path) -> bool:
    try:
        (tmp_path / "_probe_target").mkdir()
        os.symlink(tmp_path / "_probe_target", tmp_path / "_probe_link")
        return True
    except (OSError, NotImplementedError):
        return False


def test_safe_join_rejects_symlink_escape_at_root(tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("symlink creation not permitted on this host")
    root = (tmp_path / "lib").resolve()
    root.mkdir()
    outside = (tmp_path / "outside").resolve()
    outside.mkdir()
    (outside / "secret.txt").write_text("boo", encoding="utf-8")
    os.symlink(outside, root / "escape")
    # The symlinked dir resolves outside the root → rejected.
    with pytest.raises(HTTPException) as ei:
        fs.safe_join(root, "escape")
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException):
        fs.safe_join(root, "escape/secret.txt")


# ─── _is_sensitive / validate_root ──────────────────────────────────────────
def test_is_sensitive_config_dir_and_secrets(tmp_path, monkeypatch):
    config = (tmp_path / "dot-domovoi").resolve()
    config.mkdir()
    monkeypatch.setattr(fs, "CONFIG_DIR", config)
    audiobooks = config / "audiobooks"
    audiobooks.mkdir()
    allowed = {audiobooks.resolve()}

    # The config dir itself and secret siblings are sensitive.
    assert fs._is_sensitive(config, allowed) is True
    (config / "tls").mkdir()
    assert fs._is_sensitive(config / "tls", allowed) is True
    # The explicitly-allowed media subdir is NOT sensitive.
    assert fs._is_sensitive(audiobooks, allowed) is False
    assert fs._is_sensitive(audiobooks / "book", allowed) is False
    # Anything outside the config dir is fine.
    assert fs._is_sensitive(tmp_path / "Music", allowed) is False


def test_validate_root_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "CONFIG_DIR", (tmp_path / "cfg").resolve())
    good = tmp_path / "Music"
    good.mkdir()
    assert fs.validate_root(good) is True
    # Nonexistent / a file / a drive root are refused.
    assert fs.validate_root(tmp_path / "nope") is False
    afile = tmp_path / "a.txt"
    afile.write_text("x", encoding="utf-8")
    assert fs.validate_root(afile) is False
    assert fs.validate_root(Path(good.anchor)) is False


def test_validate_root_rejects_config_secret(tmp_path, monkeypatch):
    config = (tmp_path / "cfg").resolve()
    config.mkdir()
    monkeypatch.setattr(fs, "CONFIG_DIR", config)
    secret = config / "tls"
    secret.mkdir()
    # A candidate root INSIDE the config dir that isn't an allowed media
    # subdir is refused even though it exists and is a directory.
    assert fs.validate_root(secret, set()) is False
    # But an allowed subdir passes.
    ab = config / "audiobooks"
    ab.mkdir()
    assert fs.validate_root(ab, {ab.resolve()}) is True


# ─── drive_token / removable ────────────────────────────────────────────────
def test_drive_token():
    assert fs.drive_token("E:\\") == "E"
    assert fs.drive_token("e:/") == "E"
    assert fs.drive_token("/media/kamron/sdb1") == "sdb1"
    assert fs.drive_token("/media/usb/") == "usb"


def test_build_removable_libraries_stub(tmp_path, monkeypatch):
    drive = (tmp_path / "USBDRIVE").resolve()
    drive.mkdir()
    monkeypatch.setattr(
        fs, "detect_removable",
        lambda: [{"mount": str(drive), "device": "/dev/sdb1", "read_only": False}],
    )
    libs = fs.build_removable_libraries()
    assert len(libs) == 1
    lib = libs[0]
    assert lib.kind == "removable"
    assert lib.editable is False and lib.importable is False
    assert lib.root_path == drive
    # root_path is never serialized to the client.
    assert "root_path" not in lib.public()
    assert lib.public()["present"] is True


def test_detect_removable_never_raises(monkeypatch):
    def _boom():
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(fs, "_removable_via_psutil", _boom)
    monkeypatch.setattr(fs, "_removable_windows_ctypes", _boom)
    monkeypatch.setattr(fs, "_removable_linux", _boom)
    # Even with every probe raising, detection degrades to [] (never 500s).
    assert fs.detect_removable() == []


# ─── resolve_plugin_root (base vocabulary + denylist) ───────────────────────
def test_resolve_plugin_root_install_dir(tmp_path):
    install = tmp_path / "plugin-install"
    (install / "assets").mkdir(parents=True)
    decl = {"id": "roms", "label": "ROMs", "base": "install_dir", "path": "assets"}
    roots = fs.resolve_plugin_root(decl, install_dir=str(install), slug="romm")
    assert roots == [(install / "assets").resolve()]


def test_resolve_plugin_root_absolute(tmp_path):
    d = tmp_path / "Games" / "romm"
    d.mkdir(parents=True)
    decl = {"id": "roms", "label": "ROMs", "base": "absolute", "path": str(d)}
    roots = fs.resolve_plugin_root(decl, install_dir=None, slug="romm")
    assert roots == [d.resolve()]


def test_resolve_plugin_root_config_single(tmp_path, monkeypatch):
    config = (tmp_path / "cfg").resolve()
    (config / "plugins").mkdir(parents=True)
    monkeypatch.setattr(fs, "CONFIG_DIR", config)
    media = tmp_path / "roms-library"
    media.mkdir()
    (config / "plugins" / "romm.env").write_text(
        f"ROMM_LIBRARY_DIR={media}\n", encoding="utf-8"
    )
    decl = {"id": "roms", "label": "ROMs", "base": "config", "path": "library_dir"}
    roots = fs.resolve_plugin_root(
        decl, install_dir=None, slug="romm", env_prefix="ROMM_"
    )
    assert roots == [media.resolve()]


def test_resolve_plugin_root_config_multi_separator(tmp_path, monkeypatch):
    config = (tmp_path / "cfg").resolve()
    (config / "plugins").mkdir(parents=True)
    monkeypatch.setattr(fs, "CONFIG_DIR", config)
    a = tmp_path / "videos-a"
    b = tmp_path / "videos-b"
    a.mkdir()
    b.mkdir()
    (config / "plugins" / "jellyfin.env").write_text(
        f"JELLYFIN_MEDIA_DIRS={a};{b}\n", encoding="utf-8"
    )
    decl = {
        "id": "videos", "label": "Videos", "base": "config",
        "path": "media_dirs", "separator": ";", "read_only": True,
    }
    roots = fs.resolve_plugin_root(
        decl, install_dir=None, slug="jellyfin", env_prefix="JELLYFIN_"
    )
    assert roots == [a.resolve(), b.resolve()]


def test_resolve_plugin_root_config_missing_key_skips(tmp_path, monkeypatch):
    config = (tmp_path / "cfg").resolve()
    (config / "plugins").mkdir(parents=True)
    monkeypatch.setattr(fs, "CONFIG_DIR", config)
    # No .env file at all → nothing resolves (library silently skipped).
    decl = {"id": "roms", "label": "ROMs", "base": "config", "path": "library_dir"}
    assert fs.resolve_plugin_root(decl, install_dir=None, slug="romm") == []


def test_resolve_plugin_root_denylists_sensitive(tmp_path, monkeypatch):
    config = (tmp_path / "cfg").resolve()
    (config / "tls").mkdir(parents=True)
    monkeypatch.setattr(fs, "CONFIG_DIR", config)
    # A mis-set absolute config pointing INTO the secret config dir must
    # resolve to nothing (validate_root rejects the sensitive path).
    decl = {"id": "x", "label": "X", "base": "absolute", "path": str(config / "tls")}
    assert fs.resolve_plugin_root(decl, install_dir=None, slug="romm") == []
    # And the config dir root itself.
    decl2 = {"id": "y", "label": "Y", "base": "absolute", "path": str(config)}
    assert fs.resolve_plugin_root(decl2, install_dir=None, slug="romm") == []


def test_resolve_plugin_root_unknown_base(tmp_path):
    decl = {"id": "x", "label": "X", "base": "nonsense", "path": "foo"}
    assert fs.resolve_plugin_root(decl, install_dir=None, slug="romm") == []


# ─── build_plugin_libraries (merge shape) ───────────────────────────────────
def test_build_plugin_libraries_read_only_flags(tmp_path):
    install = tmp_path / "inst"
    (install / "lib").mkdir(parents=True)
    rows = [
        {
            "slug": "romm", "name": "RomM", "enabled": True, "status": "ok",
            "install_dir": str(install),
            "manifest": {
                "media_libraries": [
                    {"id": "roms", "label": "ROM library", "base": "install_dir",
                     "path": "lib", "read_only": False},
                ]
            },
        }
    ]
    libs = fs.build_plugin_libraries(rows, set())
    assert len(libs) == 1
    lib = libs[0]
    assert lib.id == "plugin:romm:roms"
    assert lib.kind == "plugin"
    assert lib.editable is True and lib.importable is True  # read_only=False
    assert lib.label == "RomM · ROM library"


def test_build_plugin_libraries_skips_disabled(tmp_path):
    install = tmp_path / "inst"
    (install / "lib").mkdir(parents=True)
    rows = [
        {
            "slug": "romm", "name": "RomM", "enabled": False, "status": "ok",
            "install_dir": str(install),
            "manifest": {"media_libraries": [
                {"id": "roms", "label": "ROMs", "base": "install_dir", "path": "lib"}
            ]},
        }
    ]
    assert fs.build_plugin_libraries(rows, set()) == []


# ─── build_core_libraries (config exclusion) ────────────────────────────────
def test_build_core_libraries_excludes_missing_and_secrets(tmp_path, monkeypatch):
    from domovoi.config import settings

    music = tmp_path / "Music"
    docs = tmp_path / "Documents"
    music.mkdir()
    docs.mkdir()
    monkeypatch.setattr(settings, "music_dir", str(music), raising=False)
    monkeypatch.setattr(settings, "documents_dir", str(docs), raising=False)
    # Point audiobooks/podcasts at nonexistent dirs so they drop out.
    monkeypatch.setattr(settings, "audiobooks_dir", str(tmp_path / "nope-ab"), raising=False)
    monkeypatch.setattr(settings, "podcasts_dir", str(tmp_path / "nope-pod"), raising=False)

    allowed = fs._allowed_under_config()
    libs = fs.build_core_libraries(allowed)
    ids = {lib.id for lib in libs}
    assert "core:music" in ids
    assert "core:documents" in ids
    assert "core:audiobooks" not in ids  # missing dir → skipped
    # documents carries the doc-editing affordance + reindex kind.
    docs_lib = next(lib for lib in libs if lib.id == "core:documents")
    assert docs_lib.doc_editing is True
    assert docs_lib.reindex_kind == "documents"
    music_lib = next(lib for lib in libs if lib.id == "core:music")
    assert music_lib.reindex_kind == "music"
    assert music_lib.doc_editing is False
