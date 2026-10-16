"""The satellite bundle a spare computer's install command downloads
(domovoi/satellite_bundle.py, ``/v1/satellite-code/bundle.tar.gz``).

It must be deterministic — the dashboard quotes its SHA-256 in the command
before anyone downloads it — and it must be exactly the satellite-code
channel's files: no caches, no secrets, none of a Pi's root helpers. No DB.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from domovoi import main, satellite_bundle

REPO = Path(__file__).resolve().parents[2]


def _members(data: bytes) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return {m.name: m for m in tar.getmembers()}


@pytest.fixture(autouse=True)
def fresh_cache():
    satellite_bundle.reset_cache()
    yield
    satellite_bundle.reset_cache()


def test_the_same_tree_gives_the_same_bytes():
    files = satellite_bundle.manifest(main.SATELLITE_CODE_DIR, main._is_allowed_satellite_code)
    a = satellite_bundle.build(main.SATELLITE_CODE_DIR, files)
    b = satellite_bundle.build(main.SATELLITE_CODE_DIR, files)
    assert a == b
    assert a[4:8] == b"\x00\x00\x00\x00", "no time in the gzip header"


def test_the_bundle_is_the_code_channel_s_files_under_satellite():
    bundle = satellite_bundle.current(main.SATELLITE_CODE_DIR, main._is_allowed_satellite_code)
    members = _members(bundle.data)
    files = {n for n, m in members.items() if m.isfile()}
    channel = satellite_bundle.manifest(main.SATELLITE_CODE_DIR, main._is_allowed_satellite_code)
    assert files == {f"satellite/{rel}" for rel in channel}
    assert "satellite/scripts/install-pc.sh" in files
    assert "satellite/client.py" in files and "satellite/enrolment.py" in files
    for helper in ("domovoi-apply-payload", "domovoi-sync-time", "domovoi-reboot"):
        assert f"satellite/scripts/{helper}" not in files, "a Pi's root helpers never travel"
    assert not any("__pycache__" in n or n.endswith((".pyc", ".bak")) or "/.env" in n for n in files)
    for m in members.values():
        assert m.mtime == 0 and m.uid == 0 and m.gid == 0 and m.uname == "" and m.gname == ""
        assert not m.issym() and not m.islnk()
    assert members["satellite/scripts/install-pc.sh"].mode == 0o755
    assert members["satellite/client.py"].mode == 0o644
    assert bundle.sha256 == hashlib.sha256(bundle.data).hexdigest()


def test_a_changed_file_makes_a_new_bundle(tmp_path):
    root = tmp_path / "satellite"
    (root / "scripts").mkdir(parents=True)
    (root / "a.py").write_text("one\n")
    (root / "scripts" / "install-pc.sh").write_text("#!/bin/sh\n")
    allowed = main._is_allowed_satellite_code
    first = satellite_bundle.current(root, allowed)
    assert satellite_bundle.current(root, allowed) is first, "kept while nothing changed"
    (root / "a.py").write_text("two\n")
    second = satellite_bundle.current(root, allowed)
    assert second.sha256 != first.sha256
    assert set(_members(second.data)) == {"satellite/scripts", "satellite/a.py",
                                          "satellite/scripts/install-pc.sh"}


async def test_the_routes_serve_the_bundle_and_its_digest(monkeypatch):
    async def open_channel(*a, **k):
        return None
    monkeypatch.setattr(main.container_freeze, "require_unfrozen", open_channel)
    digest = await main.satellite_code_bundle_digest()
    resp = await main.satellite_code_bundle()
    assert resp.media_type == "application/gzip"
    assert hashlib.sha256(resp.body).hexdigest() == digest["sha256"] == resp.headers["x-content-sha256"]
    assert digest["size"] == len(resp.body) and digest["path"] == "/v1/satellite-code/bundle.tar.gz"


def test_the_bundle_routes_are_declared_before_the_catch_all():
    paths = [r.path for r in main.app.routes if "GET" in (getattr(r, "methods", None) or ())]
    catch_all = paths.index("/v1/satellite-code/{path:path}")
    assert paths.index("/v1/satellite-code/bundle.tar.gz") < catch_all
    assert paths.index("/v1/satellite-code/bundle.sha256") < catch_all


async def test_a_frozen_channel_serves_no_bundle(monkeypatch):
    from fastapi import HTTPException

    async def frozen(*a, **k):
        raise HTTPException(status_code=423, detail="frozen")
    monkeypatch.setattr(main.container_freeze, "require_unfrozen", frozen)
    for route in (main.satellite_code_bundle, main.satellite_code_bundle_digest):
        with pytest.raises(HTTPException) as e:
            await route()
        assert e.value.status_code == 423
