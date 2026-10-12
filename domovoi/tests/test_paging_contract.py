"""The shared paging contract, DB-free (web/backend/api/paging.py).

Every files-like list (Music, Videos, Files, Audiobooks, Podcasts) pages
the same way: ``limit`` (default 50, max 200), a keyset ``cursor`` from the
previous page's ``next_cursor``, ``offset`` for old clients, and ``total`` /
``has_more`` / ``next_cursor`` in the answer. These pin the engine itself;
the per-endpoint files pin each list.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi import HTTPException

from web.backend.api import paging
from web.backend.api.paging import SortKey


def _rows(n: int) -> tuple[list[dict], list[tuple]]:
    rows = [{"name": f"item-{i:04d}"} for i in range(n)]
    return rows, [(r["name"],) for r in rows]


def _walk(rows, keys, *, limit, keep=None, sort="name"):
    """Every row, page by page through next_cursor."""
    seen, cursor, pages = [], None, 0
    while True:
        page, f = paging.page_view(rows, keys, sort=sort, limit=limit, cursor=cursor,
                                   offset=None, keep=keep)
        seen.extend(r["name"] for r in page)
        pages += 1
        if not f["has_more"]:
            assert f["next_cursor"] is None
            return seen, f, pages
        cursor = f["next_cursor"]


def test_cursor_round_trip_and_opaque():
    c = paging.encode_cursor("newest", [-1.5, "core:videos", "a/b.mp4"])
    assert "=" not in c and "/" not in c and "+" not in c   # url-safe, unpadded
    assert paging.decode_cursor(c, "newest", 3) == [-1.5, "core:videos", "a/b.mp4"]


@pytest.mark.parametrize("bad", ["", "!!!", "e30", base64.urlsafe_b64encode(b"[1,2]").decode(),
                                 "x" * 3000])
def test_garbage_cursor_is_a_400(bad):
    with pytest.raises(HTTPException) as e:
        paging.decode_cursor(bad, "name", 1)
    assert e.value.status_code == 400


def test_a_cursor_for_another_sort_is_refused():
    c = paging.encode_cursor("name", ["x"])
    with pytest.raises(HTTPException) as e:
        paging.decode_cursor(c, "newest", 1)
    assert e.value.status_code == 400 and "different sort" in e.value.detail


def test_a_cursor_of_the_wrong_width_is_refused():
    with pytest.raises(HTTPException):
        paging.decode_cursor(paging.encode_cursor("name", ["x", "y"]), "name", 1)


def test_pages_cover_every_row_exactly_once():
    rows, keys = _rows(237)
    seen, f, pages = _walk(rows, keys, limit=50)
    assert seen == [r["name"] for r in rows]
    assert pages == 5 and f["total"] == 237


def test_first_page_fields():
    rows, keys = _rows(120)
    page, f = paging.page_view(rows, keys, sort="name", limit=50, cursor=None, offset=None)
    assert len(page) == 50
    assert f == {"total": 120, "limit": 50, "has_more": True,
                 "next_cursor": f["next_cursor"], "offset": 0}
    assert f["next_cursor"]


def test_offset_paging_still_works():
    rows, keys = _rows(120)
    page, f = paging.page_view(rows, keys, sort="name", limit=50, cursor=None, offset=100)
    assert [r["name"] for r in page] == [f"item-{i:04d}" for i in range(100, 120)]
    assert f["has_more"] is False and f["offset"] == 100


def test_filter_applies_to_the_whole_list_and_total():
    rows, keys = _rows(500)
    keep = lambda i: i % 7 == 0  # noqa: E731
    seen, f, _ = _walk(rows, keys, limit=13, keep=keep)
    assert seen == [rows[i]["name"] for i in range(500) if i % 7 == 0]
    assert f["total"] == len(seen) == 72


def test_rows_added_before_the_cursor_do_not_repeat_and_after_are_not_skipped():
    rows, keys = _rows(100)
    page, f = paging.page_view(rows, keys, sort="name", limit=40, cursor=None, offset=None)
    shown = [r["name"] for r in page]
    # Between pages: ten rows land at the top, one row lands after the cut.
    new = [{"name": f"aaa-{i}"} for i in range(10)] + [{"name": "item-0050x"}]
    rows2 = sorted(rows + new, key=lambda r: r["name"])
    keys2 = [(r["name"],) for r in rows2]
    rest, cursor = [], f["next_cursor"]
    while cursor:
        page, f = paging.page_view(rows2, keys2, sort="name", limit=40, cursor=cursor, offset=None)
        rest.extend(r["name"] for r in page)
        cursor = f["next_cursor"]
    assert not set(shown) & set(rest)                         # nothing repeated
    assert set(shown) | set(rest) == {r["name"] for r in rows} | {"item-0050x"}


def test_rows_removed_between_pages_skip_nothing_else():
    rows, keys = _rows(100)
    page, f = paging.page_view(rows, keys, sort="name", limit=40, cursor=None, offset=None)
    # The very row the cursor names, and three before it, are deleted.
    gone = {"item-0039", "item-0010", "item-0011", "item-0012"}
    rows2 = [r for r in rows if r["name"] not in gone]
    keys2 = [(r["name"],) for r in rows2]
    page2, _ = paging.page_view(rows2, keys2, sort="name", limit=40,
                                cursor=f["next_cursor"], offset=None)
    assert page2[0]["name"] == "item-0040"


def test_a_cursor_past_the_end_gives_an_empty_last_page():
    rows, keys = _rows(3)
    c = paging.encode_cursor("name", ["zzz"])
    page, f = paging.page_view(rows, keys, sort="name", limit=50, cursor=c, offset=None)
    assert page == [] and f["has_more"] is False and f["next_cursor"] is None


def test_a_cursor_on_an_empty_list():
    page, f = paging.page_view([], [], sort="name", limit=50,
                               cursor=paging.encode_cursor("name", ["x"]), offset=None)
    assert page == [] and f["total"] == 0


def test_a_cursor_of_the_wrong_types_is_a_400_not_a_500():
    rows = [{"name": "a"}]
    keys = [(1.0, "a")]
    with pytest.raises(HTTPException) as e:
        paging.page_view(rows, keys, sort="newest", limit=5,
                         cursor=paging.encode_cursor("newest", [{"x": 1}, "a"]), offset=None)
    assert e.value.status_code == 400


def test_wants_page():
    assert paging.wants_page(None, None, None) is False
    assert paging.wants_page(10, None, None) is True
    assert paging.wants_page(None, "c", None) is True
    assert paging.wants_page(None, None, 0) is True


# ─── SQL keyset ──────────────────────────────────────────────────────────────
def test_order_sql_puts_nulls_last_whatever_the_direction():
    keys = [SortKey("duration_sec", desc=True, nulls_last=True, kind="int"),
            SortKey("id", desc=True, kind="int")]
    assert paging.order_sql(keys) == "(duration_sec) IS NULL, duration_sec DESC, id DESC"


def test_keyset_where_expands_to_strictly_after():
    keys = [SortKey("LOWER(title)", nulls_last=True), SortKey("id", desc=True, kind="int")]
    params: dict = {}
    sql = paging.keyset_where(keys, paging.encode_cursor("title", ["abc", 7]), "title", params)
    assert sql == (
        "((((LOWER(title)) IS NULL) > :_pc0n)"
        " OR (((LOWER(title)) IS NULL) = :_pc0n AND LOWER(title) > :_pc0)"
        " OR (((LOWER(title)) IS NULL) = :_pc0n AND LOWER(title) IS NOT DISTINCT FROM :_pc0"
        " AND id < :_pc1))"
    )
    assert params == {"_pc0n": False, "_pc0": "abc", "_pc1": 7}


def test_keyset_where_types_timestamps_and_refuses_junk():
    keys = [SortKey("added_at", desc=True, kind="ts"), SortKey("id", desc=True, kind="int")]
    params: dict = {}
    paging.keyset_where(keys, paging.encode_cursor("s", ["2026-10-11T10:00:00+00:00", 3]),
                        "s", params)
    assert params["_pc0"].tzinfo is not None and params["_pc1"] == 3
    for junk in (["not a time", 3], ["2026-10-11T10:00:00", 3], ["2026-10-11T10:00:00+00:00", "3"],
                 [None, 3], ["2026-10-11T10:00:00+00:00", True]):
        with pytest.raises(HTTPException) as e:
            paging.keyset_where(keys, paging.encode_cursor("s", junk), "s", {})
        assert e.value.status_code == 400


def test_db_page_dicts_strips_key_columns():
    keys = [SortKey("x"), SortKey("id", kind="int")]
    rows = [{"id": i, "x": f"v{i}", "_pk0": f"v{i}", "_pk1": i} for i in range(4)]
    page, more, nxt = paging.db_page_dicts(rows, keys, limit=3, sort="x")
    assert more is True and [p["id"] for p in page] == [0, 1, 2]
    assert all("_pk0" not in p for p in page)
    assert json.loads(base64.urlsafe_b64decode(nxt + "==").decode()) == {"s": "x", "k": ["v2", 2]}


# ─── listing_cache bookkeeping (DB-free) ─────────────────────────────────────
def _snap(n: int):
    from web.backend.api import listing_cache

    return listing_cache.Snapshot(rows=[{"i": i} for i in range(n)], hay=[""] * n, dirs={})


def test_the_cache_is_bounded_by_rows_and_keeps_the_newest(monkeypatch):
    from web.backend.api import listing_cache

    listing_cache.clear()
    monkeypatch.setattr(listing_cache, "MAX_CACHED_ROWS", 25)
    for name in ("a", "b", "c"):
        listing_cache.get((f"lib:{name}", "/r"), lambda: _snap(10))
    kept = [k[0] for k in listing_cache._snapshots]
    assert kept == ["lib:b", "lib:c"]
    listing_cache.get(("lib:huge", "/r"), lambda: _snap(100))
    assert [k[0] for k in listing_cache._snapshots] == ["lib:huge"]   # alone, never evicted
    listing_cache.clear()


def test_invalidate_drops_a_library_and_an_in_flight_build_is_not_kept():
    from web.backend.api import listing_cache

    listing_cache.clear()
    first = listing_cache.get(("lib:x", "/r"), lambda: _snap(1))
    assert listing_cache.get(("lib:x", "/r"), lambda: _snap(2)) is first      # cached
    listing_cache.invalidate("lib:x")
    assert first.expired

    def build_while_a_write_lands():
        listing_cache.invalidate("lib:x")
        return _snap(3)

    got = listing_cache.get(("lib:x", "/r"), build_while_a_write_lands)
    assert len(got.rows) == 3                     # the caller gets its answer...
    assert ("lib:x", "/r") not in listing_cache._snapshots   # ...but it isn't kept
    listing_cache.clear()
