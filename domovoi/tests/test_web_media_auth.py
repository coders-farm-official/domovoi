"""Who may read, who may save and who may delete on the media surfaces
(WEB-2, WEB-1) — the posture taken 2026-09-22 and revised 2026-09-24:
**saving is a household action, deleting is an admin action.**

* **Daily read tier** — a household client presenting ``X-Device-Token``,
  an admin Bearer, or (for the reads a browser fetches by URL) the
  dashboard cookie / ``?device_token=``: browsing, streaming and
  downloading single files across Files, Images, Videos and Documents.
* **Device tier** — ``X-Device-Token`` or an admin Bearer, and NEITHER
  the cookie alone nor ``?device_token=``: the ordinary Files writes, and
  every SAVE in the Documents folder — create, upload, write a text file,
  write a sheet, save a drawing.
* **Admin tier** — ``Authorization: Bearer``: deletes, the requests that
  hand back a whole tree in one response (``/api/documents/download-zip``
  and a directory ``/api/files/download``), writes onto a removable
  drive, and satellite media preparation.

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


def _no_preflight_client() -> TestClient:
    """A caller that did not send X-Requested-With — the shape of a
    cross-origin form post."""
    return TestClient(app)


def _client() -> TestClient:
    """No lifespan — nothing here needs the poll loops, and entering them
    would open a database connection this module is proving it can do
    without."""
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


# ═══ WEB-2 · Documents ════════════════════════════════════════════════


# ── Saving: the household verb ──
#
# Create, write text, write a sheet, save a drawing. Upload is the same
# tier and is exercised separately because it is multipart.
DOC_SAVES = [
    ("post", "/api/documents/create", {"json": {"name": "shopping.txt", "kind": "text"}}),
    ("put", "/api/documents/text/notes.md", {"json": {"text": "milk, oats"}}),
    ("put", "/api/documents/sheet/budget.csv", {"json": {"rows": []}}),
    ("post", "/api/documents/drawings/write",
     {"json": {"rel_path": "plan.svg", "content": "<svg/>"}}),
]
DOC_SAVE_IDS = [f"{m.upper()} {p}" for m, p, _ in DOC_SAVES]

# Delete, and the request that hands back the whole selection as one zip.
DOC_ADMIN_WRITES = [
    ("post", "/api/documents/delete", {"json": {"rel_paths": ["notes.md"]}}),
    ("post", "/api/documents/download-zip", {"json": {"rel_paths": ["notes.md"]}}),
]
DOC_ADMIN_WRITE_IDS = [f"{m.upper()} {p}" for m, p, _ in DOC_ADMIN_WRITES]


@pytest.mark.parametrize(("method", "path", "kw"), DOC_SAVES, ids=DOC_SAVE_IDS)
def test_saving_a_document_needs_a_device_token_or_a_session(
    claimed, docs_dir, method, path, kw
):
    """Nothing in the Documents folder changes for a caller with no
    credential, or one presenting a token this household never minted."""
    c = _client()
    assert getattr(c, method)(path, **kw).status_code == 401
    assert getattr(c, method)(path, headers={HEADER: "stale"}, **kw).status_code == 401
    assert (docs_dir / "notes.md").read_text(encoding="utf-8") == "# hello\n"
    assert sorted(p.name for p in docs_dir.iterdir()) == ["notes.md"]


def test_a_paired_device_saves_a_document(claimed, docs_dir):
    """The household verb: a phone holding ``X-Device-Token`` writes the
    shopping list, and the bytes are on disk afterwards. No admin
    password anywhere in this test."""
    c = _client()
    r = c.put("/api/documents/text/notes.md", json={"text": "milk, oats"}, headers=DEVICE)
    assert r.status_code == 200, r.text
    assert (docs_dir / "notes.md").read_text(encoding="utf-8") == "milk, oats"


def test_a_paired_device_creates_a_document_and_saves_a_drawing(claimed, docs_dir):
    c = _client()
    made = c.post(
        "/api/documents/create", json={"name": "shopping.txt", "kind": "text"},
        headers=DEVICE,
    )
    assert made.status_code == 200, made.text
    assert (docs_dir / "shopping.txt").exists()
    drew = c.post(
        "/api/documents/drawings/write",
        json={"rel_path": "plan.svg", "content": "<svg/>"}, headers=DEVICE,
    )
    assert drew.status_code == 200, drew.text
    assert (docs_dir / "plan.svg").read_text(encoding="utf-8") == "<svg/>"


def test_uploading_a_document_needs_a_device_token_and_a_preflight(claimed, docs_dir):
    """Upload is the household verb too — but it is multipart, the one
    shape here a cross-site form could otherwise submit, so it wants the
    preflight-forcing header as well as the household token."""
    files = [("files", ("dropped.txt", b"x", "text/plain"))]
    c = _client()
    assert c.post("/api/documents/upload", files=files).status_code == 401
    # Paired, but sent the way a cross-site form would — no preflight header.
    assert _no_preflight_client().post(
        "/api/documents/upload", files=files, headers=DEVICE
    ).status_code == 403
    assert not (docs_dir / "dropped.txt").exists()
    ok = c.post("/api/documents/upload", files=files, headers={**DEVICE, **XRW})
    assert ok.status_code == 200, ok.text
    assert (docs_dir / "dropped.txt").read_bytes() == b"x"


def test_an_admin_session_still_changes_documents(claimed, docs_dir):
    c = _client()
    r = c.put("/api/documents/text/notes.md", json={"text": "owned"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert (docs_dir / "notes.md").read_text(encoding="utf-8") == "owned"


@pytest.mark.parametrize(("method", "path", "kw"), DOC_SAVES + DOC_ADMIN_WRITES,
                         ids=DOC_SAVE_IDS + DOC_ADMIN_WRITE_IDS)
def test_the_dashboard_cookie_alone_never_changes_documents(
    claimed, docs_dir, method, path, kw
):
    """A cookie renders state; it does not authorize a write (that is the
    whole point of the split — a cross-site POST carries the cookie).
    Widening SAVE to the device tier did not widen it to the cookie: the
    refusal is 403 on the saves and on the admin verbs alike."""
    r = getattr(_client(), method)(path, headers=COOKIE_ONLY, **kw)
    assert r.status_code == 403, r.text
    assert (docs_dir / "notes.md").read_text(encoding="utf-8") == "# hello\n"
    assert sorted(p.name for p in docs_dir.iterdir()) == ["notes.md"]


# ── Deleting, and zipping the folder: the admin verbs ──


@pytest.mark.parametrize(("method", "path", "kw"), DOC_ADMIN_WRITES,
                         ids=DOC_ADMIN_WRITE_IDS)
def test_deleting_or_zipping_documents_needs_an_admin_session(
    claimed, docs_dir, method, path, kw
):
    """A household device saves; it does not destroy, and it does not
    walk away with the whole selection in one response. The file is still
    on disk after every refusal."""
    c = _client()
    assert getattr(c, method)(path, **kw).status_code == 401
    assert getattr(c, method)(path, headers=DEVICE, **kw).status_code == 401
    assert (docs_dir / "notes.md").read_text(encoding="utf-8") == "# hello\n"


def test_an_admin_deletes_a_document(claimed, docs_dir):
    r = _client().post(
        "/api/documents/delete", json={"rel_paths": ["notes.md"]}, headers=ADMIN
    )
    assert r.status_code == 200, r.text
    assert not (docs_dir / "notes.md").exists()


def test_an_admin_takes_the_zip(claimed, docs_dir):
    r = _client().post(
        "/api/documents/download-zip", json={"rel_paths": ["notes.md"]}, headers=ADMIN
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"


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


# ═══ Files, Images, Videos ════════════════════════════════════════════


@pytest.fixture
def libraries(monkeypatch, tmp_path):
    """A music library (ordinary), the Documents library (saves are the
    household's, like any other core library) and a removable drive
    (writes stay admin), each rooted in a tmp dir. The device-block
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
    r = _no_preflight_client().post(
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


def test_a_paired_device_saves_into_documents_through_files(claimed, libraries):
    """The Documents library is the household's to save into wherever it
    is reached from — the Files page included, not only the editors."""
    c = _client()
    up = c.post(
        "/api/files/upload",
        data={"library_id": "core:documents", "path": "", "device_id": "browser-test"},
        files=[("files", ("n.txt", b"hi", "text/plain"))],
        headers={**DEVICE, **XRW},
    )
    assert up.status_code == 200, up.text
    assert (libraries["documents"] / "n.txt").read_bytes() == b"hi"
    mv = c.post("/api/files/move", json={
        "source_library_id": "core:music", "paths": ["song.mp3"],
        "target_library_id": "core:documents", "target_path": "",
        "device_id": "browser-test",
    }, headers=DEVICE)
    assert mv.status_code == 200, mv.text
    assert (libraries["documents"] / "song.mp3").exists()


def test_deleting_from_documents_through_files_still_needs_an_admin_session(
    claimed, libraries
):
    """Saving got easier; deleting did not. The file survives the
    household's attempt and only goes when an admin asks."""
    (libraries["documents"] / "n.txt").write_bytes(b"hi")
    c = _client()
    body = {"library_id": "core:documents", "paths": ["n.txt"]}
    assert c.post("/api/files/delete", json=body).status_code == 401
    assert c.post("/api/files/delete", json=body, headers=DEVICE).status_code == 401
    assert (libraries["documents"] / "n.txt").exists()
    assert c.post("/api/files/delete", json=body, headers=ADMIN).status_code == 200
    assert not (libraries["documents"] / "n.txt").exists()


def test_a_paired_device_still_cannot_write_onto_a_removable_drive(claimed, libraries):
    """A stick somebody plugged into the server is not one of the
    household's libraries: writing onto it was never what was widened."""
    c = _client()
    up = c.post(
        "/api/files/upload",
        data={"library_id": "removable:E", "path": "", "device_id": "browser-test"},
        files=[("files", ("n.txt", b"hi", "text/plain"))],
        headers={**DEVICE, **XRW},
    )
    assert up.status_code == 401, up.text
    mv = c.post("/api/files/move", json={
        "source_library_id": "core:music", "paths": ["song.mp3"],
        "target_library_id": "removable:E", "target_path": "",
        "device_id": "browser-test",
    }, headers=DEVICE)
    assert mv.status_code == 401, mv.text
    assert not (libraries["usb"] / "n.txt").exists()
    assert (libraries["music"] / "song.mp3").exists()


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


# ═══ WEB-1 · Satellite media preparation ══════════════════════════════


SATELLITE_MEDIA_READS = [
    "/api/satellites/media/status",
    "/api/satellites/media/targets",
    "/api/satellites/media/jobs",
    "/api/satellites/media/jobs/1/download",
    "/api/satellites/media/jobs/1/credentials",
]


@pytest.mark.parametrize("path", SATELLITE_MEDIA_READS)
def test_reading_satellite_media_needs_an_admin_session(claimed, path):
    """The artifact a build produces is the code a Pi will run, /jobs
    lists the small integer ids that name it, and /targets enumerates the
    drives plugged into this server. None of that is a daily read."""
    c = _client()
    assert c.get(path).status_code == 401
    assert c.get(path, headers=DEVICE).status_code == 401


# The subset whose handlers touch neither the database nor the plugin
# registry, so the far side of the gate can be checked without either.
SATELLITE_MEDIA_DB_FREE_READS = [
    "/api/satellites/media/targets",
    "/api/satellites/media/jobs/1/credentials",
]


@pytest.mark.parametrize("path", SATELLITE_MEDIA_DB_FREE_READS)
def test_an_admin_reads_satellite_media(claimed, path):
    """A Bearer or the dashboard cookie renders these — they are reads.
    (404 is the honest answer for job 1 on an empty install; what matters
    is that the gate let the request through.)"""
    c = _client()
    assert c.get(path, headers=ADMIN).status_code in (200, 404)
    assert c.get(path, headers=COOKIE_ONLY).status_code in (200, 404)


def test_preparing_media_still_needs_an_admin_bearer(claimed):
    body = {"board": "pi02w", "mic_profile": "none", "target": {"kind": "zip"}}
    c = _client()
    assert c.post("/api/satellites/media/prepare", json=body).status_code == 401
    assert c.post(
        "/api/satellites/media/prepare", json=body, headers=DEVICE
    ).status_code == 401
    # A cookie renders a page; it does not start a build.
    assert c.post(
        "/api/satellites/media/prepare", json=body, headers=COOKIE_ONLY
    ).status_code == 403


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
