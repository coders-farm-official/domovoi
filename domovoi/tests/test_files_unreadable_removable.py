"""One unreadable removable drive must not take the Files page down (F-055).

On a Windows host a card reader with an unformatted card (or a drive whose
file system Windows does not recognise) is still listed as a removable
drive, and ``Path.resolve`` on its root raises ``OSError`` [WinError 1005]
"The volume does not contain a recognized file system". That call sat
outside the ``try`` in ``build_removable_libraries``, so the error escaped
``build_libraries`` and every Files route answered 500 until the drive
became readable (rc2 smoke, 2026-10-16).

The drive is skipped now; the other libraries, removable ones included,
are listed as before. Pure unit, no DB: the plugin-row read is stubbed.
"""

from __future__ import annotations

import asyncio
import pathlib

from web.backend.api import files_security as fs


def _unreadable_resolve(bad: str):
    """``Path.resolve`` that fails the way Windows does for a volume with
    no recognised file system, for ``bad`` only."""
    real = pathlib.Path.resolve

    def resolve(self, strict=False):
        if str(self).rstrip("\\/") == bad.rstrip("\\/"):
            raise OSError(
                1005, "The volume does not contain a recognized file system", str(self)
            )
        return real(self, strict=strict)

    return resolve


def _two_drives(monkeypatch, tmp_path):
    good = (tmp_path / "STICK").resolve()
    good.mkdir()
    bad = str(tmp_path / "CARDREADER")
    monkeypatch.setattr(
        fs, "detect_removable",
        lambda: [
            {"mount": bad, "device": bad, "read_only": False},
            {"mount": str(good), "device": "/dev/sdb1", "read_only": False},
        ],
    )
    monkeypatch.setattr(pathlib.Path, "resolve", _unreadable_resolve(bad))
    return good


def test_an_unreadable_drive_is_skipped_and_the_rest_listed(monkeypatch, tmp_path):
    good = _two_drives(monkeypatch, tmp_path)
    libs = fs.build_removable_libraries()
    assert [lib.root_path for lib in libs] == [good]


def test_build_libraries_survives_an_unreadable_drive(monkeypatch, tmp_path):
    """The registry every Files route resolves through (/libraries,
    /browse, /download, Documents, Videos, Pictures) still builds."""
    good = _two_drives(monkeypatch, tmp_path)

    async def no_plugins():
        return []

    from web.backend import plugin_host

    monkeypatch.setattr(plugin_host, "fetch_plugin_rows", no_plugins)
    libs = asyncio.run(fs.build_libraries())
    removable = [lib for lib in libs if lib.kind == "removable"]
    assert [lib.root_path for lib in removable] == [good]


def test_an_unreadable_drive_is_logged_once(monkeypatch, tmp_path, caplog):
    """Every Files call rebuilds the registry, so a drive that stays
    unreadable is said once, not once per request."""
    _two_drives(monkeypatch, tmp_path)
    monkeypatch.setattr(fs, "_UNREADABLE_SEEN", set())
    with caplog.at_level("INFO", logger=fs.log.name):
        for _ in range(3):
            fs.build_removable_libraries()
    said = [r for r in caplog.records if "can't be read" in r.getMessage()]
    assert len(said) == 1
