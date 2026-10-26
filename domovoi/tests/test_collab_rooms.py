"""Co-editing rooms (web/backend/collab): convergence, read-only sessions,
seeding, persistence across a restart, the save request, version history
and restore, rebuilding from a file changed outside the editor, an offline
phone's stale copy, limits, and retention.

Driven without sockets: each "client" is a pycrdt document
(``collab_sim.SimClient``) wired to a room through a Peer whose sends land
in a list. Postgres is the lane's (``requires_db``): persistence is the
point of half of these.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from domovoi.config import settings
from domovoi.db.session import engine
from domovoi.tests.collab_sim import SimClient, control
from domovoi.tests.conftest import requires_db
from web.backend.collab import content, rooms, store
from web.backend.collab.protocol import AwarenessEntry, awareness_message, encode_awareness_update

pytestmark = requires_db


@pytest_asyncio.fixture
async def collab_db():
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE collab_snapshots, collab_updates, collab_docs CASCADE"))
    yield
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE collab_snapshots, collab_updates, collab_docs CASCADE"))


@pytest.fixture
def docs(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    (tmp_path / "plan.md").write_text("# Plan\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def manager(monkeypatch):
    m = rooms.RoomManager()
    monkeypatch.setattr(rooms, "MANAGER", m)
    yield m


def make_peer(room, name="Ana", *, readonly=False, epoch=None, can_save=False):
    sent: list[bytes] = []
    closes: list[tuple[int, str]] = []

    async def send_bytes(b: bytes) -> None:
        sent.append(b)

    async def close(code: int, reason: str) -> None:
        closes.append((code, reason))

    ep = room.epoch if epoch is None else epoch
    p = rooms.Peer(send_bytes=send_bytes, close=close, name=name, color="#123456",
                   readonly=readonly, source=f"10.0.0.{len(name)}", epoch=ep,
                   archive=ep != room.epoch, session=name)
    p.can_save = can_save
    p.writer = asyncio.get_running_loop().create_task(p.run_writer())
    p.sent = sent
    p.closes = closes
    return p


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def pump(room, *pairs) -> None:
    """Deliver every queued server frame to its client and every answer
    back, until nothing moves."""
    for _ in range(20):
        await settle()
        moved = False
        for peer, sim in pairs:
            while peer.sent:
                moved = True
                for reply in sim.feed(peer.sent.pop(0)):
                    await room.handle(peer, reply)
        if not moved:
            return


async def connect(room, sim, peer):
    room.join(peer)
    for f in sim.hello_frames():
        await room.handle(peer, f)
    await pump(room, (peer, sim))


async def open_room(manager, docs, name="plan.md"):
    row = await store.get_or_create(name)
    return await manager.open(row, docs / name)


async def seed(room, sim, peer, text_value="# Plan\n"):
    """The seeding client puts the file's content in and says so."""
    assert sim.last_control("hello")["seed"] is True
    await room.handle(peer, sim.edit_text(0, text_value))
    await room.handle(peer, control({"t": "seeded"}))
    await pump(room, (peer, sim))


# ─── Sync ────────────────────────────────────────────────────────────


async def test_two_clients_converge_through_the_room(collab_db, docs, manager):
    room = await open_room(manager, docs)
    a, b = SimClient(1), SimClient(2)
    pa, pb = make_peer(room, "Ana"), make_peer(room, "Ben")
    await connect(room, a, pa)
    await seed(room, a, pa)
    await connect(room, b, pb)
    assert b.text() == "# Plan\n"
    assert b.last_control("hello")["seed"] is False and b.last_control("hello")["ready"] is True

    # Concurrent edits from both sides end up identical everywhere.
    ua = a.edit_text(7, "Ana was here. ")
    ub = b.edit_text(0, "Draft: ")
    await room.handle(pa, ua)
    await room.handle(pb, ub)
    await pump(room, (pa, a), (pb, b))
    server = str(room.ydoc.get("text", type=content.Text))
    assert a.text() == b.text() == server
    assert "Ana was here." in server and server.startswith("Draft: ")


async def test_a_read_only_session_sees_edits_but_its_own_go_nowhere(collab_db, docs, manager):
    room = await open_room(manager, docs)
    w, r = SimClient(1), SimClient(2)
    pw, pr = make_peer(room, "Ana"), make_peer(room, "Viewer", readonly=True)
    await connect(room, w, pw)
    await seed(room, w, pw)
    await connect(room, r, pr)
    assert r.last_control("hello")["readonly"] is True
    await room.handle(pr, r.edit_text(0, "vandal "))
    await room.handle(pw, w.edit_text(0, "ok "))
    await pump(room, (pw, w), (pr, r))
    assert str(room.ydoc.get("text", type=content.Text)) == "ok # Plan\n"
    assert "vandal" not in w.text()


async def test_a_viewer_never_gets_the_seeding_lease(collab_db, docs, manager):
    room = await open_room(manager, docs)
    r, w = SimClient(1), SimClient(2)
    pr = make_peer(room, "Viewer", readonly=True)
    await connect(room, r, pr)
    assert r.last_control("hello")["seed"] is False
    assert r.last_control("hello")["ready"] is False
    pw = make_peer(room, "Ana")
    await connect(room, w, pw)
    assert w.last_control("hello")["seed"] is True
    await seed(room, w, pw)
    await pump(room, (pr, r), (pw, w))
    assert r.last_control("ready") is not None
    assert r.text() == "# Plan\n"


async def test_the_lease_passes_on_when_the_seeder_leaves_first(collab_db, docs, manager):
    room = await open_room(manager, docs)
    a, b = SimClient(1), SimClient(2)
    pa, pb = make_peer(room, "Ana"), make_peer(room, "Ben")
    await connect(room, a, pa)
    await connect(room, b, pb)
    assert b.last_control("hello")["seed"] is False
    room.leave(pa)
    await pump(room, (pb, b))
    assert b.last_control("seed") is not None
    await room.handle(pb, b.edit_text(0, "# Plan\n"))
    await room.handle(pb, control({"t": "seeded"}))
    assert room.seeded


async def test_a_client_that_cannot_load_the_file_hands_the_seed_on(collab_db, docs, manager):
    room = await open_room(manager, docs)
    a, b = SimClient(1), SimClient(2)
    pa, pb = make_peer(room, "Ana"), make_peer(room, "Ben")
    await connect(room, a, pa)
    await connect(room, b, pb)
    await room.handle(pa, control({"t": "seed-failed"}))
    await pump(room, (pa, a), (pb, b))
    assert room.seed_peer is pb and b.last_control("seed") is not None


async def test_presence_is_relayed_and_withdrawn_when_a_socket_drops(collab_db, docs, manager):
    room = await open_room(manager, docs)
    a, b = SimClient(11), SimClient(22)
    pa, pb = make_peer(room, "Ana"), make_peer(room, "Ben")
    await connect(room, a, pa)
    await connect(room, b, pb)
    await room.handle(pa, a.presence_frame({"user": {"name": "Ana", "color": "#d9480f"}}))
    await pump(room, (pa, a), (pb, b))
    assert 11 in b.presence and 11 in a.presence   # the sender hears itself too
    room.leave(pa)
    await pump(room, (pb, b))
    assert 11 not in b.presence


async def test_presence_for_someone_else_s_client_id_goes_nowhere(collab_db, docs, manager):
    """The stock provider re-sends other people's presence as it hears it:
    a stale copy is dropped quietly, and a NEWER entry for a client id that
    another socket owns is not taken either (it would let one tab rename
    another person)."""
    room = await open_room(manager, docs)
    a, b = SimClient(11), SimClient(22)
    pa, pb = make_peer(room, "Ana"), make_peer(room, "Ben")
    await connect(room, a, pa)
    await connect(room, b, pb)
    await room.handle(pa, a.presence_frame({"user": {"name": "Ana"}}))
    await pump(room, (pa, a), (pb, b))
    relayed = awareness_message(encode_awareness_update([AwarenessEntry(11, 1, '{"user":{"name":"Ana"}}')]))
    forged = awareness_message(encode_awareness_update([AwarenessEntry(11, 9, '{"user":{"name":"Ben pretending"}}')]))
    await room.handle(pb, relayed)
    await room.handle(pb, forged)
    await pump(room, (pa, a), (pb, b))
    assert not pb.closes
    assert "Ana" in a.presence[11] and "pretending" not in a.presence[11]
    assert room.awareness[11].clock == 1


async def test_a_stale_relay_cannot_bring_back_someone_who_left(collab_db, docs, manager):
    room = await open_room(manager, docs)
    a, b = SimClient(11), SimClient(22)
    pa, pb = make_peer(room, "Ana"), make_peer(room, "Ben")
    await connect(room, a, pa)
    await connect(room, b, pb)
    await room.handle(pa, a.presence_frame({"user": {"name": "Ana"}}))
    await room.handle(pb, b.presence_frame({"user": {"name": "Ben"}}))   # its own, first
    await pump(room, (pa, a), (pb, b))
    room.leave(pa)
    await room.handle(pb, awareness_message(encode_awareness_update(
        [AwarenessEntry(11, 1, '{"user":{"name":"Ana"}}')])))
    await pump(room, (pb, b))
    assert 11 not in room.awareness and 11 not in b.presence
    assert not pb.closes            # relaying is what the stock client does: no penalty


# ─── Persistence ─────────────────────────────────────────────────────


async def test_the_state_survives_a_restart(collab_db, docs, manager, monkeypatch):
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await seed(room, a, pa)
    for i in range(5):
        await room.handle(pa, a.edit_text(0, f"{i}"))
    await room.flush()                      # what the tick does each second
    expected = a.text()
    # The process dies: no shut(), no compaction. The rows are all there is.
    fresh = rooms.RoomManager()
    monkeypatch.setattr(rooms, "MANAGER", fresh)
    row = await store.get_by_path("plan.md")
    again = await fresh.open(row, docs / "plan.md")
    assert str(again.ydoc.get("text", type=content.Text)) == expected
    assert again.seeded and again.rev == room.rev
    # And a client reconnecting with nothing local gets all of it.
    c = SimClient(3)
    pc = make_peer(again, "Cy")
    await connect(again, c, pc)
    assert c.text() == expected


async def test_compaction_folds_the_rows_into_the_state(collab_db, docs, manager, monkeypatch):
    monkeypatch.setattr(rooms, "COMPACT_ROWS", 3)
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await seed(room, a, pa)
    for i in range(4):
        await room.handle(pa, a.edit_text(0, "x"))
        await room.flush()
    async with engine.begin() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM collab_updates"))).scalar()
    assert n < 3
    row = await store.get_by_path("plan.md")
    assert row.state and content.doc_from_update(row.state).get("text", type=content.Text)


async def test_last_one_out_flushes_compacts_and_keeps_a_version(collab_db, docs, manager):
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await seed(room, a, pa)
    await room.handle(pa, a.edit_text(0, "Hello "))
    room.leave(pa)
    await manager.release(room)
    assert room.closed and manager.rooms == {}
    snaps = await store.list_snapshots(room.doc_id)
    assert [s.kind for s in snaps][:2] == ["leave", "from_file"]
    assert snaps[0].editors == ["Ana"]
    row = await store.get_by_path("plan.md")
    assert str(content.doc_from_update(row.state).get("text", type=content.Text)) == "Hello # Plan\n"


# ─── Saving the clean file ───────────────────────────────────────────


async def test_the_last_editor_is_asked_to_save_once_typing_stops(collab_db, docs, manager, monkeypatch):
    monkeypatch.setattr(rooms, "SAVE_IDLE_SEC", 0.0)
    room = await open_room(manager, docs)
    a, b = SimClient(1), SimClient(2)
    pa, pb = make_peer(room, "Ana", can_save=True), make_peer(room, "Ben", can_save=True)
    await connect(room, a, pa)
    await seed(room, a, pa)
    await connect(room, b, pb)
    await room.handle(pa, a.edit_text(0, "a"))
    await asyncio.sleep(0.05)       # Windows' monotonic clock ticks every ~16 ms
    await room.handle(pb, b.edit_text(0, "b"))
    await room.tick()
    await pump(room, (pa, a), (pb, b))
    assert b.last_control("save") == {"t": "save", "rev": room.rev, "epoch": room.epoch,
                                      "doc": room.doc_id}
    assert a.last_control("save") is None
    # The save lands (what note_file_saved does after PUT /text).
    (docs / "plan.md").write_text("ba# Plan\n", encoding="utf-8")
    room.note_saved(room.rev, rooms.file_fingerprint(docs / "plan.md"))
    await room.tick()
    await pump(room, (pa, a), (pb, b))
    assert room.saved_rev == room.rev
    assert len([c for c in a.controls + b.controls if c["t"] == "save"]) == 1


async def test_a_file_left_behind_is_saved_by_the_next_editor(collab_db, docs, manager, monkeypatch):
    monkeypatch.setattr(rooms, "SAVE_IDLE_SEC", 0.0)
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")          # cannot save (no editor attached)
    await connect(room, a, pa)
    await seed(room, a, pa)
    await room.handle(pa, a.edit_text(0, "unsaved "))
    room.leave(pa)
    await manager.release(room)
    row = await store.get_by_path("plan.md")
    assert row.rev > row.saved_rev
    room2 = await manager.open(row, docs / "plan.md")
    b = SimClient(2)
    pb = make_peer(room2, "Ben", can_save=True)
    await connect(room2, b, pb)
    await room2.tick()
    await pump(room2, (pb, b))
    assert b.last_control("save") is not None


# ─── History and restore ─────────────────────────────────────────────


async def test_restore_comes_back_as_a_new_change_everyone_receives(collab_db, docs, manager):
    room = await open_room(manager, docs)
    a, b = SimClient(1), SimClient(2)
    pa, pb = make_peer(room, "Ana"), make_peer(room, "Ben")
    await connect(room, a, pa)
    await seed(room, a, pa)
    await connect(room, b, pb)
    await room.handle(pa, a.edit_text(7, "Version one.\n"))
    v1 = await room.snapshot("auto")
    await room.handle(pb, b.edit_text(0, "WRONG "))
    await pump(room, (pa, a), (pb, b))
    old = await store.get_snapshot(room.doc_id, v1)
    sid = await room.restore(old.state, "Ben")
    await pump(room, (pa, a), (pb, b))
    assert a.text() == b.text() == "# Plan\nVersion one.\n"
    kinds = [s.kind for s in await store.list_snapshots(room.doc_id)]
    assert kinds[:3] == ["restored", "before_restore", "auto"]
    # Undoable: the version before the restore still has the wrong text.
    before = await store.get_snapshot(room.doc_id, (await store.list_snapshots(room.doc_id))[1].id)
    assert "WRONG" in content.preview_text(content.doc_from_update(before.state))
    assert a.last_control("restored") == {"t": "restored", "by": "Ben"}
    assert sid is not None


async def test_restore_of_rich_text_keeps_elements_attributes_and_marks(collab_db, docs, manager):
    (docs / "letter.html").write_text("<p>x</p>", encoding="utf-8")
    room = await open_room(manager, docs, "letter.html")
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await room.handle(pa, a.append_paragraph("Dear Sam"))
    await room.handle(pa, control({"t": "seeded"}))
    v = room.ydoc.get_update()
    await room.handle(pa, a.append_paragraph("delete me"))
    await room.restore(v, "Ana")
    await pump(room, (pa, a))
    assert a.xml() == "<paragraph>Dear Sam</paragraph>"


# ─── The file changed outside the editor ─────────────────────────────


async def test_an_outside_change_rebuilds_from_the_file_and_resets_editors(collab_db, docs, manager):
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await seed(room, a, pa)
    await room.handle(pa, a.edit_text(0, "co-edited "))
    old_epoch = room.epoch
    (docs / "plan.md").write_text("# Plan, edited in another app\n", encoding="utf-8")
    room.rebuild_wanted = True
    await room.tick()
    await settle()
    for frame in pa.sent:
        a.feed(frame)
    assert a.last_control("reset")["epoch"] == old_epoch + 1
    assert pa.closes[0][0] == rooms.CLOSE_RESET
    row = await store.get_by_path("plan.md")
    assert row.epoch == old_epoch + 1 and row.state is None and not row.seeded
    kinds = [s.kind for s in await store.list_snapshots(row.doc_id)]
    assert "before_rebuild" in kinds
    assert manager.rooms == {}


async def test_a_save_without_the_header_counts_as_an_outside_change(collab_db, docs, manager):
    from web.backend.api import collab as collab_api

    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await seed(room, a, pa)

    class Req:
        headers: dict = {}

    (docs / "plan.md").write_text("changed by an old editor", encoding="utf-8")
    await collab_api.note_file_saved(Req(), docs / "plan.md")
    assert room.rebuild_wanted


async def test_a_change_while_nobody_had_it_open_rebuilds_on_open(collab_db, docs, manager):
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await seed(room, a, pa)
    room.leave(pa)
    await manager.release(room)
    (docs / "plan.md").write_text("uploaded over it\n", encoding="utf-8")
    row = await store.get_by_path("plan.md")
    again = await manager.open(row, docs / "plan.md")
    assert again.epoch == row.epoch + 1 and not again.seeded and content.is_empty(again.ydoc)


async def test_an_offline_phone_s_stale_copy_is_kept_not_merged(collab_db, docs, manager):
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await seed(room, a, pa)
    # The phone goes offline and keeps typing.
    phone = SimClient(9)
    phone.doc.apply_update(room.ydoc.get_update())
    stale_epoch = room.epoch
    phone.edit_text(0, "typed on the bus ")
    # Meanwhile the file is replaced and the room rebuilt.
    (docs / "plan.md").write_text("# Plan v2\n", encoding="utf-8")
    room.rebuild_wanted = True
    await room.tick()
    row = await store.get_by_path("plan.md")
    room2 = await manager.open(row, docs / "plan.md")
    # The phone reconnects with its old epoch.
    pp = make_peer(room2, "Phone", epoch=stale_epoch)
    await connect(room2, phone, pp)
    assert phone.last_control("reset")["kept"] is True
    assert pp.closes[0][0] == rooms.CLOSE_RESET
    assert content.is_empty(room2.ydoc)          # nothing old leaked in
    snaps = await store.list_snapshots(row.doc_id)
    copy = next(s for s in snaps if s.kind == "offline_copy")
    assert copy.editors == ["Phone"]
    full = await store.get_snapshot(row.doc_id, copy.id)
    assert "typed on the bus" in content.preview_text(content.doc_from_update(full.state))


async def test_a_view_only_stale_copy_adds_no_version(collab_db, docs, manager):
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await seed(room, a, pa)
    viewer = SimClient(9)
    viewer.doc.apply_update(room.ydoc.get_update())
    viewer.edit_text(0, "junk ")
    pv = rooms.Peer(**{k: getattr(make_peer(room, "Viewer", readonly=True), k)
                       for k in ("send_bytes", "close", "name", "color", "readonly", "source")},
                    epoch=room.epoch - 1, archive=True, session="v")
    sent: list[bytes] = []

    async def sb(b):
        sent.append(b)

    pv.send_bytes = sb
    pv.writer = asyncio.get_running_loop().create_task(pv.run_writer())
    room.join(pv)
    for f in viewer.hello_frames():
        await room.handle(pv, f)
    await settle()
    for f in list(sent):
        for reply in viewer.feed(f):
            await room.handle(pv, reply)
    await settle()
    assert all(s.kind != "offline_copy" for s in await store.list_snapshots(room.doc_id))


# ─── Limits ──────────────────────────────────────────────────────────


async def test_an_oversized_message_closes_the_socket(collab_db, docs, manager, monkeypatch):
    monkeypatch.setattr(rooms, "MAX_MESSAGE_BYTES", 1000)
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await room.handle(pa, a.edit_text(0, "x" * 2000))
    await settle()
    assert pa.closes[0][0] == rooms.CLOSE_TOO_LARGE


async def test_too_many_messages_too_fast_closes_the_socket(collab_db, docs, manager, monkeypatch):
    monkeypatch.setattr(rooms, "RATE_BURST", 5.0)
    monkeypatch.setattr(rooms, "RATE_PER_SEC", 0.001)
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    for _ in range(10):
        await room.handle(pa, a.presence_frame({"user": {"name": "Ana"}}))
    await settle()
    assert pa.closes and pa.closes[0][0] == rooms.CLOSE_RATE


async def test_a_document_past_its_size_limit_takes_no_more(collab_db, docs, manager, monkeypatch):
    monkeypatch.setattr(rooms, "MAX_DOC_BYTES", 500)
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await room.handle(pa, a.edit_text(0, "y" * 800))
    await settle()
    assert content.is_empty(room.ydoc)
    assert pa.closes[0][0] == rooms.CLOSE_TOO_LARGE


async def test_garbage_is_refused_without_harm(collab_db, docs, manager):
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await room.handle(pa, b"\x00\x02\x05\xff\xff")   # an update that isn't one
    await settle()
    assert pa.closes[0][0] == rooms.CLOSE_BAD_MESSAGE
    b = SimClient(2)
    pb = make_peer(room, "Ben")
    await connect(room, b, pb)                          # the room still works
    assert b.synced


# ─── Retention ───────────────────────────────────────────────────────


def _snap(i, age, kind="auto", editors=("A",), size=10):
    now = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
    return store.SnapshotRow(id=i, doc_id="d", epoch=1, kind=kind, editors=list(editors),
                             size=size, created_at=now - age)


def test_retention_keeps_recent_versions_and_thins_older_ones():
    now = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
    snaps = [
        _snap(1, timedelta(minutes=5)),
        _snap(2, timedelta(minutes=50)),                         # both kept: < 2 h
        _snap(3, timedelta(hours=5, minutes=1), editors=("B",)),
        _snap(4, timedelta(hours=5, minutes=20), editors=("C",)),  # same hour as 3
        _snap(5, timedelta(days=20, hours=1)),
        _snap(6, timedelta(days=20, hours=3), editors=("D",)),    # same day as 5
        _snap(7, timedelta(days=10), kind="offline_copy"),        # thinned like others after 30 d? no: 10 d
    ]
    drop, inherit = store.plan_thinning(snaps, now)
    assert drop == {4, 6}
    assert inherit == {3: {"C"}, 5: {"D"}}


def test_retention_caps_the_count(monkeypatch):
    monkeypatch.setattr(store, "MAX_SNAPSHOTS", 3)
    now = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
    snaps = [_snap(i, timedelta(minutes=i)) for i in range(1, 6)]
    drop, _ = store.plan_thinning(snaps, now)
    assert drop == {4, 5}
