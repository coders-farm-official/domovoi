"""A household save writes only what its editor edits — and the two doors
into the Documents folder enforce the same rules.

Saving a document, a spreadsheet or a drawing became a HOUSEHOLD action on
2026-09-24 (see ``test_web_media_auth``, which pins who may call these
routes). This module pins what those routes will then DO, which is the
other half of the same decision and the half that was missing:

* ``PUT /api/documents/text/{p}`` writes the kinds of file the text and
  markdown editors open — and nothing else. Without that guard, moving it
  to the device tier handed every holder of the single shared household
  token a general-purpose overwrite primitive over the operator's real
  ``~/Documents``: ``{"text": ""}`` at ``tax-return-2025.pdf`` answered
  200 and left a zero-byte PDF. Deleting is the one verb that stayed
  admin, so a save that destroys an arbitrary file is that decision
  inverted, not implemented.
* ``PUT /api/documents/sheet/{p}`` writes ``.xlsx``/``.csv`` and refuses
  the rest BEFORE it creates anything, so a save the server is not going
  to do leaves nothing behind.
* ``/api/files`` and ``/api/documents`` are TWO DOORS into the one
  directory (``core:documents``). Whatever one refuses, the other must
  refuse, or a caller simply picks the other door. That is checked here
  as behaviour — block a device and watch BOTH doors shut; make the
  library admin-write and watch BOTH doors ask for an admin — precisely
  because the bug was a copy of the rules drifting from the rules.

DB-FREE by construction, like ``test_web_media_auth``: the auth
primitives are faked and the one database lookup the handlers make (the
files device-block) is stubbed per test, so nothing here can hide behind
a ``requires_db`` skip.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import web.backend.api.files as files_api
from domovoi.config import settings
from domovoi.tests.auth_testkit import HEADER, install_fake_db
from web.backend.main import app

DEVICE_TOKEN = "device-token"
DEVICE = {HEADER: DEVICE_TOKEN}

# Bytes that are NOT valid UTF-8 — a real binary payload, not a string
# that merely looks binary.
BINARY = b"\xff\xd8\xff\xe0" + bytes(4096)


@pytest.fixture
def claimed(monkeypatch):
    """An install past first-run setup, holding one household device
    token and no admin session — the persona the decision is about."""
    return install_fake_db(monkeypatch, admin=True, device_token=DEVICE_TOKEN)


@pytest.fixture
def docs_dir(monkeypatch, tmp_path):
    """A Documents folder seeded the way a real one looks: a note the
    editor owns, and several files it does not."""
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    (tmp_path / "notes.md").write_text("# hello\n", encoding="utf-8")
    (tmp_path / "tax-return-2025.pdf").write_bytes(b"%PDF-1.7" + bytes(4096))
    (tmp_path / "budget.xlsx").write_bytes(b"PK\x03\x04" + bytes(4096))
    (tmp_path / "backup.dat").write_bytes(BINARY)
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "wedding.jpg").write_bytes(BINARY)
    return tmp_path


@pytest.fixture(autouse=True)
def _nobody_is_blocked(monkeypatch):
    """The files device-block is the only database read these handlers
    make. Default it to "not blocked" so the tests that are about the
    EDITOR guards do not need Postgres; the tests that are about the
    block override it."""

    async def _not_blocked(device_id):
        return None

    monkeypatch.setattr(files_api, "_block_status", _not_blocked)


def _client() -> TestClient:
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests", **DEVICE})


def _snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


# ═══ The text editor writes only what the text editor opens ══════════

# Each of these belongs to another editor, or to no editor at all. The
# Documents page routes them elsewhere: spreadsheets to the sheet grid,
# .excalidraw to the whiteboard, PDFs and pictures to a new browser tab,
# legacy office formats to Download.
NOT_THE_TEXT_EDITORS = [
    "tax-return-2025.pdf",
    "budget.xlsx",
    "photos/wedding.jpg",
]


@pytest.mark.parametrize("rel_path", NOT_THE_TEXT_EDITORS)
def test_a_household_device_cannot_overwrite_a_pdf_or_a_sheet_with_text(
    claimed, docs_dir, rel_path
):
    """The blocker, as behaviour: a paired device with a perfectly valid
    household token PUTs empty text at a file the text editor never
    opens, and the bytes are still there afterwards."""
    before = _snapshot(docs_dir)
    r = _client().put(f"/api/documents/text/{rel_path}", json={"text": ""})
    assert r.status_code == 415, r.text
    assert "text editor" in r.json()["detail"]
    assert _snapshot(docs_dir) == before


def test_the_refusal_costs_the_bytes_nothing_even_for_a_new_name(claimed, docs_dir):
    """A target that does not exist yet is refused on its extension
    alone, and no file appears — the guard is not "only protect what is
    already there"."""
    before = _snapshot(docs_dir)
    r = _client().put("/api/documents/text/new-scan.pdf", json={"text": "hello"})
    assert r.status_code == 415
    assert not (docs_dir / "new-scan.pdf").exists()
    assert _snapshot(docs_dir) == before


def test_a_drawing_is_not_the_text_editors_to_write(claimed, docs_dir):
    """.excalidraw and .svg belong to the whiteboard, which has its own
    save route and its own guard."""
    (docs_dir / "plan.excalidraw").write_text('{"elements":[]}', encoding="utf-8")
    r = _client().put("/api/documents/text/plan.excalidraw", json={"text": "gone"})
    assert r.status_code == 415
    assert (docs_dir / "plan.excalidraw").read_text(encoding="utf-8") == '{"elements":[]}'


def test_a_binary_wearing_an_unknown_extension_is_still_refused(claimed, docs_dir):
    """The extension table cannot classify everything: ``.dat`` routes to
    the text editor because unrecognized types do. The second half of the
    guard asks the file itself — the same question ``GET /text`` asks
    before it hands a buffer over — and refuses what it could not have
    opened."""
    r = _client().put("/api/documents/text/backup.dat", json={"text": "gone"})
    assert r.status_code == 415
    assert (docs_dir / "backup.dat").read_bytes() == BINARY


def test_a_file_too_big_for_the_editor_is_not_overwritten_by_it(claimed, docs_dir):
    """``GET /text`` answers 415 ``too_large`` above the editor's limit
    rather than streaming megabytes into a textarea. The save refuses the
    same file, so "the editor could not open it" and "the editor may not
    replace it" stay the same sentence."""
    from web.backend.api import documents as docs

    big = b"a" * (docs._TEXT_MAX_BYTES + 1)
    (docs_dir / "huge.txt").write_bytes(big)
    assert _client().get("/api/documents/text/huge.txt").status_code == 415
    r = _client().put("/api/documents/text/huge.txt", json={"text": "gone"})
    assert r.status_code == 415
    assert (docs_dir / "huge.txt").read_bytes() == big


# ── and everything it DOES open still saves ──

@pytest.mark.parametrize("rel_path", ["notes.md", "list.txt", "server.log", "todo"])
def test_the_household_still_saves_the_files_the_editor_opens(
    claimed, docs_dir, rel_path
):
    """The guard must not cost the decision anything. Markdown, plain
    text, an unrecognized-but-textual extension and a name with no
    extension at all are all the text editor's, and a device token saves
    every one of them."""
    r = _client().put(f"/api/documents/text/{rel_path}", json={"text": "milk, oats"})
    assert r.status_code == 200, r.text
    assert (docs_dir / rel_path).read_text(encoding="utf-8") == "milk, oats"


def test_a_save_does_not_invent_folders_inside_the_operators_documents(
    claimed, docs_dir
):
    """A PUT at a path whose folder does not exist is a 404, not a
    silent ``mkdir -p`` in somebody's Documents."""
    r = _client().put("/api/documents/text/a/b/c.md", json={"text": "x"})
    assert r.status_code == 404
    assert not (docs_dir / "a").exists()
    # The folder that DOES exist keeps working.
    ok = _client().put("/api/documents/text/photos/caption.md", json={"text": "x"})
    assert ok.status_code == 200
    assert (docs_dir / "photos" / "caption.md").read_text(encoding="utf-8") == "x"


# ═══ The sheet editor, same shape ════════════════════════════════════

def test_the_sheet_save_refuses_a_non_sheet_cleanly(claimed, docs_dir):
    """415 with the file untouched — not a 500, and not a write. The
    refusal used to arrive only after the handler had already run
    ``mkdir(parents=True)``."""
    before = _snapshot(docs_dir)
    r = _client().put("/api/documents/sheet/notes.md", json={"rows": []})
    assert r.status_code == 415, r.text
    assert "sheet editor" in r.json()["detail"]
    assert _snapshot(docs_dir) == before


def test_a_refused_sheet_save_leaves_no_folders_behind(claimed, docs_dir):
    """The half-write: the old handler created the directory tree, THEN
    discovered it would not write the file."""
    r = _client().put("/api/documents/sheet/a/b/c.pdf", json={"rows": []})
    assert r.status_code in (404, 415)
    assert not (docs_dir / "a").exists()


@pytest.mark.parametrize("name", ["data.csv", "sums.xlsx"])
def test_the_household_still_saves_a_spreadsheet(claimed, docs_dir, name):
    r = _client().put(
        f"/api/documents/sheet/{name}", json={"rows": [[{"v": "1"}, {"v": "2"}]]}
    )
    assert r.status_code == 200, r.text
    assert (docs_dir / name).exists()


# ═══ Two doors, one room ═════════════════════════════════════════════

SAVES = [
    ("post", "/api/documents/create", {"json": {"name": "shopping.txt", "kind": "text"}}),
    ("put", "/api/documents/text/notes.md", {"json": {"text": "milk"}}),
    ("put", "/api/documents/sheet/data.csv", {"json": {"rows": []}}),
    ("post", "/api/documents/drawings/write",
     {"json": {"rel_path": "plan.excalidraw", "content": "{}"}}),
]
SAVE_IDS = [f"{m.upper()} {p}" for m, p, _ in SAVES]


@pytest.mark.parametrize(("method", "path", "kw"), SAVES, ids=SAVE_IDS)
def test_a_device_blocked_in_settings_cannot_write_documents_either(
    claimed, docs_dir, monkeypatch, method, path, kw
):
    """An operator who takes file writes away from a tablet in
    Settings → Devices takes them away on BOTH doors. Before this, the
    tablet was refused at ``POST /api/files/upload`` into
    ``core:documents`` and succeeded at the documents route into the
    identical directory."""

    async def _blocked(device_id):
        return "tablet isn't allowed to change files"

    monkeypatch.setattr(files_api, "_block_status", _blocked)
    before = _snapshot(docs_dir)
    body = dict(kw["json"], device_id="tablet")
    r = getattr(_client(), method)(path, json=body)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "tablet isn't allowed to change files"
    assert _snapshot(docs_dir) == before


def test_the_block_is_the_files_check_itself_not_a_copy_of_it(
    claimed, docs_dir, monkeypatch
):
    """Stubbing the block inside ``web.backend.api.files`` changes what
    ``/api/documents`` does. That is only true if the documents router
    CALLS that module rather than carrying its own copy of the rule —
    and the copy drifting is the whole finding."""

    async def _blocked(device_id):
        return f"{device_id} isn't allowed to change files"

    monkeypatch.setattr(files_api, "_block_status", _blocked)
    r = _client().put(
        "/api/documents/text/notes.md", json={"text": "x", "device_id": "phone-7"}
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "phone-7 isn't allowed to change files"


def test_making_documents_admin_write_again_shuts_both_doors(
    claimed, docs_dir, monkeypatch
):
    """The other half of the shared check. ``ADMIN_WRITE_LIBRARY_IDS`` is
    the hook that says "writes into this library need an admin"; it is
    empty today because saving became a household action. Put
    ``core:documents`` back into it and the DOCUMENTS door asks for an
    admin too, instead of quietly staying open — which is what it did
    while it held its own copy of the policy."""
    monkeypatch.setattr(
        files_api, "ADMIN_WRITE_LIBRARY_IDS", frozenset({"core:documents"})
    )
    r = _client().put("/api/documents/text/notes.md", json={"text": "x"})
    assert r.status_code == 401, r.text
    assert (docs_dir / "notes.md").read_text(encoding="utf-8") == "# hello\n"


# ── secret-shaped names ──

SENSITIVE = [".env", "id_rsa.key", "server.pem", "pairing_token", "setup-code.txt"]


@pytest.mark.parametrize("name", SENSITIVE)
def test_a_name_the_files_door_refuses_is_refused_here_too(claimed, docs_dir, name):
    """``POST /api/files/upload`` skips these names, and every Files
    listing, download and zip then behaves as if such a file does not
    exist. A name this router wrote but that router will never show again
    is a file hidden inside the library the Files page owns."""
    r = _client().put(f"/api/documents/text/{name}", json={"text": "secret"})
    assert r.status_code == 400, r.text
    assert not (docs_dir / name).exists()

    created = _client().post("/api/documents/create", json={"name": name, "kind": "text"})
    assert created.status_code == 400
    assert not (docs_dir / name).exists()


@pytest.mark.parametrize("name", [".env", "id_rsa.key"])
def test_the_documents_upload_skips_a_secret_shaped_name(claimed, docs_dir, name):
    """The upload door, which is the one the finding was found through:
    the file is skipped by name, exactly as ``/api/files/upload`` skips
    it, and the batch fails when it was the only file."""
    r = _client().post(
        "/api/documents/upload", files={"files": (name, b"SECRET", "text/plain")}
    )
    assert r.status_code == 400, r.text
    assert "reserved" in r.json()["detail"]
    assert not (docs_dir / name).exists()


def test_an_ordinary_upload_still_lands(claimed, docs_dir):
    r = _client().post(
        "/api/documents/upload", files={"files": ("recipe.md", b"# soup", "text/markdown")}
    )
    assert r.status_code == 200, r.text
    assert (docs_dir / "recipe.md").read_bytes() == b"# soup"


# ── the one rule the doors do NOT share, pinned so it cannot drift silently ──

def test_a_save_that_names_no_device_is_still_accepted_today(
    claimed, docs_dir, monkeypatch
):
    """``device_id`` is REQUIRED on ``/api/files`` and OPTIONAL here,
    because the in-app editors and the Android Documents screen do not
    send one yet and requiring it would break Save for every existing
    client. This test states that gap out loud: a blocked device that
    omits the field still saves through this door.

    It is the row in the two-doors table that is not yet equal. When the
    clients learn to name themselves, make the field required and flip
    this test — do not delete it.
    """

    async def _blocked(device_id):
        return "tablet isn't allowed to change files"

    monkeypatch.setattr(files_api, "_block_status", _blocked)
    r = _client().put("/api/documents/text/notes.md", json={"text": "milk"})
    assert r.status_code == 200
    assert (docs_dir / "notes.md").read_text(encoding="utf-8") == "milk"
