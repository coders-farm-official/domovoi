"""Paging on the Postgres-backed lists: Music library, Audiobooks, Podcast
subscriptions and episodes (web/backend/api/paging.py).

What each pins: following ``next_cursor`` visits every row exactly once in
exactly the order an unpaged read gives (ties, NULLs-last and mixed
directions included); ``total`` counts the filter, not the page; rows
added or removed between pages are neither repeated nor skip anything
else; the old client contract (bare list with no paging params, Music's
offset) is unchanged; bounds and bad cursors are 422 / 400.

Needs the database. Rows carry a per-run marker the filters select on, and
are removed afterwards.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from domovoi.tests.conftest import requires_db
from web.backend.main import app

pytestmark = requires_db

RUN = uuid.uuid4().hex[:10]
BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _client():
    return TestClient(app, headers={"X-Requested-With": "domovoi-tests"})


def _run(coro):
    return asyncio.run(coro)


async def _exec(sql: str, params: dict | list | None = None) -> None:
    from web.backend.db import session_scope

    async with session_scope() as s:
        if isinstance(params, list):
            for p in params:
                await s.execute(text(sql), p)
        else:
            await s.execute(text(sql), params or {})


def _follow(c, path: str, params: dict, limit: int) -> tuple[list[dict], list[dict]]:
    """Every row through next_cursor, plus the per-page answers."""
    rows, answers, cursor = [], [], None
    for _ in range(200):
        q = dict(params, limit=limit)
        if cursor:
            q["cursor"] = cursor
        r = c.get(path, params=q)
        assert r.status_code == 200, r.text
        body = r.json()
        answers.append(body)
        rows.extend(body["items"])
        if not body["has_more"]:
            assert body["next_cursor"] is None
            return rows, answers
        cursor = body["next_cursor"]
    raise AssertionError("never reached the last page")


# ─── Music library ───────────────────────────────────────────────────────────
N_TRACKS = 57


@pytest.fixture
def tracks():
    """57 tracks: shared added_at stamps (ties), some NULL titles / artists /
    durations, all carrying the run marker in the album."""
    rows = []
    for i in range(N_TRACKS):
        rows.append({
            "fp": f"/srv/pg{RUN}/t{i:03d}.mp3",
            "title": None if i % 9 == 0 else f"Song {chr(65 + (i * 7) % 26)}{i % 4}",
            "artist": None if i % 5 == 0 else f"Artist {(i * 3) % 8}",
            "album": f"album-{RUN}",
            "dur": None if i % 6 == 0 else 100 + (i * 37) % 50,
            "at": BASE + timedelta(minutes=i // 4),   # four rows per stamp
            "fav": i % 3 == 0,
        })
    _run(_exec(
        "INSERT INTO library_tracks (id, file_path, title, artist, album, duration_sec,"
        " added_at, added_via, favorited) VALUES ((SELECT COALESCE(MAX(id), 0) + 1"
        " FROM library_tracks), :fp, :title, :artist, :album, :dur, :at, 'manual', :fav)",
        rows,
    ))
    try:
        yield rows
    finally:
        _run(_exec("DELETE FROM library_tracks WHERE album = :a", {"a": f"album-{RUN}"}))


@pytest.mark.parametrize("sort", ["added_desc", "added_asc", "title", "artist", "duration"])
def test_music_cursor_pages_match_the_unpaged_order(tracks, sort):
    with _client() as c:
        whole = c.get("/api/music/library",
                      params={"q": RUN, "sort": sort, "limit": 200}).json()
        assert whole["total"] == N_TRACKS and len(whole["items"]) == N_TRACKS
        assert whole["has_more"] is False and whole["next_cursor"] is None
        paged, answers = _follow(c, "/api/music/library", {"q": RUN, "sort": sort}, limit=8)
    assert [t["id"] for t in paged] == [t["id"] for t in whole["items"]]
    assert all(a["total"] == N_TRACKS for a in answers)
    assert len(answers) == 8   # ceil(57 / 8)


def test_music_filters_cover_every_page(tracks):
    with _client() as c:
        paged, answers = _follow(c, "/api/music/library",
                                 {"q": RUN, "favorited": "true", "sort": "title"}, limit=5)
    favs = sum(1 for t in tracks if t["fav"])
    assert len(paged) == favs == answers[0]["total"]
    assert all(t["favorited"] for t in paged)


def test_music_rows_changing_between_pages(tracks):
    """Newest first; after page one, three tracks land at the top and two
    rows already shown plus one not yet shown are deleted."""
    with _client() as c:
        first = c.get("/api/music/library",
                      params={"q": RUN, "sort": "added_desc", "limit": 10}).json()
        shown = [t["id"] for t in first["items"]]
        later = [{"fp": f"/srv/pg{RUN}/new{i}.mp3", "album": f"album-{RUN}",
                  "at": BASE + timedelta(days=5, minutes=i)} for i in range(3)]
        _run(_exec(
            "INSERT INTO library_tracks (id, file_path, album, added_at, added_via) VALUES"
            " ((SELECT COALESCE(MAX(id), 0) + 1 FROM library_tracks), :fp, :album, :at, 'manual')",
            later,
        ))
        whole = c.get("/api/music/library",
                      params={"q": RUN, "sort": "added_desc", "limit": 200}).json()
        ids = [t["id"] for t in whole["items"]]
        unshown = [i for i in ids if i not in shown and i not in ids[:3]]
        gone = [shown[0], shown[-1], unshown[5]]
        _run(_exec("DELETE FROM library_tracks WHERE id = ANY(:ids)", {"ids": gone}))
        rest, cursor = [], first["next_cursor"]
        while cursor:
            body = c.get("/api/music/library", params={
                "q": RUN, "sort": "added_desc", "limit": 10, "cursor": cursor}).json()
            rest.extend(t["id"] for t in body["items"])
            cursor = body["next_cursor"]
    assert not set(rest) & set(shown)                       # no repeats
    assert set(rest) == set(unshown) - {unshown[5]}          # nothing else skipped


def test_music_offset_is_unchanged_for_the_installed_app(tracks):
    with _client() as c:
        a = c.get("/api/music/library", params={"q": RUN, "limit": 12, "offset": 12}).json()
        whole = c.get("/api/music/library", params={"q": RUN, "limit": 200}).json()
        default = c.get("/api/music/library", params={"q": RUN}).json()
    assert [t["id"] for t in a["items"]] == [t["id"] for t in whole["items"][12:24]]
    assert a["offset"] == 12 and a["total"] == N_TRACKS and a["has_more"] is True
    assert default["limit"] == 50 and len(default["items"]) == 50


def test_music_bounds_and_bad_cursors(tracks):
    with _client() as c:
        assert c.get("/api/music/library", params={"limit": 201}).status_code == 422
        assert c.get("/api/music/library", params={"limit": 0}).status_code == 422
        assert c.get("/api/music/library", params={"cursor": "garbage!"}).status_code == 400
        cut = c.get("/api/music/library",
                    params={"q": RUN, "sort": "title", "limit": 5}).json()["next_cursor"]
        r = c.get("/api/music/library", params={"q": RUN, "sort": "artist", "cursor": cut})
        assert r.status_code == 400


# ─── Audiobooks ──────────────────────────────────────────────────────────────
@pytest.fixture
def books():
    rows = [{
        "title": f"Book {RUN} {chr(65 + (i * 11) % 26)}",
        "author": None if i % 4 == 0 else f"Author {i % 5}",
        "fp": f"/srv/ab{RUN}/b{i:03d}.m4b",
        "at": BASE + timedelta(hours=i // 3),
    } for i in range(23)]
    _run(_exec(
        "INSERT INTO audiobooks (id, title, author, file_path, added_at) VALUES"
        " ((SELECT COALESCE(MAX(id), 0) + 1 FROM audiobooks), :title, :author, :fp, :at)",
        rows,
    ))
    try:
        yield rows
    finally:
        _run(_exec("DELETE FROM audiobooks WHERE file_path LIKE :p", {"p": f"/srv/ab{RUN}/%"}))


def test_audiobooks_old_clients_get_the_bare_list(books):
    with _client() as c:
        r = c.get("/api/audiobooks")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    mine = [b for b in body if RUN in b["title"]]
    assert len(mine) == 23
    assert "file_path" not in mine[0] and "file_ext" in mine[0]


@pytest.mark.parametrize("sort", ["title", "author", "newest"])
def test_audiobooks_cursor_pages(books, sort):
    with _client() as c:
        whole = c.get("/api/audiobooks", params={"q": RUN, "sort": sort})
        paged, answers = _follow(c, "/api/audiobooks", {"q": RUN, "sort": sort}, limit=4)
    assert isinstance(whole.json(), list)          # q alone keeps the bare list
    assert [b["id"] for b in paged] == [b["id"] for b in whole.json()]
    assert answers[0]["total"] == 23 and len(answers) == 6
    assert all("_pk0" not in b and "file_path" not in b for b in paged)


def test_audiobooks_search_spans_every_page(books):
    with _client() as c:
        body = c.get("/api/audiobooks", params={"q": f"{RUN} A", "limit": 2}).json()
    expect = sum(1 for b in books if b["title"].endswith(" A"))
    assert body["total"] == expect


# ─── Podcasts ────────────────────────────────────────────────────────────────
@pytest.fixture
def shows():
    subs = [{"url": f"https://example.invalid/{RUN}/{i}.xml",
             "title": None if i == 3 else f"Show {RUN} {chr(90 - i)}"} for i in range(9)]
    _run(_exec(
        "INSERT INTO podcast_subscriptions (id, feed_url, title) VALUES"
        " ((SELECT COALESCE(MAX(id), 0) + 1 FROM podcast_subscriptions), :url, :title)",
        subs,
    ))

    async def _first_id():
        from web.backend.db import session_scope

        async with session_scope() as s:
            return (await s.execute(text(
                "SELECT id FROM podcast_subscriptions WHERE feed_url = :u"),
                {"u": subs[0]["url"]})).scalar_one()

    sid = _run(_first_id())
    eps = [{
        "sid": sid, "guid": f"g{RUN}-{i}", "title": f"Episode {i:03d}",
        "pub": None if i % 10 == 0 else BASE + timedelta(days=i // 2),
        "fp": f"/srv/pod{RUN}/{i}.mp3" if i % 4 == 0 else None,
    } for i in range(41)]
    _run(_exec(
        "INSERT INTO podcast_episodes (id, subscription_id, guid, title, published_at, file_path)"
        " VALUES ((SELECT COALESCE(MAX(id), 0) + 1 FROM podcast_episodes),"
        " :sid, :guid, :title, :pub, :fp)",
        eps,
    ))
    try:
        yield sid, subs, eps
    finally:
        _run(_exec("DELETE FROM podcast_subscriptions WHERE feed_url LIKE :p",
                   {"p": f"https://example.invalid/{RUN}/%"}))


def _subs_url(i: int) -> str:
    return f"https://example.invalid/{RUN}/{i}.xml"


def test_subscriptions_old_and_paged(shows):
    with _client() as c:
        legacy = c.get("/api/podcasts/subscriptions").json()
        paged, answers = _follow(c, "/api/podcasts/subscriptions", {"q": RUN}, limit=2)
        whole = c.get("/api/podcasts/subscriptions", params={"q": RUN}).json()
    assert isinstance(legacy, list) and isinstance(whole, list)
    assert len([s for s in legacy if RUN in s["feed_url"]]) == 9
    assert [s["id"] for s in paged] == [s["id"] for s in whole]
    assert answers[0]["total"] == 9
    first = next(s for s in paged if s["feed_url"] == _subs_url(0))
    assert first["episode_count"] == 41 and first["downloaded_count"] == 0


@pytest.mark.parametrize("sort", ["newest", "oldest"])
def test_episodes_cursor_pages_undated_last(shows, sort):
    sid, _subs, eps = shows
    path = f"/api/podcasts/subscriptions/{sid}/episodes"
    with _client() as c:
        whole = c.get(path, params={"sort": sort}).json()
        paged, answers = _follow(c, path, {"sort": sort}, limit=6)
    assert isinstance(whole, list) and len(whole) == 41
    assert [e["id"] for e in paged] == [e["id"] for e in whole]
    assert all(e["published_at"] is None for e in paged[-5:])     # 5 undated, last
    assert answers[0]["total"] == 41
    assert all("file_path" not in e and "file_ext" in e for e in paged)


def test_episodes_filters(shows):
    sid, _subs, eps = shows
    path = f"/api/podcasts/subscriptions/{sid}/episodes"
    with _client() as c:
        dl = c.get(path, params={"downloaded": "true", "limit": 3}).json()
        q = c.get(path, params={"q": "Episode 02", "limit": 50}).json()
    assert dl["total"] == sum(1 for e in eps if e["fp"]) and all(e["has_file"] for e in dl["items"])
    assert q["total"] == 10   # Episode 020..029
