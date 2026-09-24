"""Containment + registry-core tests for the Files tab (design §1–§4).

Pure-unit (no DB): the security primitives in
``web/backend/api/files_security.py`` are exercised adversarially —
traversal (``..``, absolute, drive-letter, UNC), symlink escape, config-dir /
secret exclusion, root validation, removable detection stubbing, and
plugin-root resolution incl. the denylist.
"""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path
from types import SimpleNamespace

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


# ─── Boot-time creation of the core library dirs ────────────────────────────
# Regression cover for the Documents/Pictures disappearance: ``documents_dir``
# defaults to ``~/Documents`` and ``pictures_dir`` to ``~/Pictures`` — XDG
# desktop conventions a headless server account does not have — so
# ``validate_root`` failed, ``build_core_libraries`` skipped them silently, and
# the Files page lost both libraries plus the "+ New document" menu.


@pytest.fixture
def media_env(tmp_path, monkeypatch):
    """A self-contained fake install: fake home, fake CONFIG_DIR, and a
    ``core_settings`` stand-in carrying the five library paths (all missing)."""
    home = tmp_path / "home"
    home.mkdir()
    config_dir = home / ".domovoi"
    fake_settings = SimpleNamespace(
        music_dir=str(home / "Music"),
        audiobooks_dir=str(config_dir / "audiobooks"),
        podcasts_dir=str(config_dir / "podcasts"),
        documents_dir=str(home / "Documents"),
        pictures_dir=str(home / "Pictures"),
    )
    monkeypatch.setattr(fs, "core_settings", fake_settings)
    monkeypatch.setattr(fs, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(fs, "_home_dir", lambda: home.resolve())
    fs._SKIP_WARNED.clear()
    yield SimpleNamespace(home=home, config_dir=config_dir, settings=fake_settings)
    fs._SKIP_WARNED.clear()


def _status(results, lib_id):
    for r in results:
        if r.library_id == lib_id:
            return r
    raise AssertionError(f"{lib_id} missing from {[r.library_id for r in results]}")


def test_ensure_creates_every_missing_library_dir(media_env):
    for attr in ("music_dir", "audiobooks_dir", "podcasts_dir", "documents_dir",
                 "pictures_dir"):
        assert not Path(getattr(media_env.settings, attr)).exists()

    results = fs.ensure_core_library_dirs()

    assert {r.library_id for r in results} == {lib[0] for lib in fs.CORE_LIBRARIES}
    for lib_id, _label, _icon, attr, *_rest in fs.CORE_LIBRARIES:
        assert _status(results, lib_id).status == "created"
        assert Path(getattr(media_env.settings, attr)).is_dir()


def test_ensure_is_driven_by_the_table_not_a_copied_list(media_env, monkeypatch):
    """A library added to CORE_LIBRARIES later is covered automatically."""
    extra_dir = media_env.home / "Widgets"
    monkeypatch.setattr(
        media_env.settings, "widgets_dir", str(extra_dir), raising=False
    )
    monkeypatch.setattr(
        fs,
        "CORE_LIBRARIES",
        fs.CORE_LIBRARIES
        + (("core:widgets", "Widgets", "box", "widgets_dir", True, True, "widgets", False),),
    )

    results = fs.ensure_core_library_dirs()

    assert _status(results, "core:widgets").status == "created"
    assert extra_dir.is_dir()


def test_ensure_leaves_an_existing_dir_exactly_as_it_was(media_env, monkeypatch):
    docs = Path(media_env.settings.documents_dir)
    docs.mkdir(parents=True)
    (docs / "notes.md").write_text("keep me", encoding="utf-8")
    os.chmod(docs, 0o750)
    before = docs.stat()

    calls: list[str] = []
    real_mkdir = Path.mkdir

    def spy_mkdir(self, *a, **kw):
        calls.append(str(self))
        return real_mkdir(self, *a, **kw)

    monkeypatch.setattr(Path, "mkdir", spy_mkdir)

    results = fs.ensure_core_library_dirs()

    assert _status(results, "core:documents").status == "exists"
    # mkdir was never even called for the existing dir → no permission/mtime churn
    assert str(docs) not in calls
    after = docs.stat()
    assert after.st_mode == before.st_mode
    assert after.st_mtime_ns == before.st_mtime_ns
    assert (docs / "notes.md").read_text(encoding="utf-8") == "keep me"


def test_ensure_refuses_a_path_that_is_a_file(media_env, caplog):
    docs = Path(media_env.settings.documents_dir)
    docs.parent.mkdir(parents=True, exist_ok=True)
    docs.write_text("not a directory", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="web.backend.api.files_security"):
        results = fs.ensure_core_library_dirs()

    row = _status(results, "core:documents")
    assert row.status == "refused"
    assert "not a directory" in (row.reason or "")
    assert docs.read_text(encoding="utf-8") == "not a directory"  # not clobbered
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("core:documents" in m and "DOCUMENTS_DIR" in m and str(docs) in m
               for m in warned)
    # boot continued: the other four were still created
    assert _status(results, "core:music").status == "created"


def test_ensure_refuses_a_drive_or_filesystem_root(media_env, caplog):
    root = Path(media_env.home.anchor)
    media_env.settings.documents_dir = str(root)

    with caplog.at_level(logging.WARNING, logger="web.backend.api.files_security"):
        results = fs.ensure_core_library_dirs()

    row = _status(results, "core:documents")
    assert row.status == "refused"
    assert "root" in (row.reason or "")
    assert any("core:documents" in r.getMessage() for r in caplog.records
               if r.levelno >= logging.WARNING)
    assert _status(results, "core:pictures").status == "created"


def test_ensure_refuses_a_sensitive_config_dir_path(media_env, caplog):
    secret = media_env.config_dir / "tls"
    media_env.settings.documents_dir = str(secret)

    with caplog.at_level(logging.WARNING, logger="web.backend.api.files_security"):
        results = fs.ensure_core_library_dirs()

    row = _status(results, "core:documents")
    assert row.status == "refused"
    assert "config dir" in (row.reason or "")
    assert not secret.exists()  # never created
    # …while the two legitimately-under-config libraries still are
    assert _status(results, "core:podcasts").status == "created"
    assert _status(results, "core:audiobooks").status == "created"


def test_ensure_never_creates_an_unmounted_mountpoint(media_env, caplog, tmp_path):
    """The mounted-media rule: a path outside home is never created, even when
    its parent exists — that parent may be an empty mountpoint waiting for its
    drive, and an empty dir there would mask the mount."""
    mountpoint = tmp_path / "mnt" / "media"
    mountpoint.mkdir(parents=True)  # exists, empty — the drive is not up yet
    media_env.settings.music_dir = str(mountpoint / "music")

    with caplog.at_level(logging.WARNING, logger="web.backend.api.files_security"):
        results = fs.ensure_core_library_dirs()

    row = _status(results, "core:music")
    assert row.status == "refused"
    assert "outside the home directory" in (row.reason or "")
    assert list(mountpoint.iterdir()) == []  # mountpoint still pristine
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("MUSIC_DIR" in m and "core:music" in m for m in warned)


def test_ensure_survives_a_full_or_read_only_disk(media_env, monkeypatch, caplog):
    def boom(self, *a, **kw):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "mkdir", boom)

    with caplog.at_level(logging.WARNING, logger="web.backend.api.files_security"):
        results = fs.ensure_core_library_dirs()  # must not raise

    assert {r.status for r in results} == {"failed"}
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("core:documents" in m and "DOCUMENTS_DIR" in m for m in warned)


def test_ensure_survives_an_unreadable_setting(media_env, monkeypatch, caplog):
    monkeypatch.delattr(media_env.settings, "documents_dir", raising=False)

    with caplog.at_level(logging.WARNING, logger="web.backend.api.files_security"):
        results = fs.ensure_core_library_dirs()

    assert _status(results, "core:documents").status == "failed"
    assert _status(results, "core:music").status == "created"


# ─── The silence half: a skipped library must say so ────────────────────────
def test_build_core_libraries_warns_when_a_library_is_skipped(media_env, caplog):
    Path(media_env.settings.music_dir).mkdir(parents=True)  # only music exists

    with caplog.at_level(logging.WARNING, logger="web.backend.api.files_security"):
        libs = fs.build_core_libraries(fs._allowed_under_config())

    assert [lib.id for lib in libs] == ["core:music"]
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    for lib_id, env, path in (
        ("core:documents", "DOCUMENTS_DIR", media_env.settings.documents_dir),
        ("core:pictures", "PICTURES_DIR", media_env.settings.pictures_dir),
    ):
        assert any(
            lib_id in m and env in m and str(Path(path).resolve()) in m
            and "does not exist" in m
            for m in warned
        ), f"no warning naming {lib_id}/{env}/{path} in {warned}"


def test_build_core_libraries_warns_once_then_again_on_regression(media_env, caplog):
    allowed = fs._allowed_under_config()
    docs = Path(media_env.settings.documents_dir)

    with caplog.at_level(logging.WARNING, logger="web.backend.api.files_security"):
        fs.build_core_libraries(allowed)
        first = len([r for r in caplog.records
                     if r.levelno >= logging.WARNING and "core:documents" in r.getMessage()])
        fs.build_core_libraries(allowed)  # same problem → no repeat spam
        second = len([r for r in caplog.records
                      if r.levelno >= logging.WARNING and "core:documents" in r.getMessage()])
    assert first == 1 and second == 1

    docs.mkdir(parents=True)
    assert any(lib.id == "core:documents" for lib in fs.build_core_libraries(allowed))

    # …and if it disappears again, the operator is told again.
    for child in docs.iterdir():
        child.unlink()
    docs.rmdir()
    with caplog.at_level(logging.WARNING, logger="web.backend.api.files_security"):
        caplog.clear()
        fs.build_core_libraries(allowed)
    assert any("core:documents" in r.getMessage() for r in caplog.records
               if r.levelno >= logging.WARNING)


def test_ensure_then_build_restores_documents_and_pictures(media_env):
    """The end-to-end shape of Kamron's bug, DB-free."""
    before = {lib.id for lib in fs.build_core_libraries(fs._allowed_under_config())}
    assert "core:documents" not in before and "core:pictures" not in before

    fs.ensure_core_library_dirs()

    libs = fs.build_core_libraries(fs._allowed_under_config())
    by_id = {lib.id: lib for lib in libs}
    assert {lib[0] for lib in fs.CORE_LIBRARIES} <= set(by_id)
    # doc_editing is what makes the "+ New document/spreadsheet/drawing" menu render
    assert by_id["core:documents"].doc_editing is True


# ─── root_rejection: the reason strings validate_root used to swallow ───────
def test_root_rejection_matches_validate_root(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "CONFIG_DIR", tmp_path / ".domovoi")
    real = tmp_path / "lib"
    real.mkdir()
    a_file = tmp_path / "a.txt"
    a_file.write_text("x", encoding="utf-8")
    secret = tmp_path / ".domovoi" / "tls"
    secret.mkdir(parents=True)

    cases = {
        real: None,
        tmp_path / "nope": "does not exist",
        a_file: "not a directory",
        Path(tmp_path.anchor): "root",
        secret: "config dir",
    }
    for path, fragment in cases.items():
        reason = fs.root_rejection(path, set())
        if fragment is None:
            assert reason is None, (path, reason)
        else:
            assert reason and fragment in reason, (path, reason)
        assert fs.validate_root(path, set()) is (reason is None)
