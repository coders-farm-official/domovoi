"""The documents door READS with the Files door's secret-shaped-name filter
(WEB-14).

``/api/files`` and ``/api/documents`` both serve the operator's real
``~/Documents``. The Files door has always pretended a ``.env``, a
``*.pem`` / ``*.key`` / ``*.p12`` or a ``pairing_token`` does not exist —
not listed, ``404`` on download, left out of a zip. The documents door
applied the same filter only when SAVING, so the key material the Files
page withheld was listed by ``GET /api/documents`` and served by
``/raw`` to any paired device (or a cookie-only tab, or ``?device_token=``).

DB-free: the auth primitives are faked; the folder is a tmp dir.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from domovoi.config import settings
from domovoi.tests.auth_testkit import HEADER, bearer, install_fake_db
from web.backend.api import documents as documents_api
from web.backend.main import app

DEVICE_TOKEN = "device-token"
ADMIN_TOKEN = "admin-token"

SECRETS = [".env", "id_rsa.pem", "server.key", "bundle.p12", "pairing_token"]


@pytest.fixture
def claimed(monkeypatch):
    return install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )


@pytest.fixture
def docs_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    (tmp_path / "shopping.txt").write_text("milk\n", encoding="utf-8")
    for name in SECRETS:
        (tmp_path / name).write_text(f"SECRET {name}\n", encoding="utf-8")
    (tmp_path / "tls").mkdir()
    (tmp_path / "tls" / "readme.txt").write_text("SECRET tls\n", encoding="utf-8")
    (tmp_path / "keys.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (tmp_path / "x.pem.md").write_text("fine\n", encoding="utf-8")
    return tmp_path


def _client(**headers) -> TestClient:
    return TestClient(
        app,
        headers={"X-Requested-With": "domovoi-tests", HEADER: DEVICE_TOKEN, **headers},
    )


def test_the_listing_leaves_secret_shaped_names_out(claimed, docs_dir):
    r = _client().get("/api/documents", params={"kind": "all"})
    assert r.status_code == 200, r.text
    names = {row["rel_path"] for row in r.json()}
    assert "shopping.txt" in names
    # A secret-shaped SUFFIX is the last one; "x.pem.md" is a markdown note.
    assert "x.pem.md" in names
    assert not names & set(SECRETS), names


@pytest.mark.parametrize("name", SECRETS)
@pytest.mark.parametrize(
    "route", ["/api/documents/raw/{}", "/api/documents/text/{}"]
)
def test_a_secret_shaped_name_reads_as_missing(claimed, docs_dir, route, name):
    r = _client().get(route.format(name))
    assert r.status_code == 404, r.text
    assert "SECRET" not in r.text


@pytest.mark.parametrize("name", ["id_rsa.pem", ".env"])
def test_the_query_token_does_not_open_it_either(claimed, docs_dir, name):
    """The cookie-free read path the phase-2 run used: ``?device_token=``
    and no header."""
    c = TestClient(app)
    r = c.get(f"/api/documents/raw/{name}", params={"device_token": DEVICE_TOKEN})
    assert r.status_code == 404, r.text


def test_a_secret_shaped_folder_hides_what_is_inside_it(claimed, docs_dir):
    """``tls`` is on the deny list as a name; a file inside it is not
    reachable through the folder either."""
    r = _client().get("/api/documents/raw/tls/readme.txt")
    assert r.status_code == 404, r.text
    assert "SECRET" not in r.text


def test_the_other_read_routes_agree(claimed, docs_dir):
    c = _client()
    assert c.get("/api/documents/export/doc/.env").status_code == 404
    assert c.get("/api/documents/sheet/server.key").status_code == 404
    assert c.get("/api/documents/export/sheet/server.key").status_code == 404
    r = c.post("/api/documents/drawings/read", json={"rel_path": "id_rsa.pem"})
    assert r.status_code == 404, r.text


def test_the_admin_zip_skips_them(claimed, docs_dir):
    import io
    import zipfile

    r = _client(**bearer(ADMIN_TOKEN)).post(
        "/api/documents/download-zip",
        json={"rel_paths": ["shopping.txt", "id_rsa.pem", ".env"]},
    )
    assert r.status_code == 200, r.text
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert names == ["shopping.txt"]


def test_an_ordinary_document_still_reads(claimed, docs_dir):
    c = _client()
    assert c.get("/api/documents/raw/shopping.txt").status_code == 200
    assert c.get("/api/documents/text/shopping.txt").json()["text"].strip() == "milk"
    assert c.get("/api/documents/sheet/keys.csv").status_code == 200


def test_dotenv_is_not_an_editor_type_any_more():
    assert ".env" not in documents_api._TEXT_EXTS
