"""Documents and Other: two views of the one shared Documents folder
(web/backend/api/doc_types.py, Kamron 2026-10-27).

* Documents lists document types (and folders); Other lists everything
  else (and folders). Neither lists the other's files, nothing moves on
  disk, and nothing in the folder disappears from both.
* A new file written through a view must fit it, through every door:
  Files upload, folder upload (upload-stream / upload-check), move,
  import (copy), and the Documents API. The refusal names the right place.
* Other is the Documents library under another public id: the same root,
  the same write policy (device block, admin-write hook, editable), the
  same secret-name and containment filters, and the same server-side id,
  so the rules keyed on Documents (the two-doors policy, the listing
  cache, co-editing follow-moves) apply to it unchanged.
* Other only downloads: no image serve, no printing, no doc editing.

DB-free: the auth primitives are faked and the files device-block is
stubbed, like test_documents_write_guard.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import web.backend.api.files as files_api
import web.backend.api.files_security as files_security
from domovoi.config import settings
from domovoi.tests.auth_testkit import HEADER, bearer, install_fake_db
from web.backend.api.doc_types import (
    DOCUMENT_EXTS,
    DOCUMENT_IN_OTHER_REFUSAL,
    OTHER_REFUSAL,
    documents_refusal,
    in_view,
)
from web.backend.api.files_security import DOCUMENTS_LIBRARY_ID, OTHER_LIBRARY_ID, MediaLibrary
from web.backend.main import app

DEVICE_TOKEN = "device-token"
ADMIN_TOKEN = "admin-session"
XRW = {"X-Requested-With": "domovoi-tests"}
DEV = "browser-test"

DOCS_ONLY = {"notes.md", "letter.html", "tax.pdf", "budget.xlsx", "plan.excalidraw", "old.doc", "list.txt"}
OTHER_ONLY = {"setup.exe", "app.apk", "backup.zip", "wedding.jpg", "song.mp3", "clip.mp4", "todo"}


@pytest.fixture
def fake_auth(monkeypatch):
    return install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)


@pytest.fixture(autouse=True)
def _nobody_is_blocked(monkeypatch):
    async def _status(device_id):
        return "Kids iPad isn't allowed to change files" if device_id == "android-kid" else None

    monkeypatch.setattr(files_api, "_block_status", _status)


@pytest.fixture
def folder(monkeypatch, tmp_path):
    """A shared Documents folder as a household has it: documents, and
    the things that were dropped in beside them over the years."""
    root = tmp_path / "Documents"
    root.mkdir()
    monkeypatch.setattr(settings, "documents_dir", str(root))
    for name in DOCS_ONLY | OTHER_ONLY:
        (root / name).write_bytes(b"x")
    (root / ".env").write_text("SECRET=1")          # withheld by name in both views
    (root / "Taxes").mkdir()
    (root / "Taxes" / "2025.pdf").write_bytes(b"%PDF")
    (root / "Taxes" / "receipt.jpg").write_bytes(b"\xff\xd8")
    (root / "Installers").mkdir()
    (root / "Installers" / "printer-driver.msi").write_bytes(b"x")

    async def _no_plugins():
        return []

    import web.backend.plugin_host as plugin_host
    monkeypatch.setattr(plugin_host, "fetch_plugin_rows", _no_plugins)
    monkeypatch.setattr(files_security, "build_removable_libraries", lambda: [])
    return root.resolve()


def _client(headers=None) -> TestClient:
    return TestClient(app, headers={**XRW, HEADER: DEVICE_TOKEN, **(headers or {})})


def _names(c, library_id, path=""):
    r = c.get("/api/files/browse", params={"library_id": library_id, "path": path})
    assert r.status_code == 200, r.text
    return {e["name"] for e in r.json()["entries"]}, r.json()


# ─── The line itself ────────────────────────────────────────────────────────
def test_the_document_types_are_the_editors_sets_plus_formatted_html():
    for ext in (".md", ".txt", ".html", ".htm", ".docx", ".odt", ".rtf", ".xlsx", ".xls",
                ".csv", ".ods", ".pdf", ".excalidraw", ".svg"):
        assert ext in DOCUMENT_EXTS, ext
    for ext in (".zip", ".exe", ".apk", ".jpg", ".png", ".mp3", ".mp4", ".iso", ""):
        assert ext not in DOCUMENT_EXTS, ext
    assert in_view("documents", "Taxes", True) and in_view("other", "Taxes", True)


def test_a_refusal_names_where_a_file_goes():
    assert documents_refusal("report.pdf") is None
    assert documents_refusal("setup.exe") == OTHER_REFUSAL
    assert "Put pictures in Pictures" in documents_refusal("cat.jpg")
    assert "Put music in Music" in documents_refusal("song.flac")
    assert "Put videos in Videos" in documents_refusal("clip.mkv")


# ─── Listing: the two views split the folder ───────────────────────────────
def test_libraries_offer_other_beside_documents(fake_auth, folder):
    with _client() as c:
        r = c.get("/api/files/libraries")
    assert r.status_code == 200
    libs = {lib["id"]: lib for lib in r.json()["libraries"]}
    assert libs[OTHER_LIBRARY_ID]["label"] == "Other"
    assert libs[OTHER_LIBRARY_ID]["view"] == "other"
    assert libs[OTHER_LIBRARY_ID]["doc_editing"] is False
    assert libs[OTHER_LIBRARY_ID]["reindex_kind"] is None
    assert libs[OTHER_LIBRARY_ID]["editable"] == libs[DOCUMENTS_LIBRARY_ID]["editable"]
    assert libs[DOCUMENTS_LIBRARY_ID]["view"] == "documents"
    assert "root_path" not in libs[OTHER_LIBRARY_ID]
    ids = [lib["id"] for lib in r.json()["libraries"]]
    assert ids.index(OTHER_LIBRARY_ID) == ids.index("core:videos") + 1 if "core:videos" in ids else True
    assert ".html" in r.json()["document_exts"] and ".zip" not in r.json()["document_exts"]


def test_neither_view_lists_the_others_files(fake_auth, folder):
    with _client() as c:
        docs, docs_body = _names(c, DOCUMENTS_LIBRARY_ID)
        other, other_body = _names(c, OTHER_LIBRARY_ID)
    assert DOCS_ONLY <= docs and not (docs & OTHER_ONLY)
    assert OTHER_ONLY <= other and not (other & DOCS_ONLY)
    # Folders show in both, so either view navigates the whole tree.
    assert {"Taxes", "Installers"} <= docs and {"Taxes", "Installers"} <= other
    # Nothing in the folder is missing from both (the secret-shaped name is
    # withheld from both, as it always was).
    on_disk = {p.name for p in folder.iterdir()} - {".env"}
    assert docs | other == on_disk
    assert ".env" not in docs | other
    assert other_body["library_id"] == OTHER_LIBRARY_ID
    assert other_body["doc_editing"] is False
    assert docs_body["folder_total"] == len(docs) and other_body["folder_total"] == len(other)


def test_the_split_holds_in_subfolders_and_paged_searches(fake_auth, folder):
    with _client() as c:
        sub_docs, _ = _names(c, DOCUMENTS_LIBRARY_ID, "Taxes")
        sub_other, _ = _names(c, OTHER_LIBRARY_ID, "Taxes")
        r = c.get("/api/files/browse", params={"library_id": OTHER_LIBRARY_ID, "q": "a",
                                                "limit": 2})
    assert sub_docs == {"2025.pdf"} and sub_other == {"receipt.jpg"}
    body = r.json()
    assert all(e["is_dir"] or e["name"] in OTHER_ONLY for e in body["entries"])
    assert body["total"] == sum(1 for n in OTHER_ONLY | {"Taxes", "Installers"} if "a" in n.lower())


def test_the_documents_api_ignores_what_is_not_a_document(fake_auth, folder):
    with _client() as c:
        r = c.get("/api/documents", params={"kind": "all"})
        assert r.status_code == 200
        names = {row["rel_path"] for row in r.json()}
        assert not (names & OTHER_ONLY)
        assert {"notes.md", "tax.pdf", "budget.xlsx"} <= names


# ─── Uploads through every door ────────────────────────────────────────────
def test_files_upload_refuses_a_non_document_in_documents(fake_auth, folder):
    with _client() as c:
        r = c.post("/api/files/upload",
                   data={"library_id": DOCUMENTS_LIBRARY_ID, "path": "", "device_id": DEV},
                   files=[("files", ("game.zip", b"PK"))])
    assert r.status_code == 415
    assert r.json()["detail"] == f"game.zip: {OTHER_REFUSAL}"
    assert not (folder / "game.zip").exists()


def test_files_upload_of_mixed_files_keeps_the_documents_and_names_the_rest(fake_auth, folder):
    with _client() as c:
        r = c.post("/api/files/upload",
                   data={"library_id": DOCUMENTS_LIBRARY_ID, "path": "", "device_id": DEV},
                   files=[("files", ("minutes.md", b"# m")), ("files", ("photo.png", b"\x89PNG"))])
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == ["minutes.md"]
    assert r.json()["skipped"] and "Put pictures in Pictures" in r.json()["skipped"][0]
    assert (folder / "minutes.md").exists() and not (folder / "photo.png").exists()


def test_other_accepts_any_non_document_and_points_documents_to_documents(fake_auth, folder):
    with _client() as c:
        ok = c.post("/api/files/upload",
                    data={"library_id": OTHER_LIBRARY_ID, "path": "Installers", "device_id": DEV},
                    files=[("files", ("tool.exe", b"MZ")), ("files", ("phone.apk", b"PK")),
                           ("files", ("disk.iso", b"x")), ("files", ("README", b"x"))])
        doc = c.post("/api/files/upload",
                     data={"library_id": OTHER_LIBRARY_ID, "path": "", "device_id": DEV},
                     files=[("files", ("report.docx", b"PK"))])
    assert ok.status_code == 200, ok.text
    assert sorted(ok.json()["saved"]) == ["README", "disk.iso", "phone.apk", "tool.exe"]
    assert (folder / "Installers" / "tool.exe").read_bytes() == b"MZ"   # the same folder on disk
    assert doc.status_code == 415
    assert doc.json()["detail"] == f"report.docx: {DOCUMENT_IN_OTHER_REFUSAL}"


def test_upload_stream_holds_each_view_to_its_types(fake_auth, folder):
    def put(c, lib, rel):
        return c.post("/api/files/upload-stream", content=b"data", params={
            "library_id": lib, "path": "", "rel_path": rel, "device_id": DEV})

    with _client() as c:
        refused = put(c, DOCUMENTS_LIBRARY_ID, "Holiday/beach.mp4")
        wrong_way = put(c, OTHER_LIBRARY_ID, "Holiday/itinerary.pdf")
        doc = put(c, DOCUMENTS_LIBRARY_ID, "Holiday/itinerary.pdf")
        other = put(c, OTHER_LIBRARY_ID, "Holiday/beach.mp4")
    assert refused.status_code == 415 and "Put videos in Videos" in refused.json()["detail"]
    assert wrong_way.status_code == 415 and DOCUMENT_IN_OTHER_REFUSAL in wrong_way.json()["detail"]
    assert doc.status_code == 200 and doc.json()["saved"] == "Holiday/itinerary.pdf"
    assert other.status_code == 200 and other.json()["saved"] == "Holiday/beach.mp4"
    # A mixed folder, each file sent to its own view (what the page does):
    # both land in the one folder, each listed in its own view only.
    with _client() as c:
        assert _names(c, DOCUMENTS_LIBRARY_ID, "Holiday")[0] == {"itinerary.pdf"}
        assert _names(c, OTHER_LIBRARY_ID, "Holiday")[0] == {"beach.mp4"}


def test_upload_check_names_the_files_a_view_will_skip(fake_auth, folder):
    with _client() as c:
        r = c.post("/api/files/upload-check", json={
            "library_id": DOCUMENTS_LIBRARY_ID, "path": "", "device_id": DEV,
            "rel_paths": ["Trip/notes.md", "Trip/map.zip", "Trip/photo.heic"]})
        o = c.post("/api/files/upload-check", json={
            "library_id": OTHER_LIBRARY_ID, "path": "", "device_id": DEV,
            "rel_paths": ["Trip/notes.md", "Trip/map.zip"]})
    assert r.status_code == 200
    assert set(r.json()["refused"]) == {"Trip/map.zip", "Trip/photo.heic"}
    assert r.json()["refused"]["Trip/map.zip"] == OTHER_REFUSAL
    assert o.json()["refused"] == {"Trip/notes.md": DOCUMENT_IN_OTHER_REFUSAL}


def test_the_documents_api_refuses_a_non_document_upload_and_create(fake_auth, folder):
    with _client() as c:
        up = c.post("/api/documents/upload", files=[("files", ("setup.msi", b"x"))])
        mixed = c.post("/api/documents/upload",
                       files=[("files", ("agenda.md", b"x")), ("files", ("track.flac", b"x"))])
        made = c.post("/api/documents/create", json={"name": "notes.bin", "kind": "text"})
        plain = c.post("/api/documents/create", json={"name": "shopping", "kind": "text"})
        text = c.put("/api/documents/text/new.exe", json={"text": "x"})
    assert up.status_code == 415 and OTHER_REFUSAL in up.json()["detail"]
    assert mixed.status_code == 200 and mixed.json()["saved"] == ["agenda.md"]
    assert "Put music in Music" in mixed.json()["skipped"][0]
    assert text.status_code == 415 and made.status_code == 415
    # A new text file named with no extension at all is made as plain text.
    assert plain.status_code == 200 and plain.json()["rel_path"] == "shopping.txt"
    for name in ("setup.msi", "track.flac", "notes.bin", "new.exe"):
        assert not (folder / name).exists()


# ─── Move and copy into a view ─────────────────────────────────────────────
def _move(c, src_lib, paths, dst_lib, dst=""):
    return c.post("/api/files/move", json={
        "source_library_id": src_lib, "paths": paths, "target_library_id": dst_lib,
        "target_path": dst, "device_id": DEV})


def test_a_move_into_a_view_must_fit_it(fake_auth, folder):
    with _client() as c:
        into_docs = _move(c, OTHER_LIBRARY_ID, ["backup.zip"], DOCUMENTS_LIBRARY_ID, "Taxes")
        into_other = _move(c, DOCUMENTS_LIBRARY_ID, ["tax.pdf"], OTHER_LIBRARY_ID, "Installers")
        within = _move(c, OTHER_LIBRARY_ID, ["setup.exe"], OTHER_LIBRARY_ID, "Installers")
        folder_move = _move(c, OTHER_LIBRARY_ID, ["Taxes"], OTHER_LIBRARY_ID, "Installers")
    assert into_docs.json()["moved"] == [] and OTHER_REFUSAL in into_docs.json()["failed"][0]
    assert into_other.json()["moved"] == [] and DOCUMENT_IN_OTHER_REFUSAL in into_other.json()["failed"][0]
    assert (folder / "backup.zip").exists() and (folder / "tax.pdf").exists()
    assert within.json()["moved"] == ["Installers/setup.exe"]
    assert (folder / "Installers" / "setup.exe").exists()
    # A folder goes in either view; its files keep showing in theirs.
    assert folder_move.json()["moved"] == ["Installers/Taxes"]
    with _client() as c:
        assert _names(c, DOCUMENTS_LIBRARY_ID, "Installers/Taxes")[0] == {"2025.pdf"}
        assert _names(c, OTHER_LIBRARY_ID, "Installers/Taxes")[0] == {"receipt.jpg"}


def test_a_move_from_another_library_into_documents_must_be_a_document(fake_auth, folder, tmp_path,
                                                                     monkeypatch):
    pics = tmp_path / "Pictures"
    pics.mkdir()
    (pics / "cat.png").write_bytes(b"\x89PNG")
    monkeypatch.setattr(settings, "pictures_dir", str(pics))
    with _client() as c:
        r = _move(c, "core:pictures", ["cat.png"], DOCUMENTS_LIBRARY_ID, "")
        ok = _move(c, "core:pictures", ["cat.png"], OTHER_LIBRARY_ID, "")
    assert r.status_code == 200 and "Put pictures in Pictures" in r.json()["failed"][0]
    assert ok.json()["moved"] == ["cat.png"] and (folder / "cat.png").exists()


def test_an_import_copy_into_a_view_must_fit_it(fake_auth, folder, tmp_path, monkeypatch):
    usb = tmp_path / "usb"
    (usb / "Drivers").mkdir(parents=True)
    (usb / "installer.exe").write_bytes(b"MZ")
    (usb / "Drivers" / "manual.pdf").write_bytes(b"%PDF")
    (usb / "Drivers" / "driver.inf").write_bytes(b"x")
    stick = MediaLibrary(id="removable:E", label="USB", kind="removable", icon="hard-drive",
                         kind_icon="hard-drive", owner="/dev/sdb1", root_path=usb.resolve(),
                         editable=False, importable=False, doc_editing=False, reindex_kind=None)
    monkeypatch.setattr(files_security, "build_removable_libraries", lambda: [stick])

    def imp(c, path, lib):
        return c.post("/api/files/import", json={
            "source_library_id": "removable:E", "source_path": path,
            "target_library_id": lib, "target_path": "", "device_id": DEV})

    with _client() as c:
        refused = imp(c, "installer.exe", DOCUMENTS_LIBRARY_ID)
        ok = imp(c, "installer.exe", OTHER_LIBRARY_ID)
        tree = imp(c, "Drivers", DOCUMENTS_LIBRARY_ID)
    assert refused.status_code == 415 and OTHER_REFUSAL in refused.json()["detail"]
    assert ok.status_code == 200 and (folder / "installer.exe").read_bytes() == b"MZ"
    assert tree.status_code == 200
    with _client() as c:
        assert _names(c, DOCUMENTS_LIBRARY_ID, "Drivers")[0] == {"manual.pdf"}
        assert _names(c, OTHER_LIBRARY_ID, "Drivers")[0] == {"driver.inf"}


# ─── Same room, same rules ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_other_is_the_documents_library_under_another_name(fake_auth, folder):
    """Same root and the same server-side id, so every rule keyed on the
    Documents library (assert_documents_write_allowed's admin-write hook,
    the listing cache, co-editing's follow-the-move on a Documents→Documents
    move) is the rule for Other too."""
    docs = await files_api._resolve_library(DOCUMENTS_LIBRARY_ID)
    other = await files_api._resolve_library(OTHER_LIBRARY_ID)
    assert other.id == docs.id == DOCUMENTS_LIBRARY_ID
    assert other.root_path == docs.root_path == folder
    assert (other.editable, other.importable, other.kind) == (docs.editable, docs.importable, docs.kind)
    assert other.public_id == OTHER_LIBRARY_ID and other.view == "other" and not other.doc_editing


def test_a_blocked_device_is_blocked_in_other_too(fake_auth, folder):
    with _client() as c:
        r = c.post("/api/files/upload",
                   data={"library_id": OTHER_LIBRARY_ID, "path": "", "device_id": "android-kid"},
                   files=[("files", ("x.zip", b"PK"))])
        m = c.post("/api/files/move", json={
                "source_library_id": OTHER_LIBRARY_ID, "paths": ["backup.zip"],
                "target_library_id": OTHER_LIBRARY_ID, "target_path": "Installers",
                "device_id": "android-kid"})
    assert r.status_code == 403 and m.status_code == 403
    assert not (folder / "x.zip").exists() and (folder / "backup.zip").exists()


def test_the_admin_write_hook_for_documents_covers_other(fake_auth, folder, monkeypatch):
    monkeypatch.setattr(files_api, "ADMIN_WRITE_LIBRARY_IDS", frozenset({DOCUMENTS_LIBRARY_ID}))
    with _client() as c:
        r = c.post("/api/files/upload",
                   data={"library_id": OTHER_LIBRARY_ID, "path": "", "device_id": DEV},
                   files=[("files", ("x.zip", b"PK"))])
    assert r.status_code == 401
    with _client(bearer(ADMIN_TOKEN)) as c:
        r = c.post("/api/files/upload",
                   data={"library_id": OTHER_LIBRARY_ID, "path": "", "device_id": DEV},
                   files=[("files", ("x.zip", b"PK"))])
    assert r.status_code == 200


def test_other_reaches_nothing_documents_withholds(fake_auth, folder):
    (folder / ".ssh").mkdir()
    (folder / ".ssh" / "id_rsa").write_text("k")
    with _client() as c:
        assert c.get("/api/files/download", params={"library_id": OTHER_LIBRARY_ID,
                                                    "path": ".env"}).status_code == 404
        assert c.get("/api/files/browse", params={"library_id": OTHER_LIBRARY_ID,
                                                  "path": ".ssh"}).status_code == 404
        assert c.get("/api/files/download", params={"library_id": OTHER_LIBRARY_ID,
                                                    "path": "../outside"}).status_code in (400, 404)
        up = c.post("/api/files/upload",
                    data={"library_id": OTHER_LIBRARY_ID, "path": "", "device_id": DEV},
                    files=[("files", ("server.key", b"x"))])
    assert up.status_code == 400 and not (folder / "server.key").exists()


# ─── Other only downloads ──────────────────────────────────────────────────
def test_other_files_download_and_nothing_else_reads_them(fake_auth, folder):
    with _client() as c:
        dl = c.get("/api/files/download", params={"library_id": OTHER_LIBRARY_ID, "path": "setup.exe"})
        img = c.get("/api/images/raw", params={"library_id": OTHER_LIBRARY_ID, "path": "wedding.jpg"})
        thumb = c.get("/api/images/thumb", params={"library_id": OTHER_LIBRARY_ID, "path": "wedding.jpg"})
        pr = c.post("/api/print/preview", json={"library_id": OTHER_LIBRARY_ID, "path": "setup.exe"})
    assert dl.status_code == 200 and dl.content == b"x"
    assert "attachment" in dl.headers.get("content-disposition", "")
    assert img.status_code == 404 and thumb.status_code == 404
    assert pr.status_code == 409 and "only be downloaded" in pr.json()["detail"]


def test_a_folder_zip_and_delete_in_other_keep_their_admin_tier(fake_auth, folder):
    with _client() as c:
        z = c.get("/api/files/download", params={"library_id": OTHER_LIBRARY_ID, "path": "Installers"})
        d = c.post("/api/files/delete", json={"library_id": OTHER_LIBRARY_ID, "paths": ["setup.exe"]})
    assert z.status_code == 401 and d.status_code == 401
    with _client(bearer(ADMIN_TOKEN)) as c:
        z = c.get("/api/files/download", params={"library_id": OTHER_LIBRARY_ID, "path": "Installers"})
        d = c.post("/api/files/delete", json={"library_id": OTHER_LIBRARY_ID, "paths": ["setup.exe"]})
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    assert d.status_code == 200 and d.json()["deleted"] == ["setup.exe"]
    assert not (folder / "setup.exe").exists()


# ─── The route auth matrix, for Other ──────────────────────────────────────
OTHER_ROUTES = [
    # (method, path, kwargs, tier)
    ("GET", "/api/files/browse", {"params": {"library_id": OTHER_LIBRARY_ID}}, "device"),
    ("GET", "/api/files/download", {"params": {"library_id": OTHER_LIBRARY_ID, "path": "app.apk"}}, "device"),
    ("POST", "/api/files/upload-stream", {"params": {"library_id": OTHER_LIBRARY_ID, "rel_path": "n.zip",
                                                     "device_id": DEV}, "content": b"x"}, "device"),
    ("POST", "/api/files/upload-check", {"json": {"library_id": OTHER_LIBRARY_ID, "device_id": DEV,
                                                  "rel_paths": ["a.zip"]}}, "device"),
    ("POST", "/api/files/move", {"json": {"source_library_id": OTHER_LIBRARY_ID, "paths": ["todo"],
                                          "target_library_id": OTHER_LIBRARY_ID,
                                          "target_path": "Installers", "device_id": DEV}}, "device"),
    ("GET", "/api/files/download", {"params": {"library_id": OTHER_LIBRARY_ID, "path": "Installers"}}, "admin"),
    ("POST", "/api/files/delete", {"json": {"library_id": OTHER_LIBRARY_ID, "paths": ["app.apk"]}}, "admin"),
]


@pytest.mark.parametrize("method,path,kw,tier", OTHER_ROUTES,
                         ids=[f"{m} {p} {t}" for m, p, _k, t in OTHER_ROUTES])
def test_other_route_auth_matrix(fake_auth, folder, method, path, kw, tier):
    with TestClient(app, headers=XRW) as c:
        assert c.request(method, path, **kw).status_code == 401
    with TestClient(app, headers={**XRW, HEADER: DEVICE_TOKEN}) as c:
        got = c.request(method, path, **kw).status_code
        assert (got == 401) if tier == "admin" else (got == 200), got
    with TestClient(app, headers={**XRW, **bearer(ADMIN_TOKEN)}) as c:
        assert c.request(method, path, **kw).status_code == 200


# ─── Boot and registry ─────────────────────────────────────────────────────
def test_other_needs_no_folder_of_its_own(folder):
    """Other is a view, not a directory: boot creates the Documents folder
    and nothing else for it, and Other follows Documents in and out."""
    attrs = [row[3] for row in files_security.CORE_LIBRARIES]
    assert "other_dir" not in attrs and attrs.count("documents_dir") == 1
    built = files_security.build_core_libraries(files_security._allowed_under_config())
    docs = next(lib for lib in built if lib.id == DOCUMENTS_LIBRARY_ID)
    assert docs.view == "documents"
    assert not any(lib.public_id == OTHER_LIBRARY_ID for lib in built)
    assert [lib.public_id for lib in files_api._with_other(built)].count(OTHER_LIBRARY_ID) == 1
    assert files_api._with_other([lib for lib in built if lib.id != DOCUMENTS_LIBRARY_ID]) == \
        [lib for lib in built if lib.id != DOCUMENTS_LIBRARY_ID]


# ─── Co-editing history follows a move made in Other ───────────────────────
def test_a_folder_moved_in_other_keeps_its_documents_coediting_history(fake_auth, folder, monkeypatch):
    """Documents' co-editing (web/backend/api/collab.py, the editors batch)
    follows a move inside Documents and forgets a document that leaves it.
    Other IS Documents server-side, so a folder of co-edited documents moved
    through Other is a move inside Documents: its history follows. Skipped
    on a tree without co-editing."""
    collab = pytest.importorskip("web.backend.api.collab")
    calls: list[tuple] = []

    async def _moved(old, new):
        calls.append(("moved", old, new))

    async def _forget(paths):
        calls.append(("forget", tuple(paths)))

    monkeypatch.setattr(collab, "note_moved", _moved)
    monkeypatch.setattr(collab, "forget_paths", _forget)
    with _client() as c:
        r = _move(c, OTHER_LIBRARY_ID, ["Taxes"], OTHER_LIBRARY_ID, "Installers")
        back = _move(c, DOCUMENTS_LIBRARY_ID, ["Installers/Taxes"], OTHER_LIBRARY_ID, "")
    assert r.json()["moved"] == ["Installers/Taxes"] and back.json()["moved"] == ["Taxes"]
    assert calls == [("moved", "Taxes", "Installers/Taxes"), ("moved", "Installers/Taxes", "Taxes")]
