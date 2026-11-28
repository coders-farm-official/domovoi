"""Playing one library row in a room finds its file (2026-11-28).

The owner's house: "play in the garage" on a library song answered 404,
"MPD couldn't find track 5886 (file_path …)". Metadata enrichment had
rewritten the row's title and artist (MusicBrainz forms: U+2010 hyphens,
"feat." credits), so the tag search missed; and the basename fallback can
never hit a file named with a bracketed video id, because the filename
filter took the id out of the path but not out of the searched basename.

* :func:`filename_hit_matches` strips the video id from the searched words
  too, and folds typographic hyphens and quotes on both sides;
* the filename search retries with folded words when the raw ones miss;
* :func:`prepare_library_track` plays the row's EXACT file first, and
  searches only when the player refuses that path;
* ``_resolve_track`` (casts, queue adds) tries the exact file first too;
* the play-track route's 404 / 502 are one plain sentence: no path, no
  ids, no JSON; the dashboard shows that sentence alone, in place of its
  optimistic "playing … in garage…" toast.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import main as core_main
from domovoi.clients import mpd as mpd_mod
from domovoi.clients.mpd import (
    MPDStubClient,
    RealMPDClient,
    filename_hit_matches,
    fold_name,
    library_uri_for,
    prepare_library_track,
)
from domovoi.config import settings
from domovoi.tests.auth_testkit import _db  # noqa: F401 — fixture
from domovoi.tests.conftest import requires_db

MUSIC = Path(settings.music_dir).expanduser()
HY = "‐"  # the hyphen metadata enrichment writes

# The owner's rows (library GET, read-only): file, DB title, DB artist.
OWNER_ROWS = [
    ("Ultimate Bone Thugs/Mo'murda [OWw5i3x4Xlo].mp3", "Ghetto Cowboy", f"Bone Thugs{HY}n{HY}Harmony"),
    ("Ultimate Bone Thugs/Thuggish Ruggish Bone [iw7OINGjm5c].mp3", "Thuggish Ruggish Bone",
     f"Bone Thugs{HY}n{HY}Harmony feat. Shatasha Williams"),
    ("Ultimate Bone Thugs/Bone, Bone, Bone [4QbRVBH2eAM].mp3", "Bone, Bone, Bone", f"Bone Thugs{HY}n{HY}Harmony"),
]


# ─── The filename filter ──────────────────────────────────────────────────


@pytest.mark.parametrize("uri", [r[0] for r in OWNER_ROWS])
def test_a_basename_search_matches_its_own_file_with_a_video_id(uri: str) -> None:
    base = uri.rsplit("/", 1)[1]
    assert filename_hit_matches(uri, (base,))
    assert filename_hit_matches(uri, (uri,))
    # The old rule (the id kept in the searched word) is what never matched.
    assert re.sub(r"\[[A-Za-z0-9_-]{11}\]", " ", uri).casefold().find(base.casefold()) == -1


def test_a_word_inside_a_video_id_is_still_no_hit() -> None:
    path = "uploads/Night Ferry - Paper Comets [FQKdHGgKygo].mp3"
    assert not filename_hit_matches(path, ("kygo",))
    assert filename_hit_matches(path, ("Paper Comets [FQKdHGgKygo].mp3",))


def test_typographic_hyphens_and_quotes_fold_to_ascii() -> None:
    assert fold_name(f"Bone Thugs{HY}n‑Harmony – − ―") == "Bone Thugs-n-Harmony - - -"
    assert fold_name("Mo’murda ‘x’ “y”") == "Mo'murda 'x' \"y\""
    path = "Bone Thugs-n-Harmony/Mo'murda [OWw5i3x4Xlo].mp3"
    assert filename_hit_matches(path, (f"Bone Thugs{HY}n{HY}Harmony", "Mo’murda"))
    # And the other way round: a file named with the typographic forms.
    assert filename_hit_matches(f"Bone Thugs{HY}n{HY}Harmony/Song.mp3", ("bone thugs-n-harmony",))


# ─── The real client, against a recording connection ──────────────────────


class _AckError(Exception):
    pass


_AckError.__name__ = "CommandError"


class _Conn:
    """A room's MPD: ``library`` is its database; ``search`` is a
    case-insensitive substring match of every ``file`` term (as MPD's is),
    a tag term never matches (the file's own tags differ from the DB's)."""

    def __init__(self, library: list[str]) -> None:
        self.library = list(library)
        self.queue: list[str] = []
        self.sent: list[str] = []
        self.searches: list[tuple[str, ...]] = []

    async def search(self, *args: str) -> list[dict[str, Any]]:
        self.searches.append(args)
        pairs = list(zip(args[::2], args[1::2]))
        if any(k != "file" for k, _ in pairs):
            return []
        return [{"file": f} for f in self.library
                if all(v.casefold() in f.casefold() for _, v in pairs)]

    async def add(self, uri: str) -> None:
        if uri not in self.library:
            raise _AckError("[50@0] {add} Not found")
        self.sent.append(f"add {uri}")
        self.queue.append(uri)

    async def addid(self, uri: str, *args: Any) -> str:
        await self.add(uri)
        return str(100 + len(self.queue))

    async def clear(self) -> None:
        self.sent.append("clear")
        self.queue = []

    async def delete(self, rng: str) -> None:
        self.sent.append(f"delete {rng}")
        a, _, b = rng.partition(":")
        del self.queue[int(a):int(b)]

    async def play(self, pos: Any = None) -> None:
        self.sent.append("play" if pos is None else f"play {pos}")

    async def pause(self, state: Any) -> None:
        self.sent.append(f"pause {state}")

    async def playlistinfo(self) -> list[dict[str, Any]]:
        return [{"file": u, "Id": str(100 + i), "Pos": str(i)} for i, u in enumerate(self.queue)]


def _real(conn: _Conn) -> RealMPDClient:
    client = RealMPDClient("127.0.0.1", 1)

    @asynccontextmanager
    async def one():
        yield conn

    client._connect = one  # type: ignore[method-assign]
    return client


@pytest.mark.parametrize(("uri", "title", "artist"), OWNER_ROWS)
async def test_the_owners_rows_play_by_their_exact_file(uri: str, title: str, artist: str) -> None:
    conn = _Conn([r[0] for r in OWNER_ROWS])
    song = await prepare_library_track(_real(conn), title=title, artist=artist, file_path=str(MUSIC / uri))
    assert song is not None and song["file"] == uri
    assert conn.sent == [f"add {uri}", "play 0", "pause 1"]
    assert conn.searches == []  # no search at all: nothing could land on another song


@pytest.mark.parametrize(("uri", "title", "artist"), OWNER_ROWS)
async def test_the_owners_rows_play_through_the_search_fallback_too(uri: str, title: str, artist: str) -> None:
    """A row the exact path can't name (stored outside the music folder,
    as an older install's might be): the basename search finds it now."""
    conn = _Conn([r[0] for r in OWNER_ROWS])
    elsewhere = f"/elsewhere/{uri}"
    assert library_uri_for(elsewhere) is None
    song = await prepare_library_track(_real(conn), title=title, artist=artist, file_path=elsewhere)
    assert song is not None and song["file"] == uri
    assert conn.queue == [uri]


async def test_a_refused_exact_path_falls_back_to_the_search() -> None:
    uri = OWNER_ROWS[0][0]
    conn = _Conn([f"Moved/{uri.rsplit('/', 1)[1]}"])
    song = await prepare_library_track(
        _real(conn), title="Ghetto Cowboy", artist=OWNER_ROWS[0][2], file_path=str(MUSIC / uri))
    assert song is not None and song["file"].startswith("Moved/")


async def test_the_filename_search_retries_with_folded_hyphens() -> None:
    conn = _Conn(["Bone Thugs-n-Harmony/Crossroads.mp3"])
    song = await _real(conn).prepare_filename(f"Bone Thugs{HY}n{HY}Harmony", "Crossroads")
    assert song == {"file": "Bone Thugs-n-Harmony/Crossroads.mp3"}
    assert conn.searches[0] == ("file", f"Bone Thugs{HY}n{HY}Harmony", "file", "Crossroads")
    assert conn.searches[1] == ("file", "Bone Thugs-n-Harmony", "file", "Crossroads")


async def test_a_cast_and_a_queue_add_resolve_the_exact_file_first() -> None:
    uris = [r[0] for r in OWNER_ROWS]
    # Another file whose path holds the first one's whole path, listed first.
    conn = _Conn([f"{uris[0]} (live).mp3", *uris])
    specs = [{"title": t, "artist": a, "file_path": str(MUSIC / u)} for u, t, a in OWNER_ROWS]
    queued = await _real(conn).prepare_tracks(specs)
    assert [q["file"] for q in queued] == uris
    conn2 = _Conn(uris)
    added = await _real(conn2).queue_add_tracks(
        [{"title": "x", "artist": "y", "file_path": f"/elsewhere/{uris[1]}"}])
    assert [a["file"] for a in added] == [uris[1]]


def test_library_uri_for() -> None:
    assert library_uri_for(str(MUSIC / "A" / "b [OWw5i3x4Xlo].mp3")) == "A/b [OWw5i3x4Xlo].mp3"
    assert library_uri_for("A/b.mp3") == "A/b.mp3"  # stored relative already
    assert library_uri_for("../etc/passwd") is None
    assert library_uri_for("/elsewhere/a.mp3") is None
    assert library_uri_for("") is None


async def test_the_stub_plays_the_exact_file_and_falls_back_when_refused() -> None:
    uri = OWNER_ROWS[0][0]
    stub = MPDStubClient()
    song = await prepare_library_track(stub, title="Ghetto Cowboy", artist="x", file_path=str(MUSIC / uri))
    assert song["file"] == uri and await stub.state() == "pause"
    stub.refused_files = frozenset({uri})
    song = await prepare_library_track(stub, title="Ghetto Cowboy", artist="x", file_path=str(MUSIC / uri))
    assert song["file"].startswith("stub/")  # the stub's search answered


# ─── The route's wording ──────────────────────────────────────────────────


def _plain(detail: Any) -> None:
    assert isinstance(detail, str)
    assert "/" not in detail and "{" not in detail and "[" not in detail
    assert "MPD" not in detail and "file_path" not in detail and ".mp3" not in detail
    assert not re.search(r"\d{3,}", detail)


async def test_the_not_found_sentence_is_plain(monkeypatch) -> None:
    async def _garage(room_id: str) -> str:
        return "the garage"

    monkeypatch.setattr(core_main, "_room_spoken_name", _garage)
    detail = await core_main._track_not_found_detail("garage", "Ghetto Cowboy", str(MUSIC / OWNER_ROWS[0][0]))
    assert detail == ('The music player in the garage can\'t find "Ghetto Cowboy" yet. '
                      'Say "rescan my library", or try again in a minute.')
    _plain(detail)
    # No title: the file's name without its extension or video id.
    detail = await core_main._track_not_found_detail("garage", None, str(MUSIC / OWNER_ROWS[0][0]))
    assert '"Mo\'murda"' in detail
    _plain(detail)


ROOM = "ptl-garage"


@pytest.fixture
async def rows():
    from domovoi.db.session import engine

    async def _truncate() -> None:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE library_tracks RESTART IDENTITY CASCADE"))

    await _truncate()
    ids = []
    async with engine.begin() as conn:
        for uri, title, artist in OWNER_ROWS:
            ids.append((await conn.execute(text(
                "INSERT INTO library_tracks (file_path, title, artist) VALUES (:f, :t, :a) RETURNING id"),
                {"f": str(MUSIC / uri), "t": title, "a": artist})).scalar_one())
    yield ids
    await _truncate()


@pytest.fixture
def room(monkeypatch):
    stub = MPDStubClient()
    monkeypatch.setitem(mpd_mod._clients, ROOM, stub)

    async def _dispatch(response, room_id, *, start_paused=False):
        return None

    monkeypatch.setattr(core_main, "_admin_dispatch_music", _dispatch)
    return stub


def _core() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=core_main.app), base_url="http://test",
                       headers={"X-Requested-With": "t"})


@requires_db
async def test_play_track_plays_the_owners_rows_by_their_files(_db, rows, room) -> None:
    async with _core() as c:
        for tid, (uri, title, _a) in zip(rows, OWNER_ROWS):
            r = await c.post("/v1/admin/music/play-track",
                             json={"room_id": ROOM, "track_id": tid, "up_next": False})
            assert r.status_code == 200, r.text
            assert r.json()["title"] == title
            assert [e["file"] for e in await room.queue_list()] == [uri]


@requires_db
async def test_play_track_failures_are_one_plain_sentence(_db, rows, room, monkeypatch) -> None:
    async def _none(*a, **k):
        return None

    room.refused_files = frozenset(r[0] for r in OWNER_ROWS)
    monkeypatch.setattr(room, "prepare_search", _none)
    monkeypatch.setattr(room, "prepare_filename", _none)
    async with _core() as c:
        r = await c.post("/v1/admin/music/play-track",
                         json={"room_id": ROOM, "track_id": rows[0], "up_next": False})
        assert r.status_code == 404
        detail = r.json()["detail"]
        assert detail.startswith("The music player in the ptl garage can't find \"Ghetto Cowboy\" yet.")
        _plain(detail)

        async def _boom(*a, **k):
            raise ConnectionError("could not connect to MPD at 127.0.0.1:6653: Connection refused")

        monkeypatch.setattr(room, "prepare_files", _boom)
        r = await c.post("/v1/admin/music/play-track",
                         json={"room_id": ROOM, "track_id": rows[0], "up_next": False})
        assert r.status_code == 502
        detail = r.json()["detail"]
        assert detail == "The music player in the ptl garage didn't answer. Try again in a minute."
        _plain(detail)


# ─── The dashboard ────────────────────────────────────────────────────────


def test_the_music_page_replaces_its_optimistic_toast_with_the_plain_error() -> None:
    src = (Path(__file__).resolve().parents[2] / "web" / "static" / "music.jsx").read_text(encoding="utf-8")
    body = src[src.index("const playInRoom = async"):]
    body = body[: body.index("\n  };") + 5]
    # The optimistic toast is keyed, and every failure path reuses the key
    # (or dismisses it when the sign-in prompt speaks instead).
    assert "fire(startedMsg, { key: toastKey })" in body
    assert "fire(plain, { help: { code: apiErrorCode(e) }, key: toastKey })" in body
    assert "reportMutationFailure(fire, verb, e, { key: toastKey })" in body
    assert "dismissToast(toastKey)" in body
    assert "const [fire, toastNode, dismissToast] = useToast();" in src
    # The server's sentence as it is: no "play failed:", status or JSON.
    helper = src[src.index("const roomPlayFailureText"):]
    helper = helper[: helper.index("\n};") + 3]
    assert "e.detail && e.detail.detail" in helper and "e.message" not in helper
    assert "failed" not in helper
    # Every room start goes through it: one track, random, a list, a playlist.
    for name in ("onPlayRandom", "onPlayInRoom", "onPlayTracks", "onPlayPlaylist", "onShufflePlaylist"):
        start = src.index(f"const {name} = ")
        assert "playInRoom(" in src[start:start + 900], name
