"""The Music search and the Artists view (owner, 2026-11-11).

``GET /api/music/library?q=`` was one ILIKE substring: "em know" missed
"Let Em' Know", "ti" missed T.I. It now matches every word, punctuation-,
accent- and case-blind, across title / artist / album (V046's
``search_text``), best match first, with a typo fallback. ``GET
/api/music/artists`` lists performers (a "Jeezy, T.I." credit counts for
T.I.), ``GET /api/music/artist`` is one of them, and
``GET /api/music/library?artist=`` their tracks.

The pure half needs nothing; the rest needs the database (V046). Rows
carry a per-run marker word, every query names it, and they are removed
afterwards.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from web.backend.api import music_search
from web.backend.api.music_artists import performers_of

RUN = uuid.uuid4().hex[:8]
M = f"zq{RUN}"   # the marker word every row's album carries


# ─── Pure ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw, want", [
    ("Let E.M. Know", "let em know"),
    ("Let Em' Know", "let em know"),
    ("T.I.", "ti"),
    ("Jay-Z", "jayz"),
    ("Beyoncé", "beyonce"),
    ("  ÉLAN / Łódź  ", "elan lodz"),
    ("Don’t Stop", "dont stop"),
    (None, ""),
])
def test_search_norm(raw, want) -> None:
    assert music_search.search_norm(raw) == want


def test_tokens_are_unique_words_capped() -> None:
    assert music_search.search_tokens("Em em KNOW") == ["em", "know"]
    assert len(music_search.search_tokens(" ".join(f"w{i}" for i in range(20)))) == music_search.MAX_TOKENS


def test_text_rank_orders_whole_words_before_word_starts() -> None:
    assert music_search.text_rank("ti", ["ti"]) > music_search.text_rank("bryson tiller", ["ti"])
    assert music_search.text_rank("bryson tiller", ["ti"]) > music_search.text_rank("platinum", ["ti"])
    assert music_search.text_rank("bryson tiller", ["xx"]) is None


def test_performers_of() -> None:
    known = {"ti"}
    assert performers_of("Jeezy, T.I.", known) == ["Jeezy", "T.I."]
    assert performers_of("Earth, Wind & Fire", known) == ["Earth, Wind & Fire"]
    assert performers_of("Bryson Tiller feat. Drake", known) == ["Bryson Tiller", "Drake"]
    assert performers_of("Jeezy x T.I.", set()) == ["Jeezy", "T.I."]


# ─── The database ─────────────────────────────────────────────────────────

ROWS = [
    # (title, artist, album)
    ("Let Em' Know", "Bryson Tiller", f"Night {M}"),
    ("Exchange", "Bryson Tiller", f"Night {M}"),
    ("Whatever You Like", "T.I.", f"Paper {M}"),
    ("Live Your Life", "T.I.", f"Paper {M}"),
    ("Go Crazy", "Jeezy, T.I.", f"Late {M}"),
    ("Platinum Ti", "Sleepers", f"Odds {M}"),
    ("Know", "Someone Else", f"Odds {M}"),
    ("Boogie Wonderland", "Earth, Wind & Fire", f"Gold {M}"),
    ("Déjà Vu", "Beyoncé", f"Dé {M}"),
    ("Single", "T.I.", None),
]


async def _insert() -> None:
    from web.backend.db import session_scope

    async with session_scope() as s:
        for i, (title, artist, album) in enumerate(ROWS):
            await s.execute(
                text(
                    "INSERT INTO library_tracks (id, file_path, title, artist, album, added_via) "
                    "VALUES ((SELECT COALESCE(MAX(id), 0) + 1 FROM library_tracks), "
                    ":fp, :title, :artist, :album, 'manual')"
                ),
                {"fp": f"/srv/music/{M}/{i:02d}.mp3", "title": title,
                 "artist": artist, "album": album},
            )
        # The album-less row is found by its marker in the path only:
        # give it the marker in its title too so the queries can name it.
        await s.execute(
            text("UPDATE library_tracks SET title = :t WHERE file_path = :fp"),
            {"t": f"Single {M}", "fp": f"/srv/music/{M}/09.mp3"},
        )


async def _remove() -> None:
    from web.backend.db import session_scope

    async with session_scope() as s:
        await s.execute(text("DELETE FROM library_tracks WHERE file_path LIKE :p"),
                        {"p": f"/srv/music/{M}/%"})


@pytest.fixture
def client():
    from web.backend.main import app

    asyncio.run(_insert())
    try:
        with TestClient(app) as c:
            yield c
    finally:
        asyncio.run(_remove())


def _search(client, q: str, **extra) -> dict:
    r = client.get("/api/music/library", params={"q": q, "limit": 50, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _titles(page: dict) -> list[str]:
    return [t["title"] for t in page["items"]]


@requires_db
def test_the_generated_column_agrees_with_python(client) -> None:
    async def norm(values: list[str]) -> list[str]:
        from web.backend.db import session_scope

        async with session_scope() as s:
            return [
                (await s.execute(text("SELECT library_search_norm(:v)"), {"v": v})).scalar_one()
                for v in values
            ]

    samples = ["Let E.M. Know", "Let Em' Know", "T.I.", "Jay-Z", "Beyoncé", "ÉLAN / Łódź",
               "Don’t Stop", "AC/DC", "Sigur Rós", "Mötley Crüe", "  spaced   out  "]
    assert asyncio.run(norm(samples)) == [music_search.search_norm(v) for v in samples]


@requires_db
def test_words_match_across_fields_whatever_the_punctuation(client) -> None:
    assert _titles(_search(client, f"em know {M}")) == ["Let Em' Know"]
    assert _titles(_search(client, f"let e.m. know {M}")) == ["Let Em' Know"]
    assert _titles(_search(client, f"know tiller {M}")) == ["Let Em' Know"]
    assert _titles(_search(client, f"DEJA vu beyonce {M}")) == ["Déjà Vu"]


@requires_db
def test_ti_finds_ti_first(client) -> None:
    page = _search(client, f"ti {M}")
    artists = [t["artist"] for t in page["items"]]
    # Every row holding "ti" anywhere is found; the whole-word ones (T.I.,
    # "Platinum Ti") before Bryson Tiller's, where it only starts a word.
    whole = [i for i, a in enumerate(artists) if a in ("T.I.", "Jeezy, T.I.", "Sleepers")]
    tiller = [i for i, a in enumerate(artists) if a == "Bryson Tiller"]
    assert len(whole) == 5 and tiller and max(whole) < min(tiller)
    assert page["total"] == len(page["items"])


@requires_db
def test_a_title_that_is_the_query_ranks_first(client) -> None:
    assert _titles(_search(client, f"know {M}"))[0] == "Know"


@requires_db
def test_typos_fall_back_to_the_closest(client) -> None:
    page = _search(client, f"bryson tiler exchnge {M}")
    assert page["total"] >= 1
    assert page["items"][0]["title"] == "Exchange"


@requires_db
def test_search_pages_by_cursor_without_repeats(client) -> None:
    seen: list[int] = []
    params = {"q": f"{M}", "limit": 3, "sort": "title"}
    page = client.get("/api/music/library", params=params).json()
    total = page["total"]
    seen += [t["id"] for t in page["items"]]
    while page["next_cursor"]:
        page = client.get("/api/music/library",
                          params={**params, "cursor": page["next_cursor"]}).json()
        seen += [t["id"] for t in page["items"]]
    assert len(seen) == len(set(seen)) == total == len(ROWS)
    # A cursor cut for the search is refused by the plain list.
    first = client.get("/api/music/library", params=params).json()
    r = client.get("/api/music/library",
                   params={"limit": 3, "sort": "title", "cursor": first["next_cursor"]})
    assert r.status_code == 400


@requires_db
def test_artists_list_split_count_and_search(client) -> None:
    r = client.get("/api/music/artists", params={"q": M, "limit": 50})
    assert r.status_code == 200, r.text
    # Album names carry the marker, not artists: search by name instead.
    r = client.get("/api/music/artists", params={"limit": 200, "q": "ti"})
    names = {a["name"]: a for a in r.json()["items"]}
    assert "T.I." in names
    ti = names["T.I."]
    # Three solo rows plus the Jeezy credit.
    assert ti["track_count"] >= 4
    assert r.json()["items"][0]["name"] == "T.I."
    assert "Jeezy" in {a["name"] for a in client.get(
        "/api/music/artists", params={"q": "jeezy"}).json()["items"]}
    band = client.get("/api/music/artists", params={"q": "earth wind"}).json()["items"]
    assert "Earth, Wind & Fire" in {a["name"] for a in band}
    assert "Earth" not in {a["name"] for a in band}


@requires_db
def test_artists_page_by_cursor(client) -> None:
    seen: list[str] = []
    page = client.get("/api/music/artists", params={"limit": 2, "sort": "tracks"}).json()
    total = page["total"]
    seen += [a["name"] for a in page["items"]]
    while page["next_cursor"]:
        page = client.get("/api/music/artists",
                          params={"limit": 2, "sort": "tracks", "cursor": page["next_cursor"]}).json()
        seen += [a["name"] for a in page["items"]]
    assert len(seen) == len(set(seen)) == total


@requires_db
def test_one_artist_their_albums_and_tracks(client) -> None:
    r = client.get("/api/music/artist", params={"name": "t i"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["name"] == "T.I."
    albums = [a["name"] for a in d["albums"]]
    assert f"Paper {M}" in albums and f"Late {M}" in albums
    assert albums[-1] is None   # the album-less single last
    lib = client.get("/api/music/library",
                     params={"artist": "T.I.", "q": M, "sort": "album", "limit": 50}).json()
    assert {t["title"] for t in lib["items"]} == {
        "Whatever You Like", "Live Your Life", "Go Crazy", f"Single {M}",
    }
    assert set(t["id"] for t in lib["items"]) <= set(d["track_ids"])
    one = client.get("/api/music/library",
                     params={"artist": "T.I.", "album": f"Paper {M}", "limit": 50}).json()
    assert {t["title"] for t in one["items"]} == {"Whatever You Like", "Live Your Life"}
    # One album's ids, for "play" on an album chip; '' = the album-less ones.
    paper = client.get("/api/music/artist", params={"name": "T.I.", "album": f"Paper {M}"}).json()
    assert sorted(paper["track_ids"]) == sorted(t["id"] for t in one["items"])
    loose = client.get("/api/music/artist", params={"name": "T.I.", "album": ""}).json()
    # This test's album-less single is among them (other tests in the same
    # worker's database may hold a T.I. track of their own, so not "== 1").
    single = next(t["id"] for t in lib["items"] if t["title"] == f"Single {M}")
    assert single in loose["track_ids"] and len(paper["albums"]) == len(d["albums"])
    assert not set(loose["track_ids"]) & set(paper["track_ids"])
    assert client.get("/api/music/artist", params={"name": f"nobody {M}"}).status_code == 404
