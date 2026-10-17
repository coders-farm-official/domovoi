"""F-V023 (rc2 smoke): a newly added audiobook was indexed but wouldn't
play until "rescan my library", because the room's MPD learns of new files
only by watching its folders, which a Windows (Docker Desktop) or network
folder never reports. Every indexer that adds files now asks each room's
MPD to read the folders they are in (domovoi/mpd_library_sync.py): the
music library, audiobooks and podcasts alike.

The mapping and the folder list are pure; the fan-out runs on the stub
rooms (``USE_STUBS``: ``iter_mpd_clients`` lists the seeded stubs); the
indexers run on the lane's database; the real client's ``update`` lines
are checked against test_mpd_protocol's fake daemon.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi import mpd_library_sync as sync
from domovoi.clients import mpd as mpd_module
from domovoi.clients.mpd import MPDStubClient
from domovoi.config import settings
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_mpd_protocol import FakeMPD


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    music, books, pods = tmp_path / "music", tmp_path / "books", tmp_path / "pods"
    for d in (music, books, pods):
        d.mkdir()
    monkeypatch.setattr(settings, "music_dir", str(music))
    monkeypatch.setattr(settings, "audiobooks_dir", str(books))
    monkeypatch.setattr(settings, "podcasts_dir", str(pods))
    return music, books, pods


class _DownRoom(MPDStubClient):
    async def update_paths(self, uris):
        raise ConnectionRefusedError("the room's player is down")


@pytest.fixture
def rooms():
    mpd_module._clients.clear()
    kitchen, down, den = MPDStubClient(), _DownRoom(), MPDStubClient()
    mpd_module._clients.update({"kitchen": kitchen, "attic": down, "den": den})
    yield kitchen, den
    mpd_module._clients.clear()


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


# ─── Which folder MPD must read ────────────────────────────────────────────


def test_each_library_maps_to_its_folder_in_mpd(dirs, tmp_path) -> None:
    music, books, pods = dirs
    assert sync.mpd_folder_for(_touch(music / "Artist" / "Album" / "01 Song.mp3")) == ("", "Artist/Album")
    assert sync.mpd_folder_for(_touch(music / "Loose Song.mp3")) == ("", "")
    # A single-file book: the audiobooks folder; a book of chapter files: itself.
    assert sync.mpd_folder_for(_touch(books / "The Hearth Spirit.m4b")) == ("audiobooks", "audiobooks")
    _touch(books / "Chaptered Book" / "01.mp3")
    assert sync.mpd_folder_for(books / "Chaptered Book") == ("audiobooks", "audiobooks/Chaptered Book")
    assert sync.mpd_folder_for(_touch(pods / "show" / "ep1.mp3")) == ("podcasts", "podcasts/show")
    assert sync.mpd_folder_for(_touch(tmp_path / "elsewhere" / "x.mp3")) is None


def test_a_spoken_audio_folder_inside_the_music_folder_maps_as_mpd_plays_it(dirs, monkeypatch) -> None:
    music, _, _ = dirs
    monkeypatch.setattr(settings, "audiobooks_dir", str(music / "Audiobooks"))
    book = _touch(music / "Audiobooks" / "Book.m4b")
    assert sync.mpd_folder_for(book) == ("audiobooks", "audiobooks")


def test_each_folder_once_nested_ones_dropped_and_many_collapse(dirs) -> None:
    music, books, pods = dirs
    album = [_touch(music / "A" / "Album" / f"{i:02}.mp3") for i in range(1, 4)]
    assert sync.folders_to_update(album) == ["A/Album"]
    chaptered = books / "Chaptered"
    _touch(chaptered / "01.mp3")
    single = _touch(books / "Single.m4b")
    assert sync.folders_to_update([chaptered, single]) == ["audiobooks"]
    assert sync.folders_to_update([chaptered]) == ["audiobooks/Chaptered"]
    assert sync.folders_to_update(tops=["podcasts"]) == ["podcasts"]
    assert sync.folders_to_update([_touch(music / "Loose.mp3"), single]) == [""]
    # More folders than MAX_FOLDERS: their library folders instead.
    many_albums = [_touch(music / f"Artist {i}" / "Album" / "01.mp3") for i in range(sync.MAX_FOLDERS + 1)]
    assert sync.folders_to_update(many_albums) == [""]
    many_books = []
    for i in range(sync.MAX_FOLDERS + 1):
        _touch(books / f"Book {i}" / "01.mp3")
        many_books.append(books / f"Book {i}")
    assert sync.folders_to_update(many_books + [_touch(pods / "s" / "e.mp3")]) == ["audiobooks", "podcasts"]
    assert sync.folders_to_update([]) == []


# ─── Every room is asked; one that is down doesn't stop the rest ───────────


@pytest.mark.asyncio
async def test_every_room_is_asked_and_a_down_one_is_skipped(dirs, rooms) -> None:
    music, books, _ = dirs
    kitchen, den = rooms
    asked = await sync.update_rooms_for([_touch(music / "A" / "B" / "1.mp3"), _touch(books / "Book.m4b")])
    assert asked == 2
    assert kitchen.updated_paths == den.updated_paths == ["A/B", "audiobooks"]
    assert await sync.update_rooms_for([]) == 0                     # nothing new: nothing asked
    assert kitchen.updated_paths == ["A/B", "audiobooks"]


@pytest.mark.asyncio
async def test_no_rooms_is_a_no_op(dirs) -> None:
    """The web process (and a house with no room yet) has no players."""
    mpd_module._clients.clear()
    music, _, _ = dirs
    assert await sync.update_rooms_for([_touch(music / "A" / "1.mp3")]) == 0


# ─── The real client's protocol lines ─────────────────────────────────────


@pytest.mark.asyncio
async def test_the_real_client_sends_one_update_per_folder_on_one_connection() -> None:
    def reply(line: str) -> str:
        if "gone" in line:
            return "ACK [50@0] {update} Malformed path"
        return "updating_db: 7\n"

    async with FakeMPD({"update": reply}) as fake:
        client = mpd_module.RealMPDClient("127.0.0.1", fake.port)
        await client.update_paths(["Artist/Album", "gone", "audiobooks", ""])
    assert fake.lines == ['update "Artist/Album"', 'update "gone"', 'update "audiobooks"', "update"]
    assert fake.connections == 1


# ─── The indexers ask, after adding (and only then) ───────────────────────


@requires_db
@pytest.mark.asyncio
async def test_the_audiobook_indexer_asks_for_the_books_it_adds(db_session, dirs, rooms) -> None:
    """The F-V023 repro: a book added after the room's MPD started."""
    from domovoi.workers.audiobook_indexer import index_audiobooks_dir

    _, books, _ = dirs
    kitchen, den = rooms
    _touch(books / "The Hearth Spirit" / "01 Chapter One.mp3")
    _touch(books / "The Hearth Spirit" / "02 Chapter Two.mp3")
    counts = await index_audiobooks_dir()
    assert counts["inserted"] == 1
    assert kitchen.updated_paths == den.updated_paths == ["audiobooks/The Hearth Spirit"]
    # Nothing new on the next pass (the worker's 300 s tick): nothing asked.
    assert (await index_audiobooks_dir())["inserted"] == 0
    assert kitchen.updated_paths == ["audiobooks/The Hearth Spirit"]
    _touch(books / "Second Book.m4b")
    await index_audiobooks_dir()
    assert kitchen.updated_paths == ["audiobooks/The Hearth Spirit", "audiobooks"]
    n = (await db_session.execute(text("SELECT count(*) FROM audiobooks"))).scalar_one()
    assert n == 2


@requires_db
@pytest.mark.asyncio
async def test_the_library_indexer_asks_for_the_folders_it_adds(db_session, dirs, rooms) -> None:
    from domovoi.workers.library_indexer import index_music_dir, index_paths

    music, _, _ = dirs
    kitchen, _ = rooms
    _touch(music / "Hearth Cats" / "Purrs" / "01 Kindling.mp3")
    _touch(music / "Hearth Cats" / "Purrs" / "02 Embers.mp3")
    assert (await index_music_dir())["inserted"] == 2
    assert kitchen.updated_paths == ["Hearth Cats/Purrs"]
    assert (await index_music_dir())["inserted"] == 0
    assert kitchen.updated_paths == ["Hearth Cats/Purrs"]
    # The bounded post-write variant does the same.
    new = _touch(music / "Test Tones" / "A440.flac")
    assert (await index_paths([new]))["inserted"] == 1
    assert kitchen.updated_paths == ["Hearth Cats/Purrs", "Test Tones"]


@requires_db
@pytest.mark.asyncio
async def test_the_podcast_poller_asks_after_downloading(db_session, dirs, rooms, monkeypatch) -> None:
    from domovoi.workers import podcast_feed_poller as poller

    kitchen, _ = rooms
    sub = (await db_session.execute(text(
        "INSERT INTO podcast_subscriptions (feed_url, title, keep_n) "
        "VALUES ('https://feeds.example.com/s.xml', 'S', 5) RETURNING id"
    ))).scalar_one()
    await db_session.execute(text(
        "INSERT INTO podcast_episodes (subscription_id, guid, title, enclosure_url, download_status) "
        "VALUES (:s, 'g1', 'e1', 'https://feeds.example.com/e.mp3', 'pending')"
    ), {"s": sub})
    await db_session.commit()

    async def no_poll(session, sub):
        return 0

    outcome = {"ok": None}

    async def download(session, ep):
        return outcome["ok"]

    monkeypatch.setattr(poller, "poll_subscription", no_poll)
    monkeypatch.setattr(poller, "download_episode", download)
    outcome["ok"] = None                    # nothing downloaded: nothing asked
    await poller.PodcastFeedPoller().tick()
    assert kitchen.updated_paths == []
    outcome["ok"] = True
    assert (await poller.PodcastFeedPoller().tick())["downloaded"] == 1
    assert kitchen.updated_paths == ["podcasts"]


# ─── The web process indexes books too, and asks the core ─────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(("inserted", "hops"), [(1, 1), (0, 0)])
async def test_a_book_the_web_adds_has_the_core_tell_the_players(monkeypatch, inserted, hops) -> None:
    """The dashboard's audiobook reindex and a Files upload into the
    audiobooks folder index in the web process, which has no rooms: when a
    book was added it asks the core's device-tier MPD rescan (the one the
    music upload uses), with the caller's credentials."""
    from domovoi.workers import audiobook_indexer
    from web.backend.api import audiobooks as ab
    from web.backend.api import files

    async def fake_index():
        return {"scanned": 2, "inserted": inserted, "errors": 0}

    posted = []

    async def fake_post(path, body=None, timeout=30.0, headers=None):
        posted.append((path, headers))
        return 200, {"rooms": 2, "updated": 2}

    monkeypatch.setattr(audiobook_indexer, "index_audiobooks_dir", fake_index)
    monkeypatch.setattr(ab, "post_admin", fake_post)
    monkeypatch.setattr(ab, "auth_forward_headers", lambda request: {"X-Device-Token": "tok"})

    counts = await ab.index_and_tell_players(object())
    assert counts["inserted"] == inserted
    assert posted == [("/v1/admin/music/mpd-rescan", {"X-Device-Token": "tok"})] * hops
    posted.clear()
    assert await files._trigger_reindex("audiobooks", object()) is True
    assert posted == [("/v1/admin/music/mpd-rescan", {"X-Device-Token": "tok"})] * hops
