"""An empty save never silently replaces a document that says something.

wf/editors-followups (2026-10-27): a document was found empty after a
version restore. The editor could write the file from a passing state
(destroyed while closing, so ``getHTML()`` fell back to what it was opened
with; a blank new document then came back blank). ``PUT /api/documents/doc``
now refuses an EMPTY body over a file with content (409
``empty_over_content``) unless the request carries ``allow_empty``, which
the editor sends only from a live editor (on a synced shared document when
co-editing): the person deleted everything, or restored an empty version.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from domovoi import richdoc
from domovoi.config import settings
from domovoi.tests.auth_testkit import install_fake_db
from web.backend.api import documents as documents_api
from web.backend.api import files as files_api
from web.backend.main import app


@pytest.fixture(autouse=True)
def _pre_setup(monkeypatch):
    install_fake_db(monkeypatch, admin=False)

    async def _not_blocked(device_id):
        return None

    monkeypatch.setattr(files_api, "_block_status", _not_blocked)


@pytest.fixture
def docs_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    return tmp_path


def _client():
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


def _put(name, **body):
    return _client().put(f"/api/documents/doc/{name}", json=body)


def _write_html(docs_dir, name, body):
    (docs_dir / name).write_text(richdoc.document_file(body, name.rsplit(".", 1)[0]), encoding="utf-8")


@pytest.mark.parametrize("html,blank", [
    ("", True),
    ("<p></p>", True),
    ("<p></p><p><br></p>", True),
    ("<h2> </h2><p>&nbsp;</p>", True),
    ("<p>​</p>", True),
    ("<p>x</p>", False),
    ("<p></p><figure><img src='data:image/png;base64,AAAA'></figure>", False),
    ("<table><tbody><tr><td><p></p></td></tr></tbody></table>", False),
    ("<hr>", False),
])
def test_what_counts_as_an_empty_document(html, blank):
    assert documents_api._blank_html(html) is blank


@pytest.mark.parametrize("name", ["Letter.html", "Letter.htm"])
def test_an_empty_save_over_a_document_with_content_is_refused(docs_dir, name):
    _write_html(docs_dir, name, "<h2>Dear Ana</h2><p>Three pages of it.</p>")
    before = (docs_dir / name).read_bytes()
    r = _put(name, html="<p></p>")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["reason"] == "empty_over_content"
    assert (docs_dir / name).read_bytes() == before


def test_the_person_can_still_clear_a_document(docs_dir):
    _write_html(docs_dir, "Letter.html", "<p>Start over.</p>")
    r = _put("Letter.html", html="<p></p>", allow_empty=True)
    assert r.status_code == 200, r.text
    assert documents_api._blank_html(richdoc.document_body((docs_dir / "Letter.html").read_text("utf-8")))


def test_an_empty_save_over_an_empty_document_needs_nothing(docs_dir):
    _write_html(docs_dir, "New.html", "<p></p>")
    assert _put("New.html", html="<p></p>").status_code == 200


def test_a_picture_only_document_is_content(docs_dir):
    _write_html(docs_dir, "Photo.html",
                "<figure><img src='data:image/png;base64,iVBORw0KGgo='></figure>")
    assert _put("Photo.html", html="<p></p>").status_code == 409


def test_content_over_content_is_untouched_by_the_guard(docs_dir):
    _write_html(docs_dir, "Letter.html", "<p>one</p>")
    assert _put("Letter.html", html="<p>two</p>").status_code == 200


def test_markdown_gets_the_same_guard(docs_dir):
    (docs_dir / "notes.md").write_text("# Notes\n\n- milk\n", encoding="utf-8")
    r = _put("notes.md", html="<p></p>", markdown="\n")
    assert r.status_code == 409
    assert (docs_dir / "notes.md").read_text("utf-8").startswith("# Notes")
    assert _put("notes.md", html="<p></p>", markdown="", allow_empty=True).status_code == 200
    assert (docs_dir / "notes.md").read_text("utf-8").strip() == ""


def test_a_missing_file_is_nothing_to_protect(docs_dir):
    assert documents_api._file_says_something(docs_dir / "gone.html", "html") is False
