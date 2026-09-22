"""A stored file served to the browser must not become a page of the
dashboard (WEB-3).

``/api/documents/raw`` and ``/api/images/raw`` hand back whatever is in
the folder. A PDF or a JPEG is inert; an HTML, SVG or XHTML file is a
document the browser EXECUTES, and executing it at
``http://<server>:6369`` would run it on the dashboard's own origin. So
those come back as a download, declared honestly and told not to render.

DB-free: the auth primitives are faked (the raw serves are device tier
since WEB-2/REV-1) and the image registry is stubbed to a tmp dir.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import web.backend.api.images as images_api
from domovoi.config import settings
from domovoi.tests.auth_testkit import install_fake_db
from web.backend.api.files_security import MediaLibrary
from web.backend.api.inline_serve import disposition_for, inert_headers, is_active_document
from web.backend.main import app


@pytest.fixture(autouse=True)
def _pre_setup_install(monkeypatch):
    install_fake_db(monkeypatch, admin=False)


@pytest.fixture
def docs_dir(monkeypatch, tmp_path):
    d = tmp_path / "documents"
    d.mkdir()
    monkeypatch.setattr(settings, "documents_dir", str(d))
    return d


@pytest.fixture
def pictures(monkeypatch, tmp_path):
    root = tmp_path / "pictures"
    root.mkdir()
    lib = MediaLibrary(
        id="core:pictures", label="Pictures", kind="core", icon="image",
        kind_icon="folder", owner=None, root_path=root, editable=True,
        importable=True, doc_editing=False, reindex_kind=None, present=True,
    )

    async def _build():
        return [lib]

    monkeypatch.setattr(images_api, "build_libraries", _build)
    return root


def _client() -> TestClient:
    return TestClient(app)


# ─── The classifier ────────────────────────────────────────────────────

ACTIVE = [
    ("text/html", "page.html"),
    ("application/xhtml+xml", "page.xhtml"),
    ("image/svg+xml", "drawing.svg"),
    # A thin mimetypes registry (Windows) can hand back octet-stream for a
    # file a browser will still happily execute — the name decides too.
    ("application/octet-stream", "page.html"),
    ("text/html; charset=utf-8", "page.html"),
]
INERT = [
    ("application/pdf", "manual.pdf"),
    ("image/png", "cat.png"),
    ("text/plain", "notes.txt"),
    ("text/markdown", "notes.md"),
    ("audio/mpeg", "song.mp3"),
]


@pytest.mark.parametrize(("media_type", "name"), ACTIVE)
def test_a_document_the_browser_would_execute_is_downloaded_not_rendered(media_type, name):
    assert is_active_document(media_type, name) is True
    assert disposition_for(media_type, name) == "attachment"
    headers = inert_headers(media_type, name)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Content-Security-Policy"] == "sandbox"


@pytest.mark.parametrize(("media_type", "name"), INERT)
def test_everything_else_still_opens_in_the_tab(media_type, name):
    assert is_active_document(media_type, name) is False
    assert disposition_for(media_type, name) == "inline"
    headers = inert_headers(media_type, name)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" not in headers


# ─── Over HTTP ─────────────────────────────────────────────────────────


def test_a_document_html_file_comes_back_as_a_download(docs_dir):
    (docs_dir / "x.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    r = _client().get("/api/documents/raw/x.html")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox"


def test_a_document_pdf_still_opens_in_the_tab(docs_dir):
    (docs_dir / "manual.pdf").write_bytes(b"%PDF-1.4\n")
    r = _client().get("/api/documents/raw/manual.pdf")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["x-content-type-options"] == "nosniff"
    # It carries the SITE policy every response gets (WEB-8), not the
    # sandbox that keeps a stored page from running — a PDF still opens.
    assert "sandbox" not in r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_a_library_svg_comes_back_as_a_download(pictures):
    (pictures / "x.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', encoding="utf-8"
    )
    r = _client().get(
        "/api/images/raw", params={"library_id": "core:pictures", "path": "x.svg"}
    )
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox"


def test_a_library_png_still_renders(pictures):
    (pictures / "cat.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    r = _client().get(
        "/api/images/raw", params={"library_id": "core:pictures", "path": "cat.png"}
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert "attachment" not in (r.headers.get("content-disposition") or "")
    assert r.headers["x-content-type-options"] == "nosniff"
