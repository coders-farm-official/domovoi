"""Editable room queues: MPD mechanics, route shape, provenance, blocks.

Deliberately split so the parts that don't need Postgres CAN'T skip:

* **DB-free** — the stub MPD's queue semantics (append doesn't clear; songids
  stay stable across a move; a stale id is skipped), the entry normalization,
  the block-message copy, and the ROUTE-SHADOWING guard. That last one is the
  reason this file exists at all: ``/v1/admin/music/{action}/{room_id}`` is a
  two-segment catch-all declared in the same app, and if the queue routes ever
  land behind it, ``POST /v1/admin/music/queue/kitchen/add`` silently becomes
  "invalid action: queue". No database is involved in getting that wrong.
* **requires_db** — the web layer: device registration, provenance stamping
  and the join that reads it back, and block enforcement.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Match

import web.backend.api.music_queue as queue_api
from domovoi.clients import mpd as mpd_mod
from domovoi.clients.mpd import MPDStubClient
from domovoi.tests.conftest import requires_db


# ─── Stub MPD queue semantics (no DB, no Postgres, no MPD) ─────────────────
#
# The stub is what every other test and USE_STUBS mode run against, so its
# queue model has to match the real client's contract: ids assigned by the
# server, never reused, stable across moves; pos derived from order.


def _stub_with(*titles: str) -> MPDStubClient:
    stub = MPDStubClient()
    asyncio.run(stub.queue_add_tracks([{"title": t} for t in titles]))
    return stub


def test_queue_add_appends_instead_of_replacing() -> None:
    """The difference between queue_add_tracks and prepare_tracks, asserted:
    one appends, the other replaces. Getting this backwards would make "add to
    queue" silently wipe whatever the room was playing."""
    stub = _stub_with("a", "b")
    asyncio.run(stub.queue_add_tracks([{"title": "c"}]))
    items = asyncio.run(stub.queue_list())
    assert [i["title"] for i in items] == ["a", "b", "c"]
    assert [i["pos"] for i in items] == [0, 1, 2]

    asyncio.run(stub.prepare_tracks([{"title": "z"}]))
    assert [i["title"] for i in asyncio.run(stub.queue_list())] == ["z"]


def test_song_ids_are_unique_and_never_reused() -> None:
    stub = _stub_with("a", "b")
    first_ids = [i["id"] for i in asyncio.run(stub.queue_list())]
    assert len(set(first_ids)) == 2

    asyncio.run(stub.queue_remove(first_ids))
    asyncio.run(stub.queue_add_tracks([{"title": "c"}]))
    new_ids = [i["id"] for i in asyncio.run(stub.queue_list())]
    # A reused id would make a stale provenance row describe the wrong song.
    assert not set(new_ids) & set(first_ids)


def test_move_changes_positions_but_not_song_ids() -> None:
    """Why provenance is keyed by songid: a move renumbers every pos and
    leaves ids alone, so "added by" survives a reorder with no DB write."""
    stub = _stub_with("a", "b", "c")
    before = {i["title"]: i["id"] for i in asyncio.run(stub.queue_list())}

    assert asyncio.run(stub.queue_move(before["c"], 0)) is True
    items = asyncio.run(stub.queue_list())
    assert [i["title"] for i in items] == ["c", "a", "b"]
    assert {i["title"]: i["id"] for i in items} == before
    assert [i["pos"] for i in items] == [0, 1, 2]


def test_move_reports_false_for_an_unknown_song_id() -> None:
    stub = _stub_with("a")
    assert asyncio.run(stub.queue_move(9999, 0)) is False


def test_remove_skips_stale_ids_without_failing() -> None:
    """Two people pruning the same queue must not hand either an error."""
    stub = _stub_with("a", "b")
    ids = [i["id"] for i in asyncio.run(stub.queue_list())]
    removed = asyncio.run(stub.queue_remove([ids[0], 9999]))
    assert removed == [ids[0]]
    assert [i["title"] for i in asyncio.run(stub.queue_list())] == ["b"]


def test_clear_empties_the_queue_and_stops() -> None:
    stub = _stub_with("a", "b")
    asyncio.run(stub.queue_clear())
    assert asyncio.run(stub.queue_list()) == []
    assert asyncio.run(stub.state()) == "stop"
    assert asyncio.run(stub.current_song()) is None


# ─── Entry normalization (pure) ───────────────────────────────────────────


def test_queue_entry_dict_handles_both_mpd_capitalizations() -> None:
    from domovoi.main import _queue_entry_dict

    upper = _queue_entry_dict(
        {"id": "7", "pos": "2", "file": "a.mp3", "Title": "T",
         "Artist": "A", "Album": "L", "Time": "183.4"}
    )
    assert upper == {
        "song_id": 7, "pos": 2, "file": "a.mp3",
        "title": "T", "artist": "A", "album": "L", "duration_sec": 183,
    }

    lower = _queue_entry_dict(
        {"id": 7, "pos": 0, "file": "a.mp3", "title": "T",
         "artist": "A", "album": "L", "duration": "60"}
    )
    assert lower["title"] == "T" and lower["duration_sec"] == 60


def test_queue_entry_dict_survives_a_missing_duration() -> None:
    from domovoi.main import _queue_entry_dict

    entry = _queue_entry_dict({"id": 1, "pos": 0, "file": "live.mp3"})
    assert entry["duration_sec"] is None
    assert entry["title"] is None


# ─── Route shape: the {action} catch-all must not swallow queue paths ──────


def _resolved_endpoint(method: str, path: str):
    """The endpoint FastAPI would dispatch to. Declaration order decides,
    which is exactly what this guards."""
    from domovoi.main import app

    scope = {
        "type": "http", "method": method, "path": path,
        "headers": [], "query_string": b"", "root_path": "",
    }
    for route in app.router.routes:
        match, _child = route.matches(scope)
        if match == Match.FULL:
            return getattr(route, "endpoint", None)
    return None


@pytest.mark.parametrize(
    "suffix,expected",
    [
        ("add", "admin_music_queue_add"),
        ("remove", "admin_music_queue_remove"),
        ("move", "admin_music_queue_move"),
        ("clear", "admin_music_queue_clear"),
    ],
)
def test_queue_post_routes_are_not_captured_by_the_action_matcher(
    suffix: str, expected: str
) -> None:
    endpoint = _resolved_endpoint("POST", f"/v1/admin/music/queue/kitchen/{suffix}")
    assert endpoint is not None, f"no route matched queue/{suffix}"
    assert endpoint.__name__ == expected


def test_the_action_matcher_still_owns_its_own_two_segment_paths() -> None:
    endpoint = _resolved_endpoint("POST", "/v1/admin/music/pause/kitchen")
    assert endpoint is not None and endpoint.__name__ == "admin_music_action"


def test_queue_read_route_resolves_to_the_queue_endpoint() -> None:
    endpoint = _resolved_endpoint("GET", "/v1/admin/music/queue/kitchen")
    assert endpoint is not None and endpoint.__name__ == "admin_music_queue"


# ─── Core GET /queue against the stub (no DB: it reads MPD only) ───────────


@pytest.fixture
def seeded_room():
    """A stub MPD registered as the 'kitchen' room with three queued songs."""
    stub = _stub_with("one", "two", "three")
    previous = mpd_mod._clients.get("kitchen")
    mpd_mod._clients["kitchen"] = stub  # type: ignore[assignment]
    try:
        yield stub
    finally:
        if previous is None:
            mpd_mod._clients.pop("kitchen", None)
        else:
            mpd_mod._clients["kitchen"] = previous


def test_core_queue_read_reports_order_and_the_playing_entry(seeded_room) -> None:
    from domovoi.main import app

    # prepare_tracks is what a cast does; it leaves MPD paused on the first
    # track, which is what gives us a current_song_id to assert against.
    asyncio.run(seeded_room.prepare_tracks([{"title": "cast"}]))
    with TestClient(app) as client:
        body = client.get("/v1/admin/music/queue/kitchen").json()
    assert body["room_id"] == "kitchen"
    assert [i["title"] for i in body["items"]] == ["cast"]
    assert body["current_song_id"] == body["items"][0]["song_id"]


def test_core_queue_read_is_empty_for_an_untouched_room(seeded_room) -> None:
    from domovoi.main import app

    asyncio.run(seeded_room.queue_clear())
    with TestClient(app) as client:
        body = client.get("/v1/admin/music/queue/kitchen").json()
    assert body["items"] == []
    assert body["current_song_id"] is None


# ─── Block message copy (pure) ────────────────────────────────────────────


def test_blocked_message_names_the_device_and_the_scope() -> None:
    room_scoped = queue_api._blocked_message(
        {"device_name": "Kids iPad", "device_id": "android-1", "room_id": "kitchen",
         "note": "bedtime"},
        "kitchen",
    )
    assert "Kids iPad" in room_scoped
    assert "kitchen" in room_scoped
    assert "bedtime" in room_scoped

    all_rooms = queue_api._blocked_message(
        {"device_name": "Kids iPad", "device_id": None, "room_id": None, "note": None},
        "kitchen",
    )
    assert "every room" in all_rooms
    # No note ⇒ no dangling parenthesis.
    assert "(" not in all_rooms


def test_blocked_message_falls_back_to_the_device_id() -> None:
    msg = queue_api._blocked_message(
        {"device_name": None, "device_id": "browser-abc", "room_id": None, "note": None},
        "kitchen",
    )
    assert "browser-abc" in msg


# ═══ DB-backed: devices, provenance, enforcement ═══════════════════════════

# No module-level `pytestmark` here on purpose: it would mark the DB-free
# tests above as requires_db too and skip the whole file on a box without
# Postgres. Each DB test carries its own @requires_db instead.


@pytest.fixture
def web_client():
    from web.backend.main import app as web_app

    with TestClient(web_app) as client:
        yield client


@pytest.fixture
def clean_queue_tables():
    from sqlalchemy import text as sql

    from web.backend.db import session_scope

    async def _truncate():
        async with session_scope() as s:
            await s.execute(
                sql(
                    "TRUNCATE queue_device_blocks, room_queue_items, devices "
                    "RESTART IDENTITY CASCADE"
                )
            )

    asyncio.run(_truncate())
    yield
    asyncio.run(_truncate())


@requires_db
def test_device_registers_renames_and_keeps_its_name(web_client, clean_queue_tables) -> None:
    created = web_client.post(
        "/api/devices/register",
        json={"device_id": "browser-abc123", "name": "Chrome on Windows",
              "platform": "browser"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["name"] == "Chrome on Windows"

    renamed = web_client.patch(
        "/api/devices/browser-abc123", json={"name": "  Kitchen   iPad  "}
    )
    assert renamed.status_code == 200, renamed.text
    # Whitespace collapsed so a rename can't create two block targets that
    # look identical to a person.
    assert renamed.json()["name"] == "Kitchen iPad"

    # A re-register (every app boot) must NOT stomp the chosen name.
    again = web_client.post(
        "/api/devices/register",
        json={"device_id": "browser-abc123", "name": "Chrome on Windows"},
    )
    assert again.json()["name"] == "Kitchen iPad"


@requires_db
def test_register_rejects_a_malformed_device_id(web_client, clean_queue_tables) -> None:
    bad = web_client.post("/api/devices/register", json={"device_id": "a b/c"})
    assert bad.status_code == 400


@requires_db
def test_rename_404s_for_an_unregistered_device(web_client, clean_queue_tables) -> None:
    missing = web_client.patch("/api/devices/browser-nope", json={"name": "x"})
    assert missing.status_code == 404


@requires_db
def test_blocks_crud_and_duplicate_conflict(web_client, clean_queue_tables) -> None:
    made = web_client.post(
        "/api/music/queue-blocks",
        json={"device_id": "android-kid", "device_name": "Kids iPad",
              "room_id": "kitchen", "note": "bedtime"},
    )
    assert made.status_code == 201, made.text
    block_id = made.json()["id"]

    listed = web_client.get("/api/music/queue-blocks").json()
    assert [b["id"] for b in listed] == [block_id]

    dupe = web_client.post(
        "/api/music/queue-blocks",
        json={"device_id": "android-kid", "room_id": "kitchen"},
    )
    assert dupe.status_code == 409

    assert web_client.delete(f"/api/music/queue-blocks/{block_id}").status_code == 204
    assert web_client.get("/api/music/queue-blocks").json() == []
    assert web_client.delete(f"/api/music/queue-blocks/{block_id}").status_code == 404


@requires_db
def test_a_block_must_name_something(web_client, clean_queue_tables) -> None:
    empty = web_client.post("/api/music/queue-blocks", json={"room_id": "kitchen"})
    assert empty.status_code == 400


@pytest.fixture
def fake_core(monkeypatch):
    """Stand in for the web→core hop so the web layer's own behavior —
    provenance, the join, enforcement — is what's under test.

    Records every proxied call, and keeps a toy queue so /add hands back
    plausible songids.
    """
    state = {"calls": [], "next_song_id": 100, "items": []}

    async def _get_admin(path, timeout=10.0, headers=None):
        state["calls"].append(("GET", path))
        return 200, {
            "room_id": path.rsplit("/", 1)[-1],
            "items": list(state["items"]),
            "current_song_id": state["items"][0]["song_id"] if state["items"] else None,
        }

    async def _post_admin(path, body=None, timeout=30.0, headers=None):
        state["calls"].append(("POST", path, body))
        if path.endswith("/add"):
            queued = []
            for _ in body["track_ids"]:
                song_id = state["next_song_id"]
                state["next_song_id"] += 1
                entry = {
                    "song_id": song_id, "pos": len(state["items"]),
                    "file": f"{song_id}.mp3", "title": f"song {song_id}",
                    "artist": None, "album": None, "duration_sec": 100,
                }
                state["items"].append(entry)
                queued.append({"song_id": song_id, "file": entry["file"]})
            return 200, {"queued": queued, "requested": len(body["track_ids"]),
                         "started": False}
        if path.endswith("/remove"):
            keep = [i for i in state["items"] if i["song_id"] not in set(body["song_ids"])]
            removed = [i["song_id"] for i in state["items"] if i not in keep]
            state["items"] = keep
            return 200, {"removed": removed, "skipped": []}
        if path.endswith("/move"):
            return 200, {"moved": True}
        if path.endswith("/clear"):
            state["items"] = []
            return 200, {"cleared": True}
        return 200, {}

    monkeypatch.setattr(queue_api, "get_admin", _get_admin)
    monkeypatch.setattr(queue_api, "post_admin", _post_admin)
    return state


@requires_db
def test_add_stamps_provenance_and_the_read_joins_it(
    web_client, clean_queue_tables, fake_core
) -> None:
    web_client.post(
        "/api/devices/register",
        json={"device_id": "browser-abc", "name": "Kitchen iPad"},
    )
    added = web_client.post(
        "/api/music/queue/kitchen/add",
        json={"track_ids": [1, 2], "device_id": "browser-abc"},
    )
    assert added.status_code == 200, added.text

    queue = web_client.get("/api/music/queue/kitchen").json()
    assert [i["added_by"] for i in queue["items"]] == ["Kitchen iPad", "Kitchen iPad"]
    assert [i["added_by_device_id"] for i in queue["items"]] == ["browser-abc"] * 2
    # First entry is what the fake core reports as playing.
    assert queue["items"][0]["playing"] is True
    assert queue["items"][1]["playing"] is False


@requires_db
def test_renaming_a_device_relabels_its_queue_entries(
    web_client, clean_queue_tables, fake_core
) -> None:
    """The read COALESCEs the live device name over the add-time snapshot, so
    a rename shows up in the queue without rewriting rows."""
    web_client.post(
        "/api/devices/register", json={"device_id": "browser-abc", "name": "Laptop"}
    )
    web_client.post(
        "/api/music/queue/kitchen/add",
        json={"track_ids": [1], "device_id": "browser-abc"},
    )
    web_client.patch("/api/devices/browser-abc", json={"name": "Kitchen iPad"})

    queue = web_client.get("/api/music/queue/kitchen").json()
    assert queue["items"][0]["added_by"] == "Kitchen iPad"


@requires_db
def test_an_unregistered_device_leaves_added_by_null(
    web_client, clean_queue_tables, fake_core
) -> None:
    """A device that never introduced itself has no name to show, so the UI
    gets null rather than a guess — same as a voice-added entry or a cast from
    before this feature. The id is still recorded."""
    web_client.post(
        "/api/music/queue/kitchen/add",
        json={"track_ids": [1], "device_id": "browser-never-registered"},
    )
    item = web_client.get("/api/music/queue/kitchen").json()["items"][0]
    assert item["added_by"] is None
    assert item["added_by_device_id"] == "browser-never-registered"


@requires_db
def test_an_edit_without_a_device_id_is_refused(
    web_client, clean_queue_tables, fake_core
) -> None:
    """The blocklist would be trivially evaded by omitting the field, which is
    far easier than claiming someone else's id — so every edit must name a
    device. Reading still doesn't have to."""
    for path, body in (
        ("add", {"track_ids": [1]}),
        ("remove", {"song_ids": [100]}),
        ("move", {"song_id": 100, "to_position": 0}),
        ("clear", {}),
    ):
        resp = web_client.post(f"/api/music/queue/kitchen/{path}", json=body)
        assert resp.status_code == 422, f"{path}: {resp.status_code}"
    assert web_client.get("/api/music/queue/kitchen").status_code == 200


@requires_db
def test_removing_an_entry_drops_its_provenance_row(
    web_client, clean_queue_tables, fake_core
) -> None:
    web_client.post(
        "/api/devices/register", json={"device_id": "browser-abc", "name": "Laptop"}
    )
    web_client.post(
        "/api/music/queue/kitchen/add",
        json={"track_ids": [1, 2], "device_id": "browser-abc"},
    )
    song_ids = [i["song_id"] for i in web_client.get("/api/music/queue/kitchen").json()["items"]]

    removed = web_client.post(
        "/api/music/queue/kitchen/remove",
        json={"song_ids": [song_ids[0]], "device_id": "browser-abc"},
    )
    assert removed.status_code == 200, removed.text
    remaining = web_client.get("/api/music/queue/kitchen").json()["items"]
    assert [i["song_id"] for i in remaining] == [song_ids[1]]
    assert remaining[0]["added_by"] == "Laptop"


@requires_db
def test_a_room_scoped_block_stops_every_edit_but_not_reading(
    web_client, clean_queue_tables, fake_core
) -> None:
    web_client.post(
        "/api/devices/register",
        json={"device_id": "android-kid", "name": "Kids iPad"},
    )
    web_client.post(
        "/api/music/queue-blocks",
        json={"device_id": "android-kid", "device_name": "Kids iPad",
              "room_id": "kitchen", "note": "bedtime"},
    )

    for path, body in (
        ("add", {"track_ids": [1], "device_id": "android-kid"}),
        ("remove", {"song_ids": [100], "device_id": "android-kid"}),
        ("move", {"song_id": 100, "to_position": 0, "device_id": "android-kid"}),
        ("clear", {"device_id": "android-kid"}),
    ):
        resp = web_client.post(f"/api/music/queue/kitchen/{path}", json=body)
        assert resp.status_code == 403, f"{path}: {resp.status_code} {resp.text}"
        assert "Kids iPad" in resp.json()["detail"]

    # Reading is never blocked — a blocked device should still see what's on
    # and be told why it can't change it.
    queue = web_client.get(
        "/api/music/queue/kitchen", params={"device_id": "android-kid"}
    )
    assert queue.status_code == 200
    assert queue.json()["editable"] is False
    assert "bedtime" in queue.json()["blocked_reason"]

    # Another room is unaffected.
    other = web_client.get(
        "/api/music/queue/garage", params={"device_id": "android-kid"}
    ).json()
    assert other["editable"] is True
    assert other["blocked_reason"] is None
    allowed = web_client.post(
        "/api/music/queue/garage/add",
        json={"track_ids": [1], "device_id": "android-kid"},
    )
    assert allowed.status_code == 200, allowed.text


@requires_db
def test_an_all_rooms_block_covers_every_room(
    web_client, clean_queue_tables, fake_core
) -> None:
    web_client.post(
        "/api/devices/register",
        json={"device_id": "android-kid", "name": "Kids iPad"},
    )
    web_client.post(
        "/api/music/queue-blocks", json={"device_id": "android-kid"}
    )
    for room in ("kitchen", "garage", "office"):
        resp = web_client.post(
            f"/api/music/queue/{room}/add",
            json={"track_ids": [1], "device_id": "android-kid"},
        )
        assert resp.status_code == 403
        assert "every room" in resp.json()["detail"]


@requires_db
def test_a_name_block_survives_a_reinstall_and_an_id_block_survives_a_rename(
    web_client, clean_queue_tables, fake_core
) -> None:
    """The two halves of the block, each doing its job.

    Name-only block + a fresh install that picks the household's usual name ⇒
    still blocked. Id-only block + a rename ⇒ still blocked. This is why
    enforcement matches either side.
    """
    web_client.post("/api/music/queue-blocks", json={"device_name": "Kids iPad"})
    web_client.post(
        "/api/devices/register",
        json={"device_id": "android-reinstalled", "name": "Kids iPad"},
    )
    reinstalled = web_client.post(
        "/api/music/queue/kitchen/add",
        json={"track_ids": [1], "device_id": "android-reinstalled"},
    )
    assert reinstalled.status_code == 403

    web_client.post(
        "/api/devices/register",
        json={"device_id": "android-sneaky", "name": "Kids tablet"},
    )
    web_client.post("/api/music/queue-blocks", json={"device_id": "android-sneaky"})
    web_client.patch("/api/devices/android-sneaky", json={"name": "Totally Fine Device"})
    renamed = web_client.post(
        "/api/music/queue/kitchen/add",
        json={"track_ids": [1], "device_id": "android-sneaky"},
    )
    assert renamed.status_code == 403


@requires_db
def test_an_unregistered_device_id_is_not_blocked_by_a_name_block(
    web_client, clean_queue_tables, fake_core
) -> None:
    """A name block can only match a device whose name we know. An id we've
    never seen has no name to compare, so it must pass — the alternative is
    blocking every anonymous client the moment any name is blocked."""
    web_client.post("/api/music/queue-blocks", json={"device_name": "Kids iPad"})
    resp = web_client.post(
        "/api/music/queue/kitchen/add",
        json={"track_ids": [1], "device_id": "browser-stranger"},
    )
    assert resp.status_code == 200, resp.text


@requires_db
def test_clear_wipes_the_rooms_provenance(
    web_client, clean_queue_tables, fake_core
) -> None:
    web_client.post(
        "/api/devices/register", json={"device_id": "browser-abc", "name": "Laptop"}
    )
    web_client.post(
        "/api/music/queue/kitchen/add",
        json={"track_ids": [1, 2], "device_id": "browser-abc"},
    )
    cleared = web_client.post(
        "/api/music/queue/kitchen/clear", json={"device_id": "browser-abc"}
    )
    assert cleared.status_code == 200, cleared.text

    from sqlalchemy import text as sql

    from web.backend.db import session_scope

    async def _count():
        async with session_scope() as s:
            row = await s.execute(
                sql("SELECT COUNT(*) FROM room_queue_items WHERE room_id = 'kitchen'")
            )
            return int(row.scalar_one())

    assert asyncio.run(_count()) == 0
