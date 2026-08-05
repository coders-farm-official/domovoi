"""Endpoint tests for the library-image serving API — web/backend/api/images.py.

The generation surface moved to the Image Generation plugin; core keeps
the two generic endpoints (thumb + raw) the Files tab and the plugin's
history cards use. Registry stubbed to tmp-dir libraries; thumbnails
exercise real Pillow.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import web.backend.api.images as images_api
from domovoi.tests.conftest import requires_db
from web.backend.api.files_security import MediaLibrary
from web.backend.main import app


@pytest.fixture
def roots(tmp_path):
    pics = tmp_path / "pictures"
    pics.mkdir()
    return {"pictures": pics}


@pytest.fixture
def registry(roots, monkeypatch):
    libs = {
        "core:pictures": MediaLibrary(
            id="core:pictures", label="Pictures", kind="core", icon="image",
            kind_icon="folder", owner=None, root_path=roots["pictures"],
            editable=True, importable=True, doc_editing=False,
            reindex_kind=None, present=True,
        ),
    }

    async def _build():
        return list(libs.values())

    monkeypatch.setattr(images_api, "build_libraries", _build)
    return libs


@pytest.fixture
def thumbs_dir(tmp_path, monkeypatch):
    d = tmp_path / "thumbs"
    monkeypatch.setattr(images_api.core_settings, "image_thumbs_dir", str(d))
    return d


def _client():
    return TestClient(app)


def _png(root: Path, rel: str) -> Path:
    from PIL import Image

    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), (200, 120, 40)).save(p)
    return p


@requires_db
def test_raw_serves_inline_and_rejects(registry, roots, thumbs_dir):
    _png(roots["pictures"], "cat.png")
    (roots["pictures"] / "notes.txt").write_text("hi", encoding="utf-8")
    with _client() as c:
        ok = c.get("/api/images/raw",
                   params={"library_id": "core:pictures", "path": "cat.png"})
        not_image = c.get("/api/images/raw",
                          params={"library_id": "core:pictures", "path": "notes.txt"})
        escape = c.get("/api/images/raw",
                       params={"library_id": "core:pictures", "path": "../x.png"})
        unknown = c.get("/api/images/raw",
                        params={"library_id": "nope", "path": "a.png"})
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/png"
    assert "attachment" not in (ok.headers.get("content-disposition") or "")
    assert not_image.status_code == 400
    assert escape.status_code == 400
    assert unknown.status_code == 404


@requires_db
def test_thumb_caches_and_sentinels(registry, roots, thumbs_dir):
    _png(roots["pictures"], "cat.png")
    (roots["pictures"] / "broken.png").write_bytes(b"not a png")
    with _client() as c:
        t1 = c.get("/api/images/thumb",
                   params={"library_id": "core:pictures", "path": "cat.png", "size": "s"})
        t2 = c.get("/api/images/thumb",
                   params={"library_id": "core:pictures", "path": "cat.png", "size": "s"})
        bad = c.get("/api/images/thumb",
                    params={"library_id": "core:pictures", "path": "broken.png", "size": "m"})
        bad2 = c.get("/api/images/thumb",
                     params={"library_id": "core:pictures", "path": "broken.png", "size": "m"})
        badsize = c.get("/api/images/thumb",
                        params={"library_id": "core:pictures", "path": "cat.png", "size": "xxl"})
    assert t1.status_code == 200 and t1.headers["content-type"] == "image/webp"
    assert t2.status_code == 200
    assert bad.status_code == 204 and bad2.status_code == 204
    assert badsize.status_code == 400
    assert list((thumbs_dir / "s").glob("*.webp"))
