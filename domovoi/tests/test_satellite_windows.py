"""The Windows satellite's runtime on the core (domovoi/satellite_windows.py
and the /v1/satellite-windows routes): the lock's shape, a cache that serves
only pinned bytes, a fetch held to every pin and refused when internet
access is off, and the routes' tiers. No DB, no network: the download is a
fake and the cache a tmp dir.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from domovoi import main, satellite_windows as sw

REPO = Path(__file__).resolve().parents[2]
LOCK_PATH = REPO / "satellite" / "windows-runtime.json"


def tiny_lock(files: dict[str, bytes]) -> dict:
    def entry(name, body):
        host = "https://www.python.org/ftp/python/3.14.8/" if name.endswith(".zip") else \
            "https://files.pythonhosted.org/packages/aa/bb/"
        return {"file": name, "url": host + name, "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body), "name": name.split("-")[0], "version": "1"}
    names = list(files)
    return {"lock_version": 1, "python": dict(entry(names[0], files[names[0]]), version="3.14.8"),
            "pip": entry(names[1], files[names[1]]),
            "wheels": [entry(n, files[n]) for n in names[2:]]}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    from domovoi.satellite_media import cache as media_cache

    monkeypatch.setattr(media_cache, "CACHE_ROOT", tmp_path / "cache")
    sw._verified.clear()
    return tmp_path / "cache" / "windows" / "3.14.8"


FILES = {"python-3.14.8-embed-amd64.zip": b"P" * 100, "pip-26.2.1-py3-none-any.whl": b"pip" * 10,
         "numpy-2.5.4-cp314-cp314-win_amd64.whl": b"N" * 300}


def test_the_tree_s_lock_loads():
    lock = sw.load_lock(LOCK_PATH)
    assert len(sw.entries(lock)) >= 25
    assert lock["python"]["url"].startswith("https://www.python.org/ftp/python/")


@pytest.mark.parametrize("mutate", [
    lambda d: d["wheels"][0].update(file="../x.whl"),
    lambda d: d["wheels"][0].update(url="https://evil.example/numpy.whl"),
    lambda d: d["wheels"][0].update(url="https://files.pythonhosted.org/packages/other.whl"),
    lambda d: d["wheels"][0].update(sha256="zz"),
    lambda d: d["wheels"][0].update(size=0),
    lambda d: d.update(lock_version=2),
])
def test_a_malformed_lock_is_refused(tmp_path, mutate):
    doc = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    mutate(doc)
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(sw.LockError):
        sw.load_lock(path)


def test_only_listed_and_pinned_bytes_are_served(cache):
    lock = tiny_lock(FILES)
    cache.mkdir(parents=True)
    for name, body in FILES.items():
        (cache / name).write_bytes(body)
    assert sw.cached_file(lock, "numpy-2.5.4-cp314-cp314-win_amd64.whl") == cache / "numpy-2.5.4-cp314-cp314-win_amd64.whl"
    assert sw.status(lock)["ready"] is True
    (cache / "numpy-2.5.4-cp314-cp314-win_amd64.whl").write_bytes(b"M" * 300)    # same size, other bytes
    assert sw.cached_file(lock, "numpy-2.5.4-cp314-cp314-win_amd64.whl") is None
    (cache / "evil.whl").write_bytes(b"x")
    for name in ("evil.whl", "..\\..\\x.whl", "../x.whl", "python-3.14.8-embed-amd64.zip/../x"):
        assert sw.cached_file(lock, name) is None
    st = sw.status(lock)
    assert st["ready"] is False and st["missing"] == ["numpy-2.5.4-cp314-cp314-win_amd64.whl"]


def test_the_fetch_keeps_only_pinned_files_and_drops_strays(cache, monkeypatch):
    from domovoi.satellite_media import fetchers

    lock = tiny_lock(FILES)
    monkeypatch.setattr(fetchers, "_turned_off", lambda what: None)
    fetched = []

    def fetch_pinned(url, dest, *, sha256, size):
        fetched.append(url)
        name = url.rsplit("/", 1)[-1]
        if name.startswith("numpy"):
            raise ValueError("checksum mismatch")
        dest.write_bytes(FILES[name])

    monkeypatch.setattr(fetchers, "_fetch_pinned", fetch_pinned)
    cache.mkdir(parents=True)
    (cache / "stray.whl").write_bytes(b"?")
    ok, message = sw.fetch(lock)
    assert not ok and "numpy" in message and "1 of 3" in message
    assert not (cache / "stray.whl").exists()
    assert all(u.startswith(("https://www.python.org/", "https://files.pythonhosted.org/")) for u in fetched)
    assert sw.status(lock)["missing"] == ["numpy-2.5.4-cp314-cp314-win_amd64.whl"]


def test_no_fetch_when_internet_access_is_off(cache, monkeypatch):
    from domovoi.satellite_media import fetchers

    monkeypatch.setattr(fetchers, "_turned_off", lambda what: (False, "internet access is turned off; using what is already cached"))
    monkeypatch.setattr(fetchers, "_fetch_pinned", lambda *a, **k: pytest.fail("fetched with internet off"))
    ok, message = sw.fetch(tiny_lock(FILES))
    assert not ok and "turned off" in message


async def test_the_file_route_serves_cached_pinned_files_and_404s_the_rest(cache, monkeypatch):
    async def open_channel(*a, **k):
        return None

    monkeypatch.setattr(main.container_freeze, "require_unfrozen", open_channel)
    lock = tiny_lock(FILES)
    monkeypatch.setattr(main, "_windows_lock", lambda: lock)
    cache.mkdir(parents=True)
    (cache / "pip-26.2.1-py3-none-any.whl").write_bytes(FILES["pip-26.2.1-py3-none-any.whl"])
    resp = await main.satellite_windows_file("pip-26.2.1-py3-none-any.whl")
    assert Path(resp.path) == cache / "pip-26.2.1-py3-none-any.whl"
    for name in ("numpy-2.5.4-cp314-cp314-win_amd64.whl", "..\\windows-runtime.json", "client.py"):
        with pytest.raises(HTTPException) as e:
            await main.satellite_windows_file(name)
        assert e.value.status_code == 404


async def test_a_frozen_channel_serves_no_windows_files(monkeypatch):
    async def frozen(*a, **k):
        raise HTTPException(status_code=423, detail="frozen")

    monkeypatch.setattr(main.container_freeze, "require_unfrozen", frozen)
    with pytest.raises(HTTPException) as e:
        await main.satellite_windows_file("pip-26.2.1-py3-none-any.whl")
    assert e.value.status_code == 423


def test_status_and_fetch_are_an_admin_s_and_the_files_are_public():
    from domovoi.admin_auth import require_admin_mutation, require_admin_read

    routes = {r.path: r for r in main.app.routes if hasattr(r, "dependant")}
    status = routes["/v1/admin/satellite-windows"]
    fetch = routes["/v1/admin/satellite-windows/fetch"]
    assert any(d.dependency is require_admin_read for d in status.dependencies)
    assert any(d.dependency is require_admin_mutation for d in fetch.dependencies)
    assert "POST" in fetch.methods and "GET" not in fetch.methods
    assert not routes["/v1/satellite-windows/files/{name}"].dependencies


def test_the_installer_travels_in_the_bundle():
    from domovoi import satellite_bundle

    files = satellite_bundle.manifest(main.SATELLITE_CODE_DIR, main._is_allowed_satellite_code)
    for rel in ("scripts/install-windows.ps1", "scripts/uninstall-windows.ps1", "windows-runtime.json",
                "win_install.py", "win_service.py", "win_tray.py", "win_player.py", "win32.py",
                "control_win.py", "requirements-windows.txt"):
        assert rel in files, rel
