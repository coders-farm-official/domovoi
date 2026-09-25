"""Normalise before deciding, and refuse what cannot be normalised.

Every gate on the Documents write path — the reserved-name filter, the
per-editor extension gate, the containment check — asks a question about
the name the CALLER SENT. The filesystem then stores the bytes under a
name of its own choosing, and where those two disagree, the gate answered
about a file that was never written:

* ``.env.`` and ``.env `` are not ``.env``, so the reserved-name filter
  passed them — and Windows trimmed the trailing dot or space at the
  filesystem layer and stored them AS ``.env``. The result was exactly
  the state the filter exists to prevent: a file in ``core:documents``
  that ``/api/files`` browse, download, the directory zip and
  ``media_walk`` all refuse to show, while ``GET /api/documents`` lists
  it. Four doors were affected.
* ``tax.pdf:stash.md`` is not a file called ``tax.pdf``, so containment
  passed (it IS inside Documents) and the text editor's gate read the
  suffix as ``.md`` — and NTFS wrote the bytes into an alternate data
  stream hanging off the PDF. Invisible to the Documents list, to the
  Files page, to the zip, to ``media_walk`` and to ``os.listdir``,
  readable straight back through ``/raw``: persistent storage inside the
  operator's Documents that the operator cannot see, list, export or
  delete.
* a percent-encoded NUL or tab, and ``::$INDEX_ALLOCATION``, are answered
  by the OS with an exception rather than an error, which escaped the
  handler as an unhandled **500**. Nothing was written in any of those
  cases — but a 500 in the log for an input a phone can send is noise
  that hides the real ones.

The platform split is deliberate and is asserted both ways below. On
POSIX a colon is an ordinary character in a filename ("Meeting: notes.md")
and a trailing dot is a genuinely different file, so refusing them there
would break real names to fix a problem that does not exist there. The
reserved-name normalisation, by contrast, runs on both platforms: nothing
legitimate is called ``.env.``, and a filter whose answer depends on the
host OS is a filter nobody can reason about.

DB-free by construction (the same shape as ``test_documents_write_guard``).
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import web.backend.api.files as files_api
from domovoi.config import settings
from domovoi.tests.auth_testkit import HEADER, install_fake_db
from web.backend.api import files_security as fs
from web.backend.main import app

DEVICE_TOKEN = "device-token"
DEVICE = {HEADER: DEVICE_TOKEN}


@pytest.fixture
def claimed(monkeypatch):
    return install_fake_db(monkeypatch, admin=True, device_token=DEVICE_TOKEN)


@pytest.fixture(autouse=True)
def _nobody_is_blocked(monkeypatch):
    async def _not_blocked(device_id):
        return None

    monkeypatch.setattr(files_api, "_block_status", _not_blocked)


@pytest.fixture
def docs_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    (tmp_path / "notes.md").write_text("# hello\n", encoding="utf-8")
    (tmp_path / "tax.pdf").write_bytes(b"%PDF-1.7" + bytes(512))
    return tmp_path


@pytest.fixture
def windows_rules(monkeypatch):
    """Ask the module the Windows question regardless of the runner."""
    monkeypatch.setattr(fs, "WINDOWS_NAME_RULES", True)


@pytest.fixture
def posix_rules(monkeypatch):
    monkeypatch.setattr(fs, "WINDOWS_NAME_RULES", False)


def _client() -> TestClient:
    return TestClient(
        app,
        headers={"X-Requested-With": "domovoi-tests", **DEVICE},
        # A 500 must be OBSERVABLE as a 500 here, not raised into the test
        # — the whole point of the last section is which status a malformed
        # path produces.
        raise_server_exceptions=False,
    )


def _names(root):
    return sorted(p.name for p in root.iterdir())


# ═══ The reserved-name filter asks about the file that gets written ══

RESERVED_SPELLINGS = [".env.", ".env ", "id_rsa.key.", "ID_RSA.KEY ", "server.pem."]


@pytest.mark.parametrize("name", RESERVED_SPELLINGS)
def test_a_trailing_dot_or_space_does_not_launder_a_reserved_name(name):
    """The unit-level claim, on both platforms: the filter answers about
    every name this one could become on disk."""
    assert fs.is_sensitive_name(name) is True


def test_a_stream_reference_to_a_reserved_name_is_reserved_too(windows_rules):
    """``.env:x`` creates ``.env`` with a stream hanging off it, so the
    host name is what the filter has to judge."""
    assert fs.is_sensitive_name(".env:x") is True


def test_a_colon_in_a_posix_filename_is_just_a_colon(posix_rules):
    """"Meeting: notes.md" is an ordinary, legal, visible filename on the
    box this actually ships to. Over-matching there would break it."""
    assert fs.is_sensitive_name("Meeting: notes.md") is False
    assert fs.unstorable_reason("Meeting: notes.md") is None
    assert fs.unstorable_reason("notes.md.") is None


@pytest.mark.parametrize("name", RESERVED_SPELLINGS)
def test_no_door_writes_a_reserved_name_under_its_real_name(claimed, docs_dir, name):
    """All four doors, as behaviour and as bytes on disk. Before this,
    ``PUT /text/.env.`` answered 200 and the Files page then had a file it
    would never list, serve or export."""
    c = _client()
    before = _names(docs_dir)

    assert c.put(f"/api/documents/text/{name}", json={"text": "x"}).status_code == 400
    assert (
        c.post("/api/documents/create", json={"name": name, "kind": "text"}).status_code
        == 400
    )
    assert (
        c.post(
            "/api/documents/upload", files={"files": (name, b"x", "text/plain")}
        ).status_code
        == 400
    )
    assert _names(docs_dir) == before, f"{name!r} left something behind"


# ═══ Alternate data streams ══════════════════════════════════════════

ADS_PATHS = [
    "tax.pdf:stash.md",
    "notes.md:hide.md",
    "tax.pdf::$INDEX_ALLOCATION",
    "tax.pdf:$DATA",
]


@pytest.mark.parametrize("rel_path", ADS_PATHS)
def test_the_text_door_will_not_write_into_a_stream(
    claimed, docs_dir, windows_rules, rel_path
):
    """This is the one input class where the write landed at a path whose
    category gate it never actually passed: the gate read ``.md``, the
    filesystem wrote into the PDF."""
    pdf = (docs_dir / "tax.pdf").read_bytes()
    before = _names(docs_dir)
    r = _client().put(f"/api/documents/text/{rel_path}", json={"text": "hidden"})
    assert r.status_code == 400, r.text
    assert "alternate data stream" in r.json()["detail"]
    assert _names(docs_dir) == before
    assert (docs_dir / "tax.pdf").read_bytes() == pdf


def test_the_sheet_and_drawing_doors_close_with_it(claimed, docs_dir, windows_rules):
    """All three editor doors resolve through ``_safe_target``, so the one
    guard covers them — asserted rather than assumed."""
    c = _client()
    r = c.put("/api/documents/sheet/ledger.csv:hidden.csv", json={"rows": [[{"v": "1"}]]})
    assert r.status_code == 400
    r = c.post(
        "/api/documents/drawings/write",
        json={"rel_path": "board.excalidraw:x.excalidraw", "content": "{}"},
    )
    assert r.status_code == 400
    assert _names(docs_dir) == ["notes.md", "tax.pdf"]


def test_an_ordinary_save_is_untouched_by_any_of_this(claimed, docs_dir):
    """The cost side of the ledger: the names people actually use still
    save, on both platforms."""
    c = _client()
    for name in ["notes.md", "shopping list.txt", "réçu.md", "a-b_c.1.md"]:
        r = c.put(f"/api/documents/text/{name}", json={"text": "milk"})
        assert r.status_code == 200, f"{name}: {r.text}"
        assert (docs_dir / name).read_text(encoding="utf-8") == "milk"


# ═══ A malformed path is a 400, not a 500 ════════════════════════════

ENCODED_500_SHAPES = [
    "tax.pdf%00.md",     # percent-encoded NUL  -> ValueError out of stat()
    "tax.pdf%09",        # percent-encoded tab  -> OSError [Errno 22]
    "tax.pdf%09.md",
    "tax.pdf%01.md",
]


@pytest.mark.parametrize("encoded", ENCODED_500_SHAPES)
def test_a_control_character_in_a_path_is_answered_not_crashed(
    claimed, docs_dir, encoded
):
    """Sent as the literal percent-encoding a phone would put on the wire;
    Starlette decodes it to a raw control character before the handler
    sees it. Nothing is written either way — only the status code and the
    log line change, and a 500 that is really a 400 is how real 500s go
    unnoticed."""
    pdf = (docs_dir / "tax.pdf").read_bytes()
    url = httpx.URL(f"http://testserver/api/documents/text/{encoded}")
    r = _client().put(url, json={"text": "x"})
    assert r.status_code == 400, f"{encoded} -> {r.status_code}"
    assert "control characters" in r.json()["detail"]
    assert _names(docs_dir) == ["notes.md", "tax.pdf"]
    assert (docs_dir / "tax.pdf").read_bytes() == pdf


def test_an_os_level_refusal_of_a_path_is_a_400_as_well(claimed, docs_dir, monkeypatch):
    """The shapes this process cannot enumerate — a name past the OS path
    limit, a reserved device name, a volume that disagrees — arrive as an
    ``OSError`` from ``stat()``/``write_text()`` at the moment of use. They
    belong in the 4xx range too, so the guard is on the call sites and not
    only on the patterns we happened to think of."""
    import pathlib

    real_write = pathlib.Path.write_text

    def boom(self, *a, **kw):
        if self.name == "boom.md":
            raise OSError(22, "Invalid argument")
        return real_write(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "write_text", boom)
    r = _client().put("/api/documents/text/boom.md", json={"text": "x"})
    assert r.status_code == 400
    assert "not a usable document path" in r.json()["detail"]
