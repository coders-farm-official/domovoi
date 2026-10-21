"""Audiobooks for the phone (2026-10-20): what a book is made of, its cover,
folder books that play, the places on other devices, and pruning.

The API contract the Android app codes against
(design-notes AUDIOBOOKS-ANDROID-BRIEF, part S):

* ``GET /api/audiobooks`` and ``/{id}`` carry ``size_bytes``,
  ``cover_url`` (null when none), ``library_path`` (the path inside the
  ``core:audiobooks`` library) and ``files`` —
  ``[{file, title, start_sec, duration_sec, size_bytes}]``, one entry for a
  single-file book, one per chapter file for a folder book;
* ``GET /{id}/cover`` reads the picture built into the book, else an image
  beside it, on request (ETag, 304, 404 when none);
* ``GET /{id}/audio?file=`` plays a folder book file by file;
* ``GET /{id}/positions`` is this listener's places on OTHER devices and
  rooms, device tier, never another person's;
* the indexer prunes a book whose file or folder is gone, with its places,
  but not every book when the folder walks empty (a share not mounted)
  unless the caller just deleted them;
* ``POST /api/files/upload`` takes ``create_dir=true`` (one new level).

Also the room side: a folder book plays in a room as its files, resumed in
the right file, with chapters as the room's queue entries, and a room that
is playing a book has its place saved every 30 s.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from domovoi import spoken_audio as sa
from domovoi.clients import mpd as mpd_module
from domovoi.clients.mpd import MPDStubClient
from domovoi.config import settings
from domovoi.tests import media_writer as mw
from domovoi.tests.conftest import requires_db

JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + b"\x00" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
XRW = {"X-Requested-With": "domovoi-tests"}


def _client():
    from web.backend.main import app

    return TestClient(app, headers=XRW)


@pytest.fixture
def clean_db():
    """The ``db_session`` truncate, for the sync tests (they drive the web
    app with TestClient, which runs its own loop)."""
    from domovoi.db.session import engine
    from domovoi.tests.conftest import TABLES_TO_TRUNCATE

    async def truncate():
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {', '.join(TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE"))

    asyncio.run(truncate())


@pytest.fixture
def books(tmp_path, monkeypatch):
    """AUDIOBOOKS_DIR in a temp folder, no ffprobe, no room players."""
    from domovoi.workers import audiobook_indexer

    root = tmp_path / "audiobooks"
    root.mkdir()
    monkeypatch.setattr(settings, "audiobooks_dir", str(root))

    async def no_ffprobe(path):
        return None, None

    monkeypatch.setattr(audiobook_indexer, "_ffprobe_chapters", no_ffprobe)
    monkeypatch.setattr(mpd_module, "_clients", {})
    return root


def _folder_book(root: Path, name: str = "Dune", *, cover: bytes | None = None) -> Path:
    book = root / name
    book.mkdir()
    mw.write_mp3(book / "01 Arrakis.mp3", [mw.TIT2("Arrakis"), mw.text_frame("TPE1", "Frank Herbert")],
                 audio=mw.silent_mp3(400))                   # 10 s
    mw.write_mp3(book / "02 Muad'Dib.mp3", [mw.TIT2("Muad'Dib")], audio=mw.silent_mp3(400))
    (book / "notes.txt").write_text("not a chapter")
    if cover is not None:
        (book / "cover.jpg").write_bytes(cover)
    return book


async def _index(**kw):
    from domovoi.workers.audiobook_indexer import index_audiobooks_dir

    return await index_audiobooks_dir(**kw)


async def _rows(db_session):
    return (await db_session.execute(text(
        "SELECT id, title, file_path, is_folder, size_bytes, files, artwork FROM audiobooks ORDER BY id"
    ))).mappings().all()


# ─── The indexer: sizes, files, where the cover is ───────────────────────


@requires_db
@pytest.mark.asyncio
async def test_a_folder_book_keeps_its_files_sizes_and_cover(db_session, books) -> None:
    book = _folder_book(books, cover=JPEG)
    counts = await _index()
    assert counts["inserted"] == 1 and counts["pruned"] == 0
    (row,) = await _rows(db_session)
    sizes = [(book / n).stat().st_size for n in ("01 Arrakis.mp3", "02 Muad'Dib.mp3")]
    assert row["size_bytes"] == sum(sizes)
    assert row["artwork"] == "image"
    assert [(f["file"], f["title"], f["start_sec"], f["size_bytes"]) for f in row["files"]] == [
        ("01 Arrakis.mp3", "Arrakis", 0, sizes[0]),
        ("02 Muad'Dib.mp3", "Muad'Dib", 10, sizes[1]),
    ]
    assert [f["duration_sec"] for f in row["files"]] == [10, 10]


@requires_db
@pytest.mark.asyncio
async def test_a_single_file_book_is_one_file_and_its_picture_counts(db_session, books) -> None:
    mw.write_mp3(books / "Emma.mp3", [mw.TIT2("Emma"),
                                      mw.APIC(mime="image/jpeg", type=3, desc="", data=JPEG)])
    mw.write_mp3(books / "Plain.mp3", [mw.TIT2("Plain")])
    # A cover.jpg loose in AUDIOBOOKS_DIR is no single-file book's cover…
    (books / "cover.jpg").write_bytes(JPEG)
    # …an image with the book's own name is.
    mw.write_mp3(books / "Named.mp3", [mw.TIT2("Named")])
    (books / "Named.png").write_bytes(PNG)
    await _index()
    rows = {r["title"]: r for r in await _rows(db_session)}
    assert rows["Emma"]["artwork"] == "embedded"
    assert rows["Plain"]["artwork"] is None
    assert rows["Named"]["artwork"] == "image"
    f = rows["Emma"]["files"]
    assert len(f) == 1 and f[0]["file"] == "Emma.mp3" and f[0]["start_sec"] == 0
    assert f[0]["size_bytes"] == rows["Emma"]["size_bytes"] == (books / "Emma.mp3").stat().st_size


@requires_db
@pytest.mark.asyncio
async def test_a_cover_taken_away_clears_artwork(db_session, books) -> None:
    book = _folder_book(books, cover=JPEG)
    await _index()
    (book / "cover.jpg").unlink()
    (book / "folder.png").write_bytes(b"<svg>not an image</svg>")
    await _index()
    (row,) = await _rows(db_session)
    assert row["artwork"] is None


# ─── Pruning ─────────────────────────────────────────────────────────────


async def _position(db_session, book_id: int, device: str, person=None, pos: int = 100) -> None:
    await sa.upsert_position(db_session, item_type=sa.ITEM_AUDIOBOOK, item_id=book_id,
                             device_id=device, person_id=person, position_sec=pos)
    await db_session.commit()


@requires_db
@pytest.mark.asyncio
async def test_a_book_gone_from_disk_is_pruned_with_its_places(db_session, books) -> None:
    import shutil

    book = _folder_book(books)
    mw.write_mp3(books / "Emma.mp3", [mw.TIT2("Emma")])
    await _index()
    ids = {r["title"]: r["id"] for r in await _rows(db_session)}
    await _position(db_session, ids["Dune"], "kitchen")
    await _position(db_session, ids["Emma"], "kitchen")
    shutil.rmtree(book)
    counts = await _index()
    assert counts["pruned"] == 1
    assert [r["title"] for r in await _rows(db_session)] == ["Emma"]
    left = (await db_session.execute(text(
        "SELECT item_id FROM playback_positions WHERE item_type = 'audiobook'"))).scalars().all()
    assert left == [ids["Emma"]]


@requires_db
@pytest.mark.asyncio
async def test_a_folder_left_without_chapters_is_pruned(db_session, books) -> None:
    book = _folder_book(books)
    mw.write_mp3(books / "Emma.mp3", [mw.TIT2("Emma")])
    await _index()
    for p in book.glob("*.mp3"):
        p.unlink()
    assert (await _index())["pruned"] == 1
    assert [r["title"] for r in await _rows(db_session)] == ["Emma"]


@requires_db
@pytest.mark.asyncio
async def test_an_empty_folder_keeps_the_list_unless_the_books_were_just_deleted(db_session, books) -> None:
    """What an unmounted share looks like: the walk finds nothing. Nothing
    is pruned then — but a delete the caller just made (the app's "Remove
    from server", the Files page) still takes its book off the list."""
    mw.write_mp3(books / "Emma.mp3", [mw.TIT2("Emma")])
    mw.write_mp3(books / "Persuasion.mp3", [mw.TIT2("Persuasion")])
    await _index()
    (books / "Emma.mp3").unlink()
    (books / "Persuasion.mp3").unlink()
    assert (await _index())["pruned"] == 0
    assert len(await _rows(db_session)) == 2
    counts = await _index(removed=[str((books / "Emma.mp3").resolve())])
    assert counts["pruned"] == 1
    assert [r["title"] for r in await _rows(db_session)] == ["Persuasion"]


@requires_db
@pytest.mark.asyncio
async def test_a_missing_folder_prunes_nothing(db_session, books, monkeypatch) -> None:
    mw.write_mp3(books / "Emma.mp3", [mw.TIT2("Emma")])
    await _index()
    monkeypatch.setattr(settings, "audiobooks_dir", str(books.parent / "unplugged"))
    assert (await _index())["pruned"] == 0
    assert len(await _rows(db_session)) == 1


# ─── The API: size, files, cover_url, library_path ───────────────────────


@requires_db
def test_the_list_and_the_book_carry_what_the_phone_needs(clean_db, books) -> None:
    _folder_book(books, cover=JPEG)
    mw.write_mp3(books / "Emma.mp3", [mw.TIT2("Emma")])
    asyncio.run(_index())
    with _client() as c:
        listed = {b["title"]: b for b in c.get("/api/audiobooks").json()}
        paged = c.get("/api/audiobooks?limit=10").json()["items"]
        one = c.get(f"/api/audiobooks/{listed['Dune']['id']}").json()
    dune, emma = listed["Dune"], listed["Emma"]
    assert one == dune
    assert {b["id"] for b in paged} == {dune["id"], emma["id"]}
    assert dune["library_path"] == "Dune" and emma["library_path"] == "Emma.mp3"
    assert dune["cover_url"] == f"/api/audiobooks/{dune['id']}/cover" == dune["artwork"]
    assert emma["cover_url"] is None and emma["artwork"] is None
    assert dune["size_bytes"] == sum(f["size_bytes"] for f in dune["files"])
    assert [f["file"] for f in dune["files"]] == ["01 Arrakis.mp3", "02 Muad'Dib.mp3"]
    assert set(dune["files"][0]) == {"file", "title", "start_sec", "duration_sec", "size_bytes"}
    assert [f["file"] for f in emma["files"]] == ["Emma.mp3"]
    for b in (dune, emma):
        assert "file_path" not in b
        assert str(books) not in repr(b)


@requires_db
def test_a_book_the_indexer_has_not_seen_since_answers_from_its_chapters(clean_db, books) -> None:
    """A row from before V032 (no ``files``): the folder book's chapters
    name its files; sizes are unknown until the next sweep."""
    folder = books / "Old"
    folder.mkdir()

    async def insert() -> int:
        from web.backend.db import session_scope

        async with session_scope() as s:
            return int((await s.execute(text(
                "INSERT INTO audiobooks (title, file_path, is_folder, duration_sec, chapters) "
                "VALUES ('Old', :fp, TRUE, 50, CAST(:ch AS JSONB)) RETURNING id"),
                {"fp": str(folder), "ch": '[{"title": "One", "start_sec": 0, "file": "1.mp3"},'
                                          ' {"title": "Two", "start_sec": 20, "file": "2.mp3"}]'},
            )).scalar_one())

    book_id = asyncio.run(insert())
    with _client() as c:
        b = c.get(f"/api/audiobooks/{book_id}").json()
    assert b["files"] == [
        {"file": "1.mp3", "title": "One", "start_sec": 0, "duration_sec": 20, "size_bytes": None},
        {"file": "2.mp3", "title": "Two", "start_sec": 20, "duration_sec": 30, "size_bytes": None},
    ]
    assert b["size_bytes"] is None and b["cover_url"] is None and b["library_path"] == "Old"


# ─── Folder books play file by file ──────────────────────────────────────


@requires_db
def test_a_folder_book_plays_file_by_file_with_ranges(clean_db, books) -> None:
    book = _folder_book(books, cover=JPEG)
    asyncio.run(_index())
    with _client() as c:
        (b,) = c.get("/api/audiobooks").json()
        whole = c.get(f"/api/audiobooks/{b['id']}/audio", params={"file": b["files"][1]["file"]})
        part = c.get(f"/api/audiobooks/{b['id']}/audio", params={"file": b["files"][0]["file"]},
                     headers={"Range": "bytes=0-9"})
        bare = c.get(f"/api/audiobooks/{b['id']}/audio")
        cover = c.get(f"/api/audiobooks/{b['id']}/audio", params={"file": "cover.jpg"})
        climb = c.get(f"/api/audiobooks/{b['id']}/audio", params={"file": "../Dune/01 Arrakis.mp3"})
    assert whole.status_code == 200 and whole.content == (book / "02 Muad'Dib.mp3").read_bytes()
    assert part.status_code == 206 and len(part.content) == 10
    assert bare.status_code == 400
    assert cover.status_code == 404           # a folder book's files are its chapters
    assert climb.status_code == 200           # bare name only: it is this book's own chapter


# ─── The cover route ─────────────────────────────────────────────────────


@requires_db
def test_the_cover_is_the_picture_built_in_then_the_image_beside_it(clean_db, books) -> None:
    folder = _folder_book(books, cover=JPEG)
    mw.write_mp3(books / "Emma.mp3", [mw.TIT2("Emma"),
                                      mw.APIC(mime="image/png", type=3, desc="", data=PNG)])
    mw.write_mp3(books / "Plain.mp3", [mw.TIT2("Plain")])
    (books / "cover.jpg").write_bytes(JPEG)   # loose in the dir: nobody's
    asyncio.run(_index())
    with _client() as c:
        ids = {b["title"]: b["id"] for b in c.get("/api/audiobooks").json()}
        dune = c.get(f"/api/audiobooks/{ids['Dune']}/cover")
        again = c.get(f"/api/audiobooks/{ids['Dune']}/cover",
                      headers={"If-None-Match": dune.headers["etag"]})
        emma = c.get(f"/api/audiobooks/{ids['Emma']}/cover")
        plain = c.get(f"/api/audiobooks/{ids['Plain']}/cover")
        unknown = c.get("/api/audiobooks/999999/cover")
        (folder / "cover.jpg").write_bytes(PNG)   # swapped: a new ETag
        swapped = c.get(f"/api/audiobooks/{ids['Dune']}/cover",
                        headers={"If-None-Match": dune.headers["etag"]})
    assert dune.status_code == 200 and dune.content == JPEG
    assert dune.headers["content-type"] == "image/jpeg" and "max-age" in dune.headers["cache-control"]
    assert again.status_code == 304
    assert emma.status_code == 200 and emma.content == PNG and emma.headers["content-type"] == "image/png"
    assert plain.status_code == 404
    assert unknown.status_code == 404
    assert swapped.status_code == 200 and swapped.content == PNG


@requires_db
def test_a_cover_link_out_of_the_library_is_not_followed(clean_db, books, tmp_path) -> None:
    folder = _folder_book(books)
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(JPEG)
    try:
        os.symlink(outside, folder / "cover.jpg")
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    asyncio.run(_index())
    with _client() as c:
        (b,) = c.get("/api/audiobooks").json()
        r = c.get(f"/api/audiobooks/{b['id']}/cover")
    assert b["cover_url"] is None
    assert r.status_code == 404


# ─── Places on other devices and rooms ───────────────────────────────────


async def _seed_places(book_id: int) -> None:
    from web.backend.db import session_scope

    async with session_scope() as s:
        await s.execute(text("INSERT INTO people (id, name) VALUES (1, 'Alex'), (2, 'Sam')"))
        await s.execute(text(
            "INSERT INTO devices (device_id, name, platform) VALUES "
            "('android-mine', 'Pixel', 'android'), ('browser-den', 'den laptop', 'browser') "
            "ON CONFLICT (device_id) DO UPDATE SET name = EXCLUDED.name"))
        # ``devices`` is not one of the tables the suite truncates.
        await s.execute(text("DELETE FROM devices WHERE device_id = 'android-unnamed'"))
        for dev, pid, pos in [
            ("android-mine", 1, 50),          # the asking phone itself
            ("kitchen", 1, 7980),             # a room, this person
            ("browser-den", 1, 300),          # a named browser, this person
            ("android-unnamed", 1, 120),      # a phone never named
            ("office", 2, 9000),              # another person's
            ("garage", None, 400),            # the unidentified listener's
            ("default", 1, 60),               # a turn with no room
            ("phone-abc", 1, 70),             # a phone's own voice slot
            ("den", 1, 0),                    # started, never past the start
        ]:
            await sa.upsert_position(s, item_type=sa.ITEM_AUDIOBOOK, item_id=book_id,
                                     device_id=dev, person_id=pid, position_sec=pos)
        # Another book's place never shows.
        await sa.upsert_position(s, item_type=sa.ITEM_AUDIOBOOK, item_id=book_id + 1,
                                 device_id="kitchen", person_id=1, position_sec=5)
        await s.execute(text(
            "UPDATE playback_positions SET updated_at = now() - interval '1 hour' "
            "WHERE device_id = 'browser-den'"))


@requires_db
def test_places_on_other_devices_are_this_persons_only(clean_db, books) -> None:
    mw.write_mp3(books / "Emma.mp3", [mw.TIT2("Emma")])
    asyncio.run(_index())
    with _client() as c:
        (b,) = c.get("/api/audiobooks").json()
        asyncio.run(_seed_places(b["id"]))
        mine = c.get(f"/api/audiobooks/{b['id']}/positions",
                     params={"device_id": "android-mine", "person_id": 1}).json()
        anon = c.get(f"/api/audiobooks/{b['id']}/positions",
                     params={"device_id": "android-mine"}).json()
        sam = c.get(f"/api/audiobooks/{b['id']}/positions",
                    params={"device_id": "android-mine", "person_id": 2}).json()
    assert [(p["device_label"], p["room"], p["position_sec"]) for p in mine] == [
        ("phone", None, 120), ("kitchen", "kitchen", 7980), ("den laptop", None, 300),
    ] or [(p["device_label"], p["room"], p["position_sec"]) for p in mine] == [
        ("kitchen", "kitchen", 7980), ("phone", None, 120), ("den laptop", None, 300),
    ]
    assert mine[-1]["device_label"] == "den laptop"     # newest first; it is an hour old
    assert all(set(p) == {"device_label", "room", "position_sec", "updated_at"} for p in mine)
    assert [(p["room"], p["position_sec"]) for p in anon] == [("garage", 400)]
    assert [(p["room"], p["position_sec"]) for p in sam] == [("office", 9000)]


@pytest.fixture(autouse=True)
def _forget_seeded_devices():
    yield
    from domovoi.db.session import engine

    async def forget():
        async with engine.begin() as conn:
            await conn.execute(text(
                "DELETE FROM devices WHERE device_id IN ('android-mine', 'browser-den')"))

    try:
        asyncio.run(forget())
    except Exception:
        pass


@requires_db
@pytest.mark.asyncio
async def test_places_are_a_device_tier_read(monkeypatch, books) -> None:
    from domovoi.tests.auth_testkit import HEADER, install_fake_db, web_client

    install_fake_db(monkeypatch, admin=True, device_token="d3v1ce-t0ken")
    async with web_client() as c:
        none = await c.get("/api/audiobooks/1/positions", params={"device_id": "android-x"})
        ok = await c.get("/api/audiobooks/1/positions", params={"device_id": "android-x"},
                         headers={HEADER: "d3v1ce-t0ken"})
        # The list, the cover and the audio stay open, as they were.
        listed = await c.get("/api/audiobooks")
    assert none.status_code == 401
    assert ok.status_code == 200 and isinstance(ok.json(), list)
    assert listed.status_code == 200


# ─── create_dir on the Files upload ──────────────────────────────────────


@pytest.fixture
def ab_library(books, monkeypatch):
    import web.backend.api.files as files_api
    from web.backend.api.files_security import MediaLibrary

    lib = MediaLibrary(
        id="core:audiobooks", label="Audiobooks", kind="core", icon="book-open",
        kind_icon="folder", owner=None, editable=True, importable=True,
        doc_editing=False, reindex_kind="audiobooks", present=True,
        root_path=books.resolve(),
    )

    async def _build():
        return [lib]

    reindexed: list[tuple] = []

    async def _index_and_tell(request, *, removed=()):
        reindexed.append(tuple(removed))
        return {"scanned": 0, "inserted": 0, "errors": 0, "pruned": 0}

    import web.backend.api.audiobooks as ab_api

    monkeypatch.setattr(files_api, "build_libraries", _build)
    monkeypatch.setattr(ab_api, "index_and_tell_players", _index_and_tell)
    return reindexed


def _upload(c, path: str, *, create_dir: bool | None = True, names=("01.mp3", "02.mp3")):
    data = {"library_id": "core:audiobooks", "path": path, "device_id": "android-test"}
    if create_dir is not None:
        data["create_dir"] = "true" if create_dir else "false"
    return c.post("/api/files/upload", data=data,
                  files=[("files", (n, mw.silent_mp3(), "audio/mpeg")) for n in names])


@requires_db
def test_create_dir_makes_the_one_folder_a_book_goes_in(clean_db, books, ab_library) -> None:
    with _client() as c:
        made = _upload(c, "Dune")
        again = _upload(c, "Dune", names=("03.mp3",))
        plain = _upload(c, "Emma", create_dir=None)
    assert made.status_code == 200, made.text
    assert made.json()["created_dir"] is True and made.json()["saved"] == ["01.mp3", "02.mp3"]
    assert again.status_code == 200 and again.json()["created_dir"] is False
    assert sorted(p.name for p in (books / "Dune").iterdir()) == ["01.mp3", "02.mp3", "03.mp3"]
    assert plain.status_code == 404                     # without the flag, as before
    assert not (books / "Emma").exists()
    assert len(ab_library) == 2                          # each upload reindexed


@requires_db
def test_create_dir_is_one_level_under_an_existing_folder(clean_db, books, ab_library) -> None:
    (books / "Series").mkdir()
    with _client() as c:
        nested = _upload(c, "Series/Book One")
        deep = _upload(c, "Missing/Book")
    assert nested.status_code == 200 and (books / "Series" / "Book One" / "01.mp3").exists()
    assert deep.status_code == 404 and not (books / "Missing").exists()


@pytest.mark.parametrize("name", [
    "..", ".", "CON", "nul.txt", "Com1", "lpt9.mp3", ".ssh", "a\x01b", "trailing.", " lead",
    "what?", "a:b",
])
@requires_db
def test_create_dir_refuses_names_that_are_not_a_folder(clean_db, books, ab_library, name) -> None:
    before = sorted(p.name for p in books.iterdir())
    with _client() as c:
        r = _upload(c, name)
    assert r.status_code == 400, (name, r.text)
    assert sorted(p.name for p in books.iterdir()) == before
    assert ab_library == []


@requires_db
def test_create_dir_refuses_a_climb_out_of_the_library(clean_db, books, ab_library, tmp_path) -> None:
    with _client() as c:
        up = _upload(c, "../escaped")
        drive = _upload(c, "C:/escaped")
    assert up.status_code == 400 and drive.status_code == 400
    assert not (tmp_path / "escaped").exists()


@requires_db
def test_create_dir_refuses_a_link_out_of_the_library(clean_db, books, ab_library, tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, books / "Linked", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    with _client() as c:
        r = _upload(c, "Linked")
    assert r.status_code == 400
    assert list(outside.iterdir()) == []


@requires_db
def test_a_folder_made_for_nothing_is_taken_away_again(clean_db, books, ab_library) -> None:
    with _client() as c:
        r = _upload(c, "Dune", names=(".env",))
    assert r.status_code == 400
    assert not (books / "Dune").exists()


@requires_db
def test_a_delete_names_what_it_removed_for_the_prune(clean_db, books, ab_library) -> None:
    from domovoi.tests.auth_testkit import install_fake_db  # noqa: F401 — pre-setup grace below

    _folder_book(books)
    with _client() as c:
        r = c.post("/api/files/delete", json={
            "library_id": "core:audiobooks", "paths": ["Dune"], "recursive": True})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == ["Dune"]
    assert ab_library == [(str((books / "Dune").resolve()),)]


# ─── Rooms: a folder book plays as its files ─────────────────────────────


async def _folder_book_row(db_session, books) -> int:
    _folder_book(books)
    await _index()
    return int((await _rows(db_session))[0]["id"])


def _handler_with_memory():
    from domovoi.handlers.spoken_audio import SpokenAudioHandler

    h = SpokenAudioHandler()
    memory: dict = {}

    async def remember(ctx, session, item):
        memory["now"] = item

    async def current(ctx, session):
        return memory.get("now")

    h._remember = remember  # type: ignore[method-assign]
    h._current = current    # type: ignore[method-assign]
    return h, memory


@requires_db
@pytest.mark.asyncio
async def test_a_folder_book_resumes_in_the_right_file_in_a_room(db_session, books) -> None:
    from domovoi.handlers.spoken_audio import _RESUME_RE
    from domovoi.models import Context

    book_id = await _folder_book_row(db_session, books)
    stub = MPDStubClient()
    mpd_module._clients = {"kitchen": stub}
    await _position(db_session, book_id, "kitchen", pos=13)   # 3 s into chapter two
    h, memory = _handler_with_memory()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True, person_id=None)
    resp = await h._resume_from_match(_RESUME_RE.match("resume my book"), ctx, db_session)
    await db_session.commit()
    assert resp.music_action == "start", resp.text
    assert [e["file"] for e in await stub.queue_list()] == [
        "audiobooks/Dune/01 Arrakis.mp3", "audiobooks/Dune/02 Muad'Dib.mp3"]
    assert stub._song["file"] == "audiobooks/Dune/02 Muad'Dib.mp3"
    assert stub._song["_elapsed"] == 3.0
    assert memory["now"]["starts"] == [0, 10]


@requires_db
@pytest.mark.asyncio
async def test_chapters_of_a_folder_book_are_the_rooms_queue(db_session, books) -> None:
    from domovoi.handlers.spoken_audio import _PLAY_BOOK_RE
    from domovoi.models import Context

    book_id = await _folder_book_row(db_session, books)
    stub = MPDStubClient()
    mpd_module._clients = {"kitchen": stub}
    h, _ = _handler_with_memory()
    ctx = Context(session_id=uuid4(), room_id="kitchen", online=True, person_id=None)
    await h._play_book_from_match(_PLAY_BOOK_RE.match("play the audiobook dune"), ctx, db_session)
    await stub.resume()
    assert stub._song["file"].endswith("01 Arrakis.mp3")
    nxt = await h._chapter_nav(ctx, db_session, +1)
    await db_session.commit()
    assert nxt.text == "Muad'Dib."
    assert stub._song["file"].endswith("02 Muad'Dib.mp3")
    pos = await sa.get_position(db_session, item_type=sa.ITEM_AUDIOBOOK, item_id=book_id,
                                device_id="kitchen", person_id=None)
    assert pos["position_sec"] == 10
    # A skip inside chapter two is saved as a place in the BOOK.
    await stub.seek_to(4.0)
    await h._skip(ctx, db_session, 30)
    await db_session.commit()
    pos = await sa.get_position(db_session, item_type=sa.ITEM_AUDIOBOOK, item_id=book_id,
                                device_id="kitchen", person_id=None)
    assert pos["position_sec"] == 44
    back = await h._chapter_nav(ctx, db_session, -1)
    assert back.text == "Arrakis." and stub._song["file"].endswith("01 Arrakis.mp3")


@requires_db
@pytest.mark.asyncio
async def test_a_playing_room_has_its_place_saved(db_session, books) -> None:
    from domovoi.workers.room_book_positions import save_room_book_positions

    book_id = await _folder_book_row(db_session, books)
    mw.write_mp3(books / "Emma.mp3", [mw.TIT2("Emma")])
    await _index()
    emma_id = next(r["id"] for r in await _rows(db_session) if r["title"] == "Emma")
    await _position(db_session, book_id, "kitchen", person=None, pos=0)
    await _position(db_session, emma_id, "den", person=None, pos=0)

    kitchen, den, office, idle = MPDStubClient(), MPDStubClient(), MPDStubClient(), MPDStubClient()
    await kitchen.prepare_files(["audiobooks/Dune/01 Arrakis.mp3", "audiobooks/Dune/02 Muad'Dib.mp3"],
                                start_index=1, start_sec=7)
    await kitchen.resume()
    await den.prepare_files(["audiobooks/Emma.mp3"], start_sec=42)
    await den.resume()
    await office.prepare_files(["audiobooks/Emma.mp3"], start_sec=99)   # nobody started it by voice
    await office.resume()
    await idle.prepare_files(["audiobooks/Emma.mp3"], start_sec=5)      # paused: left alone
    n = await save_room_book_positions(
        [("kitchen", kitchen), ("den", den), ("office", office), ("garage", idle)])
    assert n == 2
    places = dict((await db_session.execute(text(
        "SELECT device_id, position_sec FROM playback_positions"))).all())
    assert places == {"kitchen": 17, "den": 42}


# ─── The MPD client: start in the middle of the list ─────────────────────


@pytest.mark.asyncio
async def test_the_stub_starts_on_the_asked_file() -> None:
    stub = MPDStubClient()
    q = await stub.prepare_files(["a.mp3", "", "b.mp3", "c.mp3"], start_index=2, start_sec=5)
    assert [e["file"] for e in q] == ["a.mp3", "b.mp3", "c.mp3"]
    assert stub._song["file"] == "b.mp3" and stub._song["_elapsed"] == 5.0


@pytest.mark.asyncio
async def test_the_real_client_starts_on_the_asked_file() -> None:
    from domovoi.tests.test_library_match import _real, _RecordingMPD

    conn = _RecordingMPD(queue=["old.mp3"])
    await _real(conn).prepare_files(["01.mp3", "02.mp3", "03.mp3"], start_index=1, start_sec=30.5)
    assert conn.sent == ["add 01.mp3", "add 02.mp3", "add 03.mp3", "delete 0:1", "seek 1 30", "pause 1"]


@pytest.mark.asyncio
async def test_a_refused_start_file_starts_the_next_one_from_its_top() -> None:
    from domovoi.tests.test_library_match import _real, _RecordingMPD

    conn = _RecordingMPD(refuse={"02.mp3"})
    await _real(conn).prepare_files(["01.mp3", "02.mp3", "03.mp3"], start_index=1, start_sec=30)
    assert conn.sent == ["add 01.mp3", "add 03.mp3", "play 1", "pause 1"]
    conn = _RecordingMPD(refuse={"03.mp3"})
    await _real(conn).prepare_files(["01.mp3", "02.mp3", "03.mp3"], start_index=2, start_sec=30)
    assert conn.sent == ["add 01.mp3", "add 02.mp3", "play 1", "pause 1"]
