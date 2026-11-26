"""Rooms with several satellites (1.5.0): the pieces a docs pass found
missing.

* linked rooms (design §3.5, owner answer Q3): the admin API, the
  suggestion after repeated near-simultaneous wakes in two rooms, and its
  dismissal (remembered);
* "Update the app on this phone." (owner answer Q6): a live listener that
  can't claim, in a room that takes one turn per wake word;
* "Heard best by …" on a room's turns (design §3.2 item 10);
* "Add a satellite to the Kitchen" (design §2): the device name, `in=`,
  the invitation that pre-selects the room for an app that ignores `in=`,
  and old payloads still parsing.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi import room_devices, room_hear_suggestions, room_invites, streaming, wake_claims
from domovoi.db.repositories import RoomsRepository, SatelliteApprovalRepository
from domovoi.db.session import session_scope
from domovoi.main import app as core_app
from domovoi.satellite_abilities import Abilities
from domovoi.tests.auth_testkit import _db  # noqa: F401 — fixture
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_rooms_registry import _core, admin, house  # noqa: F401 — fixtures
from satellite import enrolment, pc_install

FP = "SHA256:4gq+pNW/lnGijI3MZ4vGYczKK6r6YgmCylej3GSSoOA"
CORE = "http://192.168.1.20:6370"


# ─── near-simultaneous wakes, no database ─────────────────────────────────


def test_two_rooms_waking_together_are_noticed_once() -> None:
    found: list[tuple[str, str]] = []
    near = room_hear_suggestions.NearWakes(lambda a, b: found.append((a, b)))
    near.heard("kitchen", frozenset({"kitchen"}), 100.0)
    near.heard("den", frozenset({"den"}), 100.4)
    # A second device of the den on the same word counts once.
    near.heard("den", frozenset({"den"}), 100.5)
    assert found == [("den", "kitchen")]


def test_wakes_far_apart_or_already_linked_are_not_noticed() -> None:
    found: list[tuple[str, str]] = []
    near = room_hear_suggestions.NearWakes(lambda a, b: found.append((a, b)))
    near.heard("kitchen", frozenset({"kitchen"}), 100.0)
    near.heard("den", frozenset({"den"}), 102.0)                 # 2 s later
    near.heard("hall", frozenset({"hall", "den"}), 102.3)        # linked to the den
    assert found == []


async def test_the_arbiter_feeds_wakes_claimed_or_not() -> None:
    now = {"t": 10.0}
    found: list[tuple[str, str]] = []
    reg = SimpleNamespace(devices_in=lambda r: [r], satellites={})
    arb = wake_claims.Arbiter(reg, clock=lambda: now["t"], near_wake=lambda a, b: found.append((a, b)))

    async def send(_s, _f):
        return None

    arb.send = send
    old = SimpleNamespace(satellite_id="den", room_id="den")
    new = SimpleNamespace(satellite_id="kitchen", room_id="kitchen")
    assert await arb.on_capture_start(old, {"trigger": "wake_word"}) is True
    now["t"] = 10.3
    await arb.on_claim(new, {"claim": 1, "floor_dbfs": -60.0})
    assert found == [("den", "kitchen")]
    # A follow-up is not a wake word.
    now["t"] = 10.4
    await arb.on_capture_start(SimpleNamespace(satellite_id="hall", room_id="hall"),
                               {"trigger": "followup"})
    assert found == [("den", "kitchen")]


# ─── the suggestion's counts, with the database ───────────────────────────


@pytest_asyncio.fixture
async def clean_settings():
    keys = (room_hear_suggestions.KEY, room_invites.KEY)
    async with session_scope() as s:
        saved = {k: (await s.execute(text("SELECT value FROM household_settings WHERE key = :k"),
                                     {"k": k})).scalar() for k in keys}
        await s.execute(text("DELETE FROM household_settings WHERE key = ANY(:k)"), {"k": list(keys)})
    yield
    async with session_scope() as s:
        await s.execute(text("DELETE FROM household_settings WHERE key = ANY(:k)"), {"k": list(keys)})
        import json

        for k, v in saved.items():
            if v is not None:
                await s.execute(text("INSERT INTO household_settings (key, value) "
                                     "VALUES (:k, CAST(:v AS jsonb))"), {"k": k, "v": json.dumps(v)})


@requires_db
async def test_three_wakes_together_make_a_suggestion_and_a_dismissal_sticks(
        house, clean_settings) -> None:
    h = house
    k, d = h["kitchen"], h["den"]
    now = dt.datetime.now(dt.timezone.utc)
    async with session_scope() as s:
        # One from a month ago is past the window.
        await room_hear_suggestions.record(s, k, d, now=now - dt.timedelta(days=30))
        await room_hear_suggestions.record(s, d, k, now=now - dt.timedelta(hours=2))
        assert await room_hear_suggestions.record(s, k, d, now=now - dt.timedelta(hours=1)) == 2
        assert await room_hear_suggestions.suggestions(s) == []
        assert await room_hear_suggestions.record(s, k, d) == 3
        got = await room_hear_suggestions.suggestions(s)
        assert got == [{"rooms": sorted((k, d)), "count": 3}]
        await room_hear_suggestions.dismiss(s, d, k)
        assert await room_hear_suggestions.suggestions(s) == []
        for _ in range(5):
            await room_hear_suggestions.record(s, k, d)
        assert await room_hear_suggestions.suggestions(s) == []       # remembered


@requires_db
async def test_a_linked_pair_is_not_suggested(house, clean_settings) -> None:
    h = house
    k, d = h["kitchen"], h["den"]
    async with session_scope() as s:
        for _ in range(3):
            await room_hear_suggestions.record(s, k, d)
        assert await room_hear_suggestions.suggestions(s)
        await RoomsRepository(s).link(k, d)
        assert await room_hear_suggestions.suggestions(s) == []


# ─── the links API ────────────────────────────────────────────────────────


@requires_db
async def test_an_admin_links_unlinks_and_dismisses(house, admin, clean_settings) -> None:  # noqa: F811
    h = house
    k, d = h["kitchen"], h["den"]
    async with session_scope() as s:
        for _ in range(3):
            await room_hear_suggestions.record(s, k, d)
    async with _core() as c:
        body = (await c.get("/v1/admin/rooms", headers=admin)).json()
        assert {"rooms": sorted((k, d)), "count": 3} in body["link_suggestions"]
        r = await c.post(f"/v1/admin/rooms/{k}/links", json={"room_id": d}, headers=admin)
        assert r.status_code == 200, r.text
        assert r.json() == {"room_id": k, "linked_rooms": [d], "added": True}
        assert wake_claims.arbiter(core_app).group(d) == frozenset({k, d})
        body = (await c.get("/v1/admin/rooms", headers=admin)).json()
        rooms = {x["room_id"]: x for x in body["rooms"]}
        assert rooms[k]["linked_rooms"] == [d] and rooms[d]["linked_rooms"] == [k]
        assert not any(set(o["rooms"]) == {k, d} for o in body["link_suggestions"])
        # Linking again changes nothing; refusals.
        r = await c.post(f"/v1/admin/rooms/{d}/links", json={"room_id": k}, headers=admin)
        assert r.json()["added"] is False
        r = await c.post(f"/v1/admin/rooms/{k}/links", json={"room_id": k}, headers=admin)
        assert r.status_code == 422
        r = await c.post(f"/v1/admin/rooms/{k}/links", json={"room_id": "rr_nowhere"},
                         headers=admin)
        assert r.status_code == 404
        r = await c.post(f"/v1/admin/rooms/{k}/links", json={"room_id": "Not An Id"},
                         headers=admin)
        assert r.status_code == 422
        r = await c.delete(f"/v1/admin/rooms/{k}/links/Bad!", headers=admin)
        assert r.status_code in (404, 422)
        r = await c.delete(f"/v1/admin/rooms/{d}/links/{k}", headers=admin)
        assert r.json() == {"room_id": d, "linked_rooms": [], "removed": True}
        assert wake_claims.arbiter(core_app).group(d) == frozenset({d})
        # The count started again on linking: no suggestion straight away.
        body = (await c.get("/v1/admin/rooms", headers=admin)).json()
        assert not any(set(o["rooms"]) == {k, d} for o in body["link_suggestions"])
        r = await c.post(f"/v1/admin/rooms/{k}/links/{d}/dismiss", headers=admin)
        assert r.json() == {"rooms": sorted((k, d)), "dismissed": True}
    async with session_scope() as s:
        for _ in range(3):
            await room_hear_suggestions.record(s, k, d)
        assert await room_hear_suggestions.suggestions(s) == []


@requires_db
async def test_a_live_device_hears_its_new_claim_flag(house, admin, monkeypatch) -> None:  # noqa: F811
    h = house
    # The arbiter reads the registry the admin fixture just made.
    monkeypatch.setattr(core_app.state, "wake_claims", None, raising=False)
    k, d = h["kitchen"], h["den"]
    sent: list[dict] = []

    async def send(frame):
        sent.append(frame)

    async def info():
        return {"room_id": d, "claim": wake_claims.arbiter(core_app).claim_flag(d)}

    live = SimpleNamespace(satellite_id=h["c"], room_id=d, knows_rooms=True,
                           _claim_flag_sent=False, _safe_send_text=send, _room_info=info,
                           abilities=Abilities(), mic_enabled=True)
    room_devices.registry(core_app).satellites[h["c"]] = live
    await room_devices.registry(core_app).refresh()
    async with _core() as c:
        r = await c.post(f"/v1/admin/rooms/{k}/links", json={"room_id": d}, headers=admin)
        assert r.status_code == 200
    assert sent == [{"type": "room", "room": {"room_id": d, "claim": True}}]
    async with session_scope() as s:
        await RoomsRepository(s).unlink(k, d)


# ─── "Update the app on this phone." ──────────────────────────────────────


@requires_db
async def test_a_listener_that_cannot_claim_is_flagged(house, admin, monkeypatch) -> None:  # noqa: F811
    h = house
    monkeypatch.setattr(core_app.state, "wake_claims", None, raising=False)
    reg = room_devices.registry(core_app)
    await reg.refresh()
    old = SimpleNamespace(satellite_id=h["b"], room_id=h["kitchen"], mic_enabled=True,
                          abilities=Abilities(declared=True, names=frozenset({"restart"})))
    new = SimpleNamespace(satellite_id=h["a"], room_id=h["kitchen"], mic_enabled=True,
                          abilities=Abilities(declared=True, names=frozenset({"wake_claim"})))
    alone = SimpleNamespace(satellite_id=h["c"], room_id=h["den"], mic_enabled=True,
                            abilities=Abilities(declared=True, names=frozenset({"restart"})))
    reg.satellites.update({h["a"]: new, h["b"]: old, h["c"]: alone})
    async with _core() as c:
        body = (await c.get("/v1/admin/rooms", headers=admin)).json()
    flags = {s["satellite_id"]: s["needs_update"] for r in body["rooms"] for s in r["satellites"]}
    # The old app shares the kitchen; the den's only device has nobody to share with.
    assert flags[h["b"]] is True and flags[h["a"]] is False and flags[h["c"]] is False


# ─── "Heard best by …" ────────────────────────────────────────────────────


@requires_db
async def test_turns_say_which_mic_heard_best(house) -> None:
    from web.backend.api.satellites import list_conversations

    h = house
    k = h["kitchen"]
    async with session_scope() as s:
        ids = []
        for owner, mic in ((h["a"], h["b"]), (h["a"], h["a"]), (None, None)):
            ids.append((await s.execute(text(
                "INSERT INTO conversation_log (room_id, user_text, assistant_text, "
                "satellite_id, mic_satellite_id) VALUES (:r, 'hi', 'hello', :o, :m) RETURNING id"
            ), {"r": k, "o": owner, "m": mic})).scalar())
    try:
        turns = {t.id: t for t in await list_conversations(k, limit=10, request=None)}
        moved, own, old = (turns[i] for i in ids)
        assert (moved.room_name, moved.heard_by) == ("Kitchen", "Kitchen phone")
        assert (moved.satellite_id, moved.mic_satellite_id) == (h["a"], h["b"])
        # Several satellites in the room: its own mic still says so (unnamed).
        assert own.heard_by == "Satellite for the Kitchen"
        assert old.heard_by is None and old.mic_satellite_id is None
    finally:
        async with session_scope() as s:
            await s.execute(text("DELETE FROM conversation_log WHERE id = ANY(:i)"), {"i": ids})


# ─── "Add a satellite to the Kitchen" ─────────────────────────────────────


def test_the_payload_carries_in_and_old_parsers_ignore_it() -> None:
    p = enrolment.build(CORE, FP, "Kitchen 2", in_room="kitchen")
    assert p.endswith("&room=Kitchen%202&in=kitchen")
    e = enrolment.parse(p)
    assert (e.room, e.room_id, e.in_room) == ("Kitchen 2", "kitchen_2", "kitchen")
    assert e.to_json()["in_room"] == "kitchen"
    # An old payload: no in_room, and the shared fixture's shape unchanged.
    old = enrolment.parse(enrolment.build(CORE, FP, "Kitchen"))
    assert old.in_room is None and "in_room" not in old.to_json()
    # A value that is no room id is ignored like an unknown key.
    odd = enrolment.parse(enrolment.build(CORE, FP, "Kitchen 2") + "&in=Not%20an%20id")
    assert odd.in_room is None and odd.room_id == "kitchen_2"
    with pytest.raises(enrolment.EnrolmentError):
        enrolment.build(CORE, FP, "Kitchen 2", in_room="Kitchen")


def test_a_pc_enrolled_into_a_room_asks_for_it() -> None:
    e = enrolment.parse(enrolment.build(CORE, FP, "Kitchen 2", in_room="kitchen"))
    assert pc_install.config_changes(e, "automatic")["satellite.requested_room"] == "kitchen"
    e = enrolment.parse(enrolment.build(CORE, FP, "Office"))
    assert pc_install.config_changes(e, "automatic")["satellite.requested_room"] == ""


@requires_db
async def test_an_invitation_names_the_device_and_preselects_the_room(
        house, admin, clean_settings) -> None:  # noqa: F811
    h = house
    k = h["kitchen"]
    async with _core() as c:
        r = await c.post(f"/v1/admin/rooms/{k}/invite", json={}, headers=admin)
        assert r.status_code == 200, r.text
        made = r.json()
        assert made["room_id"] == k and made["room_name"] == "Kitchen"
        assert made["name"].startswith("Kitchen ")
        sid = made["satellite_id"]
        assert sid == enrolment.room_id_for(made["name"])
        # The same code again: the same name.
        again = (await c.post(f"/v1/admin/rooms/{k}/invite", json={"name": made["name"]},
                              headers=admin)).json()
        assert again["satellite_id"] == sid
        # A second new satellite gets the next name.
        other = (await c.post(f"/v1/admin/rooms/{k}/invite", json={}, headers=admin)).json()
        assert other["satellite_id"] != sid
        r = await c.post("/v1/admin/rooms/rr_nowhere/invite", json={}, headers=admin)
        assert r.status_code == 404
        # An older app ignores `in=` and parks as `sid` asking for nothing:
        # the invitation fills in the room, and approval files it there.
        async with session_scope() as s:
            asked = await streaming._invited_room(s, sid)
            assert asked == k
            await SatelliteApprovalRepository(s).request(
                sid, token_hash="h" * 64, code="123456", requested_room=asked)
        r = await c.post(f"/v1/admin/satellites/approvals/{sid}/approve", headers=admin,
                         json={"code": "123456"})
        assert r.status_code == 200, r.text
        assert r.json()["into_room_id"] == k
    async with session_scope() as s:
        await s.execute(text("DELETE FROM satellite_pairings WHERE satellite_id = :i"), {"i": sid})
        await s.execute(text("DELETE FROM satellite_approvals WHERE satellite_id = :i"), {"i": sid})
        await s.execute(text("DELETE FROM satellites WHERE satellite_id = :i"), {"i": sid})
        await s.execute(text("DELETE FROM rooms WHERE room_id = :i"), {"i": sid})


@requires_db
async def test_no_invitation_means_no_room(clean_settings) -> None:
    async with session_scope() as s:
        assert await streaming._invited_room(s, f"nobody_{uuid.uuid4().hex[:6]}") is None
