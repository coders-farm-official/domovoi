"""The secrets people actually keep in a home folder stay out of both doors
into it (REV-12, 2026-10-08).

``/api/files`` and ``/api/documents`` serve the operator's real
``~/Documents`` to any paired device, and both withhold a secret-shaped
name through one shared filter (``files_security.is_sensitive_name``). That
filter knew Domovoi's own secrets and the PEM family, and missed the most
common real ones: SSH private keys under their default names and the
``.ssh`` folder, dotenv variants (``.env.local``), ``.netrc``, password
databases (``*.kdbx``), encrypted files (``*.gpg``). They were served as an
attachment through both doors.

And the documents door asks about the path as sent AND as resolved — but
no test held it to the resolved half, so a link such as
``notes.txt -> .env`` would have served the ``.env`` the moment that half
was dropped. The Files door asked only about the last segment, so a
withheld folder (``.ssh``) could still be read through
(``.ssh/config``). Both are pinned here.

DB-free: the auth primitives are faked; the folder is a tmp dir.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import web.backend.api.files as files_api
from domovoi.config import settings
from domovoi.tests.auth_testkit import HEADER, install_fake_db
from web.backend.api import files_security as fs
from web.backend.api.files_security import MediaLibrary
from web.backend.main import app

DEVICE_TOKEN = "secret-names-household-token"

# The names the shared filter now withholds (beside the ones it already did).
NEW_SECRETS = [
    "id_rsa",
    "id_rsa.pub",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_ed25519_sk",
    "ID_RSA",
    ".env.local",
    ".env.production",
    ".netrc",
    ".git-credentials",
    ".pgpass",
    "credentials.json",
    "secrets.json",
    "passwords.kdbx",
    "tax-2025.pdf.gpg",
    "home.ovpn",
]
NEW_SECRET_FOLDERS = [".ssh", ".gnupg", ".aws", ".kube"]

# Ordinary documents that merely look a little like the above.
ORDINARY = [
    "notes.txt",
    "identity.md",
    "id_card.jpg",
    "ssh-notes.md",
    "envelope.txt",
    "my.env.txt",
    "credentials.md",
    "gpg-howto.md",
    "keys.csv",
]


@pytest.mark.parametrize("name", NEW_SECRETS + NEW_SECRET_FOLDERS)
def test_the_shared_filter_withholds_it(name) -> None:
    assert fs.is_sensitive_name(name) is True


@pytest.mark.parametrize("name", ORDINARY)
def test_an_ordinary_document_is_not_mistaken_for_one(name) -> None:
    assert fs.is_sensitive_name(name) is False


def test_a_folder_on_the_list_withholds_what_is_inside_it() -> None:
    assert fs.has_sensitive_segment(".ssh/config") is True
    assert fs.has_sensitive_segment("backup/.gnupg/pubring.kbx") is True
    assert fs.has_sensitive_segment("letters/2026/notes.txt") is False


# ─── the folder, both doors ─────────────────────────────────────────────


@pytest.fixture
def household(monkeypatch):
    return install_fake_db(monkeypatch, admin=True, device_token=DEVICE_TOKEN)


@pytest.fixture
def docs_dir(monkeypatch, tmp_path):
    docs = (tmp_path / "Documents").resolve()
    docs.mkdir()
    monkeypatch.setattr(settings, "documents_dir", str(docs))
    (docs / "shopping.txt").write_text("milk\n", encoding="utf-8")
    for name in NEW_SECRETS:
        (docs / name).write_text(f"SECRET {name}\n", encoding="utf-8")
    for folder in NEW_SECRET_FOLDERS:
        (docs / folder).mkdir()
        (docs / folder / "config").write_text(f"SECRET {folder}/config\n", encoding="utf-8")

    library = MediaLibrary(
        id="core:documents", label="Documents", kind="core", icon="file-text",
        kind_icon="folder", owner=None, editable=True, importable=True,
        doc_editing=True, reindex_kind="documents", present=True, root_path=docs,
    )

    async def _build():
        return [library]

    monkeypatch.setattr(files_api, "build_libraries", _build)
    return docs


def _client() -> TestClient:
    return TestClient(
        app, headers={"X-Requested-With": "domovoi-tests", HEADER: DEVICE_TOKEN}
    )


def _files_download(c: TestClient, path: str):
    return c.get(
        "/api/files/download", params={"library_id": "core:documents", "path": path}
    )


@pytest.mark.parametrize("name", NEW_SECRETS)
def test_the_documents_door_reads_it_as_missing(household, docs_dir, name) -> None:
    with _client() as c:
        for route in ("/api/documents/raw/{}", "/api/documents/text/{}"):
            r = c.get(route.format(name))
            assert r.status_code == 404, (route, r.text)
            assert "SECRET" not in r.text


@pytest.mark.parametrize("name", NEW_SECRETS)
def test_the_files_door_reads_it_as_missing(household, docs_dir, name) -> None:
    with _client() as c:
        r = _files_download(c, name)
    assert r.status_code == 404, r.text
    assert "SECRET" not in r.text


@pytest.mark.parametrize("folder", NEW_SECRET_FOLDERS)
def test_neither_door_reads_through_a_withheld_folder(household, docs_dir, folder) -> None:
    with _client() as c:
        assert c.get(f"/api/documents/raw/{folder}/config").status_code == 404
        r = _files_download(c, f"{folder}/config")
        assert r.status_code == 404, r.text
        assert "SECRET" not in r.text
        r = c.get(
            "/api/files/browse", params={"library_id": "core:documents", "path": folder}
        )
        assert r.status_code == 404, r.text


def test_neither_listing_names_them(household, docs_dir) -> None:
    hidden = set(NEW_SECRETS) | set(NEW_SECRET_FOLDERS)
    with _client() as c:
        r = c.get("/api/documents", params={"kind": "all"})
        assert r.status_code == 200, r.text
        listed = {row["rel_path"].split("/")[0] for row in r.json()}
        assert "shopping.txt" in listed
        assert not listed & hidden, listed & hidden
        r = c.get("/api/files/browse", params={"library_id": "core:documents", "path": ""})
        assert r.status_code == 200, r.text
        names = {e["name"] for e in r.json()["entries"]}
        assert "shopping.txt" in names
        assert not names & hidden, names & hidden


def test_an_ordinary_document_still_reads_through_both_doors(household, docs_dir) -> None:
    with _client() as c:
        assert c.get("/api/documents/raw/shopping.txt").status_code == 200
        assert _files_download(c, "shopping.txt").status_code == 200


# ─── a link is judged by where it lands ─────────────────────────────────


def _symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        os.symlink(target, link, target_is_directory=directory)
        return
    except (OSError, NotImplementedError) as e:  # Windows without the privilege
        reason = e
    if directory and os.name == "nt":
        # A directory junction needs no privilege, and resolve() follows it
        # the way it follows a symlink — so the folder case runs here too.
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
            return
        except (OSError, ImportError, AttributeError) as e:
            reason = e
    pytest.skip(f"this host cannot create links: {reason}")


def test_the_documents_door_judges_the_resolved_path(household, docs_dir, monkeypatch) -> None:
    """The same promise as the link tests below, held on every host: when a
    harmless name RESOLVES onto a secret, the read answers 404. Without the
    resolved half of ``_withheld`` this serves the ``.env``."""
    from web.backend.api import documents as documents_api

    (docs_dir / ".env").write_text("SECRET dotenv", encoding="utf-8")
    real = documents_api._safe_target

    def resolves_onto_dotenv(rel_path: str) -> Path:
        if rel_path == "notes.txt":
            return docs_dir / ".env"
        return real(rel_path)

    monkeypatch.setattr(documents_api, "_safe_target", resolves_onto_dotenv)
    with _client() as c:
        for route in ("/api/documents/raw/notes.txt", "/api/documents/text/notes.txt"):
            r = c.get(route)
            assert r.status_code == 404, (route, r.text)
            assert "SECRET" not in r.text


def test_a_harmless_name_linked_to_a_dotenv_reads_as_missing(household, docs_dir) -> None:
    """The documents door's resolved-path half, which no test held: drop it
    and ``notes.txt -> .env`` serves the ``.env``."""
    (docs_dir / ".env").write_text("SECRET dotenv\n", encoding="utf-8")
    _symlink(docs_dir / "notes.txt", docs_dir / ".env")
    with _client() as c:
        for route in ("/api/documents/raw/notes.txt", "/api/documents/text/notes.txt"):
            r = c.get(route)
            assert r.status_code == 404, (route, r.text)
            assert "SECRET" not in r.text
        r = _files_download(c, "notes.txt")
        assert r.status_code == 404, r.text
        assert "SECRET" not in r.text


def test_a_harmless_folder_linked_to_dot_ssh_reads_as_missing(household, docs_dir) -> None:
    _symlink(docs_dir / "keys", docs_dir / ".ssh", directory=True)
    with _client() as c:
        assert c.get("/api/documents/raw/keys/config").status_code == 404
        r = _files_download(c, "keys/config")
        assert r.status_code == 404, r.text
        assert "SECRET" not in r.text
