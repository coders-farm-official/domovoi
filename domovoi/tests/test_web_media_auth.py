"""Who may read and who may change the media surfaces (WEB-2, REV-1,
WEB-1) — the two-tier posture taken on 2026-09-22.

* **Daily (device) tier** — a household client presenting
  ``X-Device-Token``, an admin Bearer, or (for reads the browser fetches
  by URL) the dashboard cookie / ``?device_token=``: browsing, streaming
  and downloading single files across Files, Images, Videos and
  Documents, and the ordinary Files writes.
* **Admin tier** — ``Authorization: Bearer``: everything that changes the
  operator's Documents folder, deletes, hands back a whole directory as
  a zip, or touches satellite media preparation.

DB-FREE by construction: the auth primitives are faked
(``auth_testkit.install_fake_db``) so the REAL routers, wearing their
REAL dependencies, are exercised without Postgres — this file can never
hide behind ``requires_db`` the way a skipped test does. The handlers'
own database use (the files device-block lookup) is stubbed per test.

Deliberately phrased as behaviour: what a caller gets, never how the
gate is implemented.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import web.backend.api.files as files_api
import web.backend.api.images as images_api
import web.backend.api.videos as videos_api
from domovoi.config import settings
from domovoi.tests.auth_testkit import HEADER, bearer, install_fake_db
from web.backend.api.files_security import MediaLibrary
from web.backend.main import app

ADMIN_TOKEN = "admin-token"
DEVICE_TOKEN = "device-token"
XRW = {"X-Requested-With": "XMLHttpRequest"}

DEVICE = {HEADER: DEVICE_TOKEN}
ADMIN = bearer(ADMIN_TOKEN)
COOKIE_ONLY = {"Cookie": f"domovoi_admin={ADMIN_TOKEN}"}


@pytest.fixture
def claimed(monkeypatch):
    """An install that HAS an admin password (so the pre-setup grace no
    longer waves everything through), one live admin session and one
    household device token."""
    return install_fake_db(
        monkeypatch,
        admin=True,
        sessions={ADMIN_TOKEN},
        device_token=DEVICE_TOKEN,
    )


@pytest.fixture
def unclaimed(monkeypatch):
    """A fresh install, before first-run setup."""
    return install_fake_db(monkeypatch, admin=False, device_token=DEVICE_TOKEN)


@pytest.fixture
def docs_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    (tmp_path / "notes.md").write_text("# hello\n", encoding="utf-8")
    return tmp_path


def _client() -> TestClient:
    """No lifespan — nothing here needs the poll loops, and entering them
    would open a database connection this module is proving it can do
    without."""
    return TestClient(app)


# ═══ WEB-2 · Documents ════════════════════════════════════════════════


DOC_WRITES = [
    ("post", "/api/documents/create", {"json": {"name": "x", "kind": "text"}}),
    ("post", "/api/documents/delete", {"json": {"rel_paths": ["notes.md"]}}),
    ("post", "/api/documents/download-zip", {"json": {"rel_paths": ["notes.md"]}}),
    ("put", "/api/documents/text/notes.md", {"json": {"text": "owned"}}),
    ("put", "/api/documents/sheet/t.csv", {"json": {"rows": []}}),
    ("post", "/api/documents/drawings/write",
     {"json": {"rel_path": "d.svg", "content": "<svg/>"}}),
]
DOC_WRITE_IDS = [f"{m.upper()} {p}" for m, p, _ in DOC_WRITES]


@pytest.mark.parametrize(("method", "path", "kw"), DOC_WRITES, ids=DOC_WRITE_IDS)
def test_changing_documents_needs_an_admin_session(claimed, docs_dir, method, path, kw):
    """Nothing in ``~/Documents`` changes — and no archive of it comes
    back — for a caller with no credential, and a household device token
    is not enough either: this is the operator's own folder."""
    c = _client()
    assert getattr(c, method)(path, **kw).status_code == 401
    assert getattr(c, method)(path, headers=DEVICE, **kw).status_code == 401
    assert (docs_dir / "notes.md").read_text(encoding="utf-8") == "# hello\n"


def test_uploading_a_document_needs_an_admin_session_and_a_preflight(claimed, docs_dir):
    """The multipart upload is the one route here a cross-site form could
    otherwise submit, so it wants the preflight-forcing header as well as
    the admin session."""
    files = [("files", ("dropped.txt", b"x", "text/plain"))]
    c = _client()
    assert c.post("/api/documents/upload", files=files).status_code == 401
    assert c.post(
        "/api/documents/upload", files=files, headers={**DEVICE, **XRW}
    ).status_code == 401
    # Admin, but sent the way a cross-site form would — no preflight header.
    assert c.post(
        "/api/documents/upload", files=files, headers=ADMIN
    ).status_code == 403
    assert not (docs_dir / "dropped.txt").exists()
    ok = c.post("/api/documents/upload", files=files, headers={**ADMIN, **XRW})
    assert ok.status_code == 200, ok.text
    assert (docs_dir / "dropped.txt").read_bytes() == b"x"


def test_an_admin_session_still_changes_documents(claimed, docs_dir):
    c = _client()
    r = c.put("/api/documents/text/notes.md", json={"text": "owned"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert (docs_dir / "notes.md").read_text(encoding="utf-8") == "owned"


def test_the_dashboard_cookie_alone_never_changes_documents(claimed, docs_dir):
    """A cookie renders state; it does not authorize a write (that is the
    whole point of the split — a cross-site POST carries the cookie)."""
    r = _client().put(
        "/api/documents/text/notes.md", json={"text": "owned"}, headers=COOKIE_ONLY
    )
    assert r.status_code == 403
    assert (docs_dir / "notes.md").read_text(encoding="utf-8") == "# hello\n"


DOC_READS = [
    "/api/documents",
    "/api/documents/text/notes.md",
    "/api/documents/raw/notes.md",
    "/api/documents/export/doc/notes.md",
]


@pytest.mark.parametrize("path", DOC_READS)
def test_reading_documents_needs_a_device_token_or_a_session(claimed, docs_dir, path):
    c = _client()
    assert c.get(path).status_code == 401
    assert c.get(path, headers={HEADER: "not-the-household-token"}).status_code == 401


@pytest.mark.parametrize("path", DOC_READS)
def test_a_paired_device_an_admin_or_the_cookie_reads_documents(
    claimed, docs_dir, path
):
    c = _client()
    assert c.get(path, headers=DEVICE).status_code == 200
    assert c.get(path, headers=ADMIN).status_code == 200
    assert c.get(path, headers=COOKIE_ONLY).status_code == 200


def test_a_browser_may_carry_the_household_token_in_the_query_for_reads(
    claimed, docs_dir
):
    """``window.open`` and ``<img src>`` cannot set a header, so the daily
    READ tier also takes the token as a query parameter."""
    c = _client()
    assert c.get(f"/api/documents/raw/notes.md?device_token={DEVICE_TOKEN}").status_code == 200
    assert c.get("/api/documents/raw/notes.md?device_token=nope").status_code == 401


def test_a_query_token_never_authorizes_a_write(claimed, docs_dir):
    r = _client().put(
        f"/api/documents/text/notes.md?device_token={DEVICE_TOKEN}",
        json={"text": "owned"},
    )
    assert r.status_code == 401
    assert (docs_dir / "notes.md").read_text(encoding="utf-8") == "# hello\n"


def test_a_fresh_install_can_still_use_documents_before_setup(unclaimed, docs_dir):
    """The pre-setup grace: an install with no admin password yet keeps
    the whole surface usable, so first-run onboarding works."""
    c = _client()
    assert c.get("/api/documents").status_code == 200
    r = c.put("/api/documents/text/notes.md", json={"text": "owned"})
    assert r.status_code == 200, r.text


# ═══ REV-1 · Files, Images, Videos ════════════════════════════════════


@pytest.fixture
def libraries(monkeypatch, tmp_path):
    """A music library (ordinary), the Documents library (admin writes)
    and a removable drive, each rooted in a tmp dir. The device-block
    lookup is stubbed to "not blocked" — whose writes are blocked is
    test_files_api.py's subject, not this module's."""
    roots = {}
    for name in ("music", "documents", "usb"):
        root = tmp_path / name
        root.mkdir()
        roots[name] = root
    (roots["music"] / "song.mp3").write_bytes(b"aa")
    (roots["music"] / "Beatles").mkdir()
    (roots["music"] / "clip.mp4").write_bytes(b"vv")
    (roots["music"] / "cat.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (roots["usb"] / "x.mp3").write_bytes(b"x")

    def _lib(lib_id, label, root, **kw):
        base = dict(
            id=lib_id, label=label, kind="core", icon="music", kind_icon="folder",
            owner=None, root_path=root, editable=True, importable=True,
            doc_editing=False, reindex_kind=None, present=True,
        )
        base.update(kw)
        return MediaLibrary(**base)

    libs = [
        _lib("core:music", "Music", roots["music"]),
        _lib("core:documents", "Documents", roots["documents"], doc_editing=True),
        _lib("removable:E", "USB (E:)", roots["usb"], kind="removable",
             editable=False, importable=False),
    ]

    async def _build():
        return list(libs)

    async def _not_blocked(_device_id):
        return None

    async def _no_reindex(_kind, _request):
        return False

    for module in (files_api, images_api, videos_api):
        monkeypatch.setattr(module, "build_libraries", _build)
    monkeypatch.setattr(files_api, "_block_status", _not_blocked)
    monkeypatch.setattr(files_api, "_trigger_reindex", _no_reindex)
    return roots


FILE_READS = [
    ("/api/files/libraries", {}),
    ("/api/files/browse", {"library_id": "core:music", "path": ""}),
    ("/api/files/download", {"library_id": "core:music", "path": "song.mp3"}),
    ("/api/images/raw", {"library_id": "core:music", "path": "cat.png"}),
    ("/api/videos/list", {}),
    ("/api/videos/stream", {"library_id": "core:music", "path": "clip.mp4"}),
]
FILE_READ_IDS = [p for p, _ in FILE_READS]


@pytest.mark.parametrize(("path", "params"), FILE_READS, ids=FILE_READ_IDS)
def test_browsing_and_downloading_needs_a_device_token_or_a_session(
    claimed, libraries, path, params
):
    c = _client()
    assert c.get(path, params=params).status_code == 401
    assert c.get(path, params=params, headers={HEADER: "stale"}).status_code == 401


@pytest.mark.parametrize(("path", "params"), FILE_READS, ids=FILE_READ_IDS)
def test_a_paired_device_browses_and_downloads(claimed, libraries, path, params):
    c = _client()
    assert c.get(path, params=params, headers=DEVICE).status_code in (200, 206)
    assert c.get(path, params=params, headers=ADMIN).status_code in (200, 206)
    # And by URL, for the <img>/<video>/window.open shapes.
    assert c.get(
        path, params={**params, "device_token": DEVICE_TOKEN}
    ).status_code in (200, 206)


def test_uploading_moving_and_importing_need_a_device_token(claimed, libraries):
    c = _client()
    up = c.post(
        "/api/files/upload",
        data={"library_id": "core:music", "path": "", "device_id": "browser-test"},
        files=[("files", ("u.mp3", b"u", "audio/mpeg"))],
        headers=XRW,
    )
    assert up.status_code == 401
    mv = c.post("/api/files/move", json={
        "source_library_id": "core:music", "paths": ["song.mp3"],
        "target_library_id": "core:music", "target_path": "Beatles",
        "device_id": "browser-test",
    })
    assert mv.status_code == 401
    im = c.post("/api/files/import", json={
        "source_library_id": "removable:E", "source_path": "x.mp3",
        "target_library_id": "core:music", "target_path": "",
        "device_id": "browser-test",
    })
    assert im.status_code == 401
    assert (libraries["music"] / "song.mp3").exists()
    assert not (libraries["music"] / "u.mp3").exists()


def test_a_paired_device_uploads_moves_and_imports(claimed, libraries):
    c = _client()
    up = c.post(
        "/api/files/upload",
        data={"library_id": "core:music", "path": "", "device_id": "browser-test"},
        files=[("files", ("u.mp3", b"u", "audio/mpeg"))],
        headers={**DEVICE, **XRW},
    )
    assert up.status_code == 200, up.text
    mv = c.post("/api/files/move", json={
        "source_library_id": "core:music", "paths": ["song.mp3"],
        "target_library_id": "core:music", "target_path": "Beatles",
        "device_id": "browser-test",
    }, headers=DEVICE)
    assert mv.status_code == 200, mv.text
    im = c.post("/api/files/import", json={
        "source_library_id": "removable:E", "source_path": "x.mp3",
        "target_library_id": "core:music", "target_path": "",
        "device_id": "browser-test",
    }, headers=DEVICE)
    assert im.status_code == 200, im.text
    assert (libraries["music"] / "Beatles" / "song.mp3").exists()


def test_an_upload_without_the_preflight_header_is_refused(claimed, libraries):
    """Multipart is a CORS simple request; the header is what makes the
    browser ask first."""
    r = _client().post(
        "/api/files/upload",
        data={"library_id": "core:music", "path": "", "device_id": "browser-test"},
        files=[("files", ("u.mp3", b"u", "audio/mpeg"))],
        headers=DEVICE,
    )
    assert r.status_code == 403
    assert not (libraries["music"] / "u.mp3").exists()


def test_deleting_needs_an_admin_session(claimed, libraries):
    c = _client()
    body = {"library_id": "core:music", "paths": ["song.mp3"]}
    assert c.post("/api/files/delete", json=body).status_code == 401
    assert c.post("/api/files/delete", json=body, headers=DEVICE).status_code == 401
    assert (libraries["music"] / "song.mp3").exists()
    assert c.post("/api/files/delete", json=body, headers=ADMIN).status_code == 200
    assert not (libraries["music"] / "song.mp3").exists()


def test_downloading_a_whole_directory_needs_an_admin_session(claimed, libraries):
    """A single file is a daily action. A directory is a server-built zip
    of a whole tree, which is not."""
    c = _client()
    params = {"library_id": "core:music", "path": "Beatles"}
    (libraries["music"] / "Beatles" / "b.mp3").write_bytes(b"b")
    assert c.get("/api/files/download", params=params, headers=DEVICE).status_code == 401
    ok = c.get("/api/files/download", params=params, headers=ADMIN)
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "application/zip"


def test_writing_into_documents_through_files_needs_an_admin_session(claimed, libraries):
    """The Documents library is the operator's own folder wherever it is
    reached from — the Files page included."""
    c = _client()
    up = c.post(
        "/api/files/upload",
        data={"library_id": "core:documents", "path": "", "device_id": "browser-test"},
        files=[("files", ("n.txt", b"hi", "text/plain"))],
        headers={**DEVICE, **XRW},
    )
    assert up.status_code == 401
    assert not (libraries["documents"] / "n.txt").exists()
    mv = c.post("/api/files/move", json={
        "source_library_id": "core:music", "paths": ["song.mp3"],
        "target_library_id": "core:documents", "target_path": "",
        "device_id": "browser-test",
    }, headers=DEVICE)
    assert mv.status_code == 401
    ok = c.post(
        "/api/files/upload",
        data={"library_id": "core:documents", "path": "", "device_id": "browser-test"},
        files=[("files", ("n.txt", b"hi", "text/plain"))],
        headers={**ADMIN, **XRW},
    )
    assert ok.status_code == 200, ok.text


def test_a_paired_device_still_reads_documents_through_files(claimed, libraries):
    (libraries["documents"] / "n.txt").write_bytes(b"hi")
    c = _client()
    assert c.get(
        "/api/files/browse",
        params={"library_id": "core:documents", "path": ""},
        headers=DEVICE,
    ).status_code == 200
    assert c.get(
        "/api/files/download",
        params={"library_id": "core:documents", "path": "n.txt"},
        headers=DEVICE,
    ).status_code == 200


def test_saving_a_video_position_needs_a_device_token_not_just_a_cookie(claimed, libraries):
    body = {
        "library_id": "core:music", "path": "clip.mp4",
        "device_id": "browser-test", "position_sec": 12,
    }
    c = _client()
    assert c.post("/api/videos/position", json=body).status_code == 401
    # A cookie renders the page; it does not write a row.
    assert c.post("/api/videos/position", json=body, headers=COOKIE_ONLY).status_code == 403


def test_a_fresh_install_can_still_use_the_files_surface(unclaimed, libraries):
    c = _client()
    assert c.get("/api/files/libraries").status_code == 200
    up = c.post(
        "/api/files/upload",
        data={"library_id": "core:music", "path": "", "device_id": "browser-test"},
        files=[("files", ("u.mp3", b"u", "audio/mpeg"))],
        headers=XRW,
    )
    assert up.status_code == 200, up.text
