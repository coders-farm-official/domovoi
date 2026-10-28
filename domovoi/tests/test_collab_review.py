"""Security reviews of wf/doc-collab (2026-10-26): the findings, as tests
that pin the fixes.

Round 2 changed the design: pycrdt runs only in a worker subprocess per
open document (web/backend/collab/worker.py), which checks nesting after
every apply; the web process relays bytes only once the worker has taken
them, and a worker that crashes or stalls is replaced from Postgres.
guard.py is a cheap linear pre-filter. So the crash tests here show the
WEB process (this test process, which runs the rooms) carrying on.

Round 1   F1 a tiny update crashed the web process (yrs recursion)
          F2 saves landing out of order; F3 offline-copy flooding;
          F4 reopening during last-one-out; M3 joining a room being shut;
          the LOW items (presence, caps, restore, the save lock)
Round 2   clock wrap (yrs keeps 32 bits), the quadratic resolver,
          legitimate updates refused (out of order, partial overlap),
          a forced worker crash and a stall, poisoned stored state,
          close codes, seeded fuzz with three Yjs clients, latency
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from pycrdt import Doc, Text, XmlElement, XmlFragment, write_var_uint

from domovoi.tests.collab_sim import SimClient, control
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_collab_rooms import (  # noqa: F401 — fixtures and helpers
    collab_db, connect, docs, make_peer, manager, open_room, pump, seed, server_doc,
    server_text, settle,
)
from web.backend.collab import content, guard, rooms, store, workers
from web.backend.collab.protocol import ROOT_KINDS, var_bytes

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "web" / "static"
NODE = shutil.which("node")
W = 2 ** 32

pytestmark = requires_db


def _vu(n: int) -> bytes:
    return bytes(write_var_uint(n))


def deep_any_update(depth: int) -> bytes:
    """One ContentAny item in root text 'text' holding [[[[...null...]]]]
    nested ``depth`` deep (~2 bytes per level)."""
    any_ = bytes([117, 1]) * depth + bytes([126])
    return (_vu(1) + _vu(1) + _vu(77) + _vu(0) + bytes([8]) + _vu(1) + _vu(4) + b"text"
            + _vu(1) + any_ + _vu(0))


def deep_json_update(depth: int) -> bytes:
    js = "[" * depth + "]" * depth
    return (_vu(1) + _vu(1) + _vu(78) + _vu(0) + bytes([5]) + _vu(1) + _vu(4) + b"text"
            + var_bytes(js.encode()) + _vu(0))


def deep_xml_update(depth: int, client: int = 55) -> bytes:
    d = Doc(client_id=client)
    node = d.get("default", type=XmlFragment)
    for _ in range(depth):
        node = node.children.append(XmlElement("p"))
    return d.get_update()


def delete_first_struct_of(client: int) -> bytes:
    """No structs; a delete set removing (client, 0)."""
    return _vu(0) + _vu(1) + _vu(client) + _vu(1) + _vu(0) + _vu(1)


def sync_update(update: bytes) -> bytes:
    return bytes([0, 2]) + var_bytes(update)


def sync_step2(update: bytes) -> bytes:
    return bytes([0, 1]) + var_bytes(update)


async def seeded_room(manager, docs, name="Ana"):
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, name)
    await connect(room, a, pa)
    await seed(room, a, pa)
    return room, a, pa


@pytest.fixture
def test_ops(monkeypatch):
    """Lets this test's workers take the 'crash' and 'sleep' ops."""
    monkeypatch.setenv("DOMOVOI_COLLAB_WORKER_TEST", "1")


# ─── The pre-filter (guard.py) ───────────────────────────────────────


def test_the_web_process_never_loads_pycrdt():
    code = ("import sys; sys.path.insert(0, %r); import web.backend.api.collab, "
            "web.backend.collab.rooms; print('pycrdt' in sys.modules)" % str(REPO))
    r = subprocess.run([__import__("sys").executable, "-c", code], capture_output=True,
                       text=True, timeout=120, cwd=str(REPO))
    assert r.stdout.strip().endswith("False"), (r.stdout, r.stderr[-500:])


def test_a_real_yjs_document_passes_the_prefilter():
    real = bytes.fromhex(json.loads(_fuzz(seed=7, steps=60))["final"]) if NODE else None
    if real is not None:
        info = guard.check(real, ROOT_KINDS.keys())
        assert info.has_types and info.structs > 0


_REFUSED = [
    # yrs keeps clocks and lengths in 32 bits (round 2, wrap.py / vint.py)
    (_vu(1) + _vu(1) + _vu(7) + _vu(W) + bytes([4]) + _vu(1) + _vu(4) + b"text"
     + var_bytes(b"a") + _vu(0), "clock 2^32"),
    (_vu(1) + _vu(1) + _vu(7) + _vu(W - 1) + bytes([4]) + _vu(1) + _vu(4) + b"text"
     + var_bytes(b"ab") + _vu(0), "clock + length reaches 2^32"),
    (_vu(1) + _vu(1) + _vu(8) + _vu(0) + bytes([1]) + _vu(1) + _vu(4) + b"text"
     + _vu(W + 2) + _vu(0), "a length past 32 bits"),
    (_vu(1) + _vu(1) + _vu(8) + _vu(0) + bytes([0x84]) + _vu(9) + _vu(W + 1)
     + var_bytes(b"a") + _vu(0), "an origin clock past 32 bits"),
    (_vu(1) + _vu(1) + _vu(9) + _vu(0) + bytes([4]) + _vu(2) + var_bytes(b"a") + _vu(0),
     "a parent flag that is neither 0 nor 1"),
    (_vu(0) + _vu(1) + _vu(5) + _vu(1) + _vu(W - 1) + _vu(5), "a delete past 32 bits"),
    (deep_any_update(guard.MAX_VALUE_DEPTH + 1), "value nesting"),
    (deep_any_update(10000), "value nesting (the round 1 crash)"),
    (deep_json_update(guard.MAX_VALUE_DEPTH + 1), "JSON nesting"),
    (_vu(1) + _vu(1) + _vu(9) + _vu(0) + bytes([4]) + _vu(1) + _vu(4) + b"evil"
     + var_bytes(b"a") + _vu(0), "a root no editor writes"),
    (_vu(1) + _vu(1) + _vu(9) + _vu(0) + bytes([9]) + _vu(1) + _vu(4) + b"text"
     + var_bytes(b"guid") + bytes([126]) + _vu(0), "a sub-document"),
    (_vu(1) + _vu(1) + _vu(9) + _vu(0) + bytes([4]) + _vu(1) + _vu(4) + b"text"
     + var_bytes(b"a") + _vu(0) + b"\x00", "trailing bytes"),
]


@pytest.mark.parametrize("case", range(len(_REFUSED)), ids=[why for _, why in _REFUSED])
def test_the_prefilter_refuses(case):
    blob, _why = _REFUSED[case]
    with pytest.raises(guard.UpdateRejected):
        guard.check(blob, ROOT_KINDS.keys())


def test_client_ids_may_go_to_2_53_like_yjs():
    u = (_vu(1) + _vu(1) + _vu(2 ** 52) + _vu(0) + bytes([4]) + _vu(1) + _vu(4) + b"text"
         + var_bytes(b"a") + _vu(0))
    assert guard.check(u, ROOT_KINDS.keys()).ends == {2 ** 52: 1}


def test_the_prefilter_is_linear():
    """Round 2 quad.py: an 80 KB reverse chain froze the old resolver for
    8.6 s. The pre-filter resolves nothing: one pass."""
    def chain(n):
        parts = [_vu(n)]
        for k in range(n):
            client = 1000 + k
            parts.append(_vu(1) + _vu(client) + _vu(0))
            if k == n - 1:
                parts.append(bytes([4]) + _vu(1) + _vu(4) + b"text" + var_bytes(b"a"))
            else:
                parts.append(bytes([0x84]) + _vu(client + 1) + _vu(0) + var_bytes(b"a"))
        parts.append(_vu(0))
        return b"".join(parts)

    times = {}
    for n in (2000, 8000):
        u = chain(n)
        t = time.perf_counter()
        guard.check(u, ROOT_KINDS.keys())
        times[n] = time.perf_counter() - t
    assert times[8000] < 0.5
    assert times[8000] < times[2000] * 8 + 0.05      # ~4x, not ~16x


# ─── F1: deep nesting reaches only a worker, which refuses it ────────


async def test_F1_deep_values_are_refused_before_any_worker(collab_db, docs, manager):
    room, a, pa = await seeded_room(manager, docs)
    pid = room.worker.proc.pid
    p = make_peer(room, "Mallory")
    room.join(p)
    await room.handle(p, sync_update(deep_any_update(10000)))
    await settle()
    assert p.closes and p.closes[0][0] == rooms.CLOSE_BAD_MESSAGE
    assert room.worker.proc.pid == pid               # the pre-filter caught it
    await room.handle(pa, a.edit_text(0, "still fine "))
    assert (await server_text(room)).startswith("still fine ")


async def test_F1_a_deep_tree_is_refused_by_the_worker_and_never_stays(collab_db, docs, manager):
    room, a, pa = await seeded_room(manager, docs)
    before = await server_text(room)
    p = make_peer(room, "Mallory")
    room.join(p)
    await room.handle(p, sync_update(deep_xml_update(20000)))
    await settle()
    assert p.closes and p.closes[0][0] == rooms.CLOSE_BAD_MESSAGE
    p2 = make_peer(room, "Mallory 2")
    room.join(p2)
    await room.handle(p2, sync_update(delete_first_struct_of(55)))   # what used to crash
    assert not room.worker.dead
    assert await server_text(room) == before
    assert "<p>" not in str((await server_doc(room)).get("default", type=XmlFragment))
    # The limit itself: 64 levels are a document, 65 are not.
    p3 = make_peer(room, "Deep but fine")
    room.join(p3)
    await room.handle(p3, sync_update(deep_xml_update(64, client=66)))
    assert not p3.closes


async def test_F1_the_offline_copy_path_reads_the_payload_first(collab_db, docs, manager):
    room, a, pa = await seeded_room(manager, docs)
    (docs / "plan.md").write_text("changed elsewhere\n", encoding="utf-8")
    room.rebuild_wanted = True
    await room.tick()
    row = await store.get_by_path("plan.md")
    room2 = await manager.open(row, docs / "plan.md")
    for payload in (deep_any_update(guard.MAX_VALUE_DEPTH + 1), deep_xml_update(500)):
        p = make_peer(room2, "Phone", epoch=row.epoch - 1)
        room2.join(p)
        await room2.handle(p, sync_step2(payload))
        await settle()
        assert p.closes and p.closes[0][0] == rooms.CLOSE_RESET
    snaps = await store.list_snapshots(row.doc_id)
    assert not any(s.kind == "offline_copy" for s in snaps)


# ─── Round 2: the clock-wrap differential (wrap.py) ──────────────────


def _section(client, clock, structs):
    return _vu(len(structs)) + _vu(client) + _vu(clock) + b"".join(structs)


def _el_root():
    return bytes([7]) + _vu(1) + _vu(7) + b"default" + _vu(3) + _vu(1) + b"p"


def _el_child(pc, pk):
    return bytes([7]) + _vu(0) + _vu(pc) + _vu(pk) + _vu(3) + _vu(1) + b"p"


def _upd(*sections):
    return _vu(len(sections)) + b"".join(sections) + b"\x00"


async def test_round2_clock_wrap_twins_never_reach_yrs(collab_db, docs, manager):
    """Twins at 2^32+k were indexed shallow by the old guard and landed deep
    in yrs; ~1000 small updates built a 30k-deep tree and the next delete
    crashed the process. Now the twins are refused at the pre-filter, and
    the chain cannot grow past 64 in the worker anyway."""
    room, a, pa = await seeded_room(manager, docs)
    ch = 60
    p = make_peer(room, "Chain")
    room.join(p)
    await room.handle(p, sync_update(_upd(_section(1000, 0, [_el_root()] + [
        _el_child(1000, k - 1) for k in range(1, ch)]))))
    assert not p.closes                          # 60 deep: a document
    twin = make_peer(room, "Twin")
    room.join(twin)
    await room.handle(twin, sync_update(_upd(_section(1000, W, [_el_root() for _ in range(ch)]))))
    await settle()
    assert twin.closes and twin.closes[0][0] == rooms.CLOSE_BAD_MESSAGE
    nxt = make_peer(room, "Next")
    room.join(nxt)                                # the next 60, hanging off the real chain
    await room.handle(nxt, sync_update(_upd(_section(1001, 0, [_el_child(1000, ch - 1)] + [
        _el_child(1001, k - 1) for k in range(1, ch)]))))
    await settle()
    assert nxt.closes and nxt.closes[0][0] == rooms.CLOSE_BAD_MESSAGE   # 120 deep: refused
    d = make_peer(room, "Delete")
    room.join(d)
    await room.handle(d, sync_update(delete_first_struct_of(1000)))
    assert not room.worker.dead
    await room.handle(pa, a.edit_text(0, "alive "))
    assert (await server_text(room)).startswith("alive ")


# ─── Round 2: no quadratic cost (quad.py, alt.js) ────────────────────


async def test_round2_a_long_reverse_chain_is_cheap_end_to_end(collab_db, docs, manager):
    room, a, pa = await seeded_room(manager, docs)
    n = 8000
    parts = [_vu(n)]
    for k in range(n):
        client = 5000 + k
        parts.append(_vu(1) + _vu(client) + _vu(0))
        if k == n - 1:
            parts.append(bytes([4]) + _vu(1) + _vu(4) + b"text" + var_bytes(b"a"))
        else:
            parts.append(bytes([0x84]) + _vu(client + 1) + _vu(0) + var_bytes(b"a"))
    parts.append(_vu(0))
    p = make_peer(room, "Chain")
    room.join(p)
    t = time.perf_counter()
    await room.handle(p, sync_update(b"".join(parts)))
    took = time.perf_counter() - t
    assert not p.closes
    assert took < 3.0, took


async def test_round2_an_ordinary_two_person_document_opens_quickly(collab_db, docs, manager):
    """alt.js: two people alternating keystrokes, ~56 KB of state: the old
    index_for took 5.3 s on every open and compaction."""
    a, b = Doc(client_id=5), Doc(client_id=9)
    ta, tb = a.get("text", type=Text), b.get("text", type=Text)
    for i in range(6000):
        src, dst, t = (a, b, ta) if i % 2 else (b, a, tb)
        sv = dst.get_state()
        t.insert(len(t), "w ")
        dst.apply_update(src.get_update(sv))
    state = a.get_update()
    assert len(state) > 40_000
    t0 = time.perf_counter()
    guard.check(state, ROOT_KINDS.keys())
    assert time.perf_counter() - t0 < 0.5
    row = await store.get_or_create("plan.md")
    await store.compact(row.doc_id, row.epoch, state, 1)
    t0 = time.perf_counter()
    room = await manager.open(row, docs / "plan.md")
    assert time.perf_counter() - t0 < 5.0
    async with room.lock:
        await room.compact()                     # compaction no longer re-indexes in Python
    assert (await server_text(room)).count("w ") == 6000


# ─── Round 2: legitimate updates that used to be refused ─────────────


async def test_round2_out_of_order_after_a_blip_is_accepted(collab_db, docs, manager):
    """ooo.js: the live keystroke typed right after reconnecting arrives
    before the sync step 2 that carries what it builds on. yrs holds it as
    pending and integrates it when the rest arrives."""
    room = await open_room(manager, docs)
    c = SimClient(21)
    pc = make_peer(room, "Ana")
    await connect(room, c, pc)
    await seed(room, c, pc, "ab")
    server_sv = c.doc.get_state()
    t = c.doc.get("text", type=Text)
    sv = c.doc.get_state()
    t.insert(2, "cd")                            # offline, unsent
    sv2 = c.doc.get_state()
    t.insert(4, "e")                             # live, sent first
    live = c.doc.get_update(sv2)
    step2 = c.doc.get_update(server_sv)
    await room.handle(pc, sync_update(live))
    await room.handle(pc, sync_step2(step2))
    await settle()
    assert not pc.closes
    assert await server_text(room) == "abcde"
    del sv


async def test_round2_a_whole_document_offline_copy_with_overlap_is_kept(collab_db, docs, manager):
    """S1: the offline copy arrives as the WHOLE document (it overlaps what
    the server already had for that epoch). The old guard skipped the
    partly known struct, refused the rest, and the client lost its edits."""
    room = await open_room(manager, docs)
    a = SimClient(11)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await seed(room, a, pa, "ab")
    old_epoch = room.epoch
    phone = SimClient(11)
    phone.doc.apply_update((await server_doc(room)).get_update())
    phone.edit_text(2, "cde")
    phone.edit_text(3, "Q")
    room.leave(pa)
    await manager.release(room)
    (docs / "plan.md").write_text("changed elsewhere\n", encoding="utf-8")
    row = await store.get_by_path("plan.md")
    room2 = await manager.open(row, docs / "plan.md")
    pp = make_peer(room2, "Phone", epoch=old_epoch)
    pp.device = "phone-1"
    room2.join(pp)
    await room2.handle(pp, sync_step2(phone.doc.get_update()))     # the whole document
    await settle()
    copy = next(s for s in await store.list_snapshots(row.doc_id) if s.kind == "offline_copy")
    full = await store.get_snapshot(row.doc_id, copy.id)
    assert str(content.doc_from_update(full.state).get("text", type=Text)) == "abcQde"


# ─── Round 2: crashes and stalls are contained ───────────────────────


async def test_round2_a_worker_crash_on_one_update_closes_only_its_sender(
        collab_db, docs, manager, monkeypatch, test_ops):
    room, a, pa = await seeded_room(manager, docs)
    b = SimClient(2)
    pb = make_peer(room, "Ben")
    await connect(room, b, pb)
    await room.handle(pa, a.edit_text(0, "kept "))
    old_pid = room.worker.proc.pid
    mallory = make_peer(room, "Mallory")
    mallory.source = "10.9.9.9"
    room.join(mallory)
    real_call = room.call

    async def crashing_call(op, blobs=(), args=None):
        if op == "apply" and blobs and b"BOOM" in blobs[0]:
            try:
                await room.worker.call("crash", {"code": 3})
            except workers.WorkerFailed:
                raise
        return await real_call(op, blobs, args)

    monkeypatch.setattr(room, "call", crashing_call)
    evil = SimClient(99)
    await room.handle(mallory, evil.edit_text(0, "BOOM"))
    await settle()
    assert mallory.closes and mallory.closes[0][0] == rooms.CLOSE_BAD_MESSAGE
    assert not pa.closes and not pb.closes                 # everyone else stays
    assert room.worker.proc.pid != old_pid                 # a new worker, from Postgres
    await pump(room, (pa, a), (pb, b))                     # step 1 → they resend what it lacks
    await room.handle(pb, b.edit_text(0, "after "))
    await pump(room, (pa, a), (pb, b))
    text = await server_text(room)
    assert "kept " in text and text.startswith("after ") and "BOOM" not in text
    assert a.text() == b.text() == text
    assert manager.crashes_by_source["10.9.9.9"]


async def test_round2_repeated_crashes_from_one_source_are_refused_for_a_while(manager):
    for _ in range(rooms.CRASHES_PER_SOURCE):
        manager.note_crash("10.6.6.6")
    assert manager.crash_blocked("10.6.6.6")
    assert not manager.crash_blocked("10.6.6.7")


async def test_round2_a_stalled_worker_is_killed_and_replaced(
        collab_db, docs, manager, monkeypatch, test_ops):
    room, a, pa = await seeded_room(manager, docs)
    monkeypatch.setitem(workers.TIMEOUTS, "sleep", 0.5)
    old = room.worker
    with pytest.raises(workers.WorkerFailed):
        async with room.lock:
            await room.call("sleep", args={"seconds": 30})
    assert old.dead
    async with room.lock:
        assert await room.recover()
    assert room.worker is not old and not room.worker.dead
    await room.handle(pa, a.edit_text(0, "on "))
    assert (await server_text(room)).startswith("on ")


async def test_round2_a_stored_state_that_breaks_workers_turns_co_editing_off(
        collab_db, docs, manager):
    from web.backend.api import collab as collab_api

    row = await store.get_or_create("plan.md")
    await store.compact(row.doc_id, row.epoch, deep_xml_update(200), 1)   # stored before any limits
    await store.mark_seeded(row.doc_id, row.epoch,
                            rooms.file_fingerprint(docs / "plan.md"), 1)
    with pytest.raises(rooms.RoomUnsafe):
        await manager.open(row, docs / "plan.md")
    row = await store.get_by_path("plan.md")
    assert row.unavailable
    with pytest.raises(rooms.RoomUnsafe):
        await manager.open(row, docs / "plan.md")          # stays off, no more workers wasted
    # Saved single-editor: the file changed, so co-editing comes back.
    (docs / "plan.md").write_text("# Plan, edited alone\n", encoding="utf-8")
    again = await collab_api._maybe_available_again(row, docs / "plan.md")
    assert not again.unavailable and again.epoch == row.epoch + 1
    room = await manager.open(again, docs / "plan.md")
    assert room.empty


def test_round2_close_codes_retry_where_the_server_is_at_fault():
    """y-websocket stops reconnecting on 4400-4499 only."""
    transient = (rooms.CLOSE_RETRY, rooms.CLOSE_BUSY, rooms.CLOSE_RATE, rooms.CLOSE_SHUTDOWN)
    final = (rooms.CLOSE_BAD_MESSAGE, rooms.CLOSE_UNAVAILABLE, rooms.CLOSE_UNAUTHORIZED,
             rooms.CLOSE_RESET, rooms.CLOSE_MOVED, rooms.CLOSE_TOO_LARGE)
    assert all(not 4400 <= c < 4500 for c in transient)
    assert all(4400 <= c < 4500 for c in final)


# ─── Round 2: seeded fuzz with three Yjs clients ─────────────────────


def _fuzz(seed: int, steps: int) -> str:
    r = subprocess.run([NODE, str(Path(__file__).with_name("collab_fuzz_gen.js")), str(STATIC),
                        str(seed), str(steps)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    return r.stdout


@pytest.mark.skipif(not NODE, reason="needs Node")
@pytest.mark.parametrize("fuzz_seed", [20261026, 7])
async def test_round2_fuzz_three_yjs_clients_are_never_refused_and_converge(
        collab_db, docs, manager, fuzz_seed):
    data = json.loads(_fuzz(fuzz_seed, 400))
    room = await open_room(manager, docs)
    peers = []
    for i in range(3):
        p = make_peer(room, f"P{i}")
        p.bucket.burst = p.bucket.tokens = 10 ** 6          # replayed at machine speed
        room.join(p)
        peers.append(p)
    room.seeded = True                                       # nobody seeds a fuzz run
    for i, hx in data["updates"]:
        await room.handle(peers[i], sync_update(bytes.fromhex(hx)))
    await settle()
    assert not any(p.closes for p in peers)
    expected = content.doc_from_update(bytes.fromhex(data["final"]))
    got = await server_doc(room)
    assert str(got.get("text", type=Text)) == str(expected.get("text", type=Text))
    assert (str(got.get("default", type=XmlFragment))
            == str(expected.get("default", type=XmlFragment)))


# ─── Round 2: what the worker hop costs ──────────────────────────────


async def test_round2_worker_hop_latency(collab_db, docs, manager, capsys):
    room, a, pa = await seeded_room(manager, docs)
    times = []
    for i in range(300):
        frame = a.edit_text(0, "x")
        t = time.perf_counter()
        await room.handle(pa, frame)
        times.append(time.perf_counter() - t)
    times.sort()
    mean = sum(times) / len(times)
    p50, p95, p99 = (times[int(len(times) * q)] for q in (0.5, 0.95, 0.99))
    with capsys.disabled():
        print(f"\nworker hop per keystroke: mean {mean * 1000:.2f} ms, p50 {p50 * 1000:.2f} ms, "
              f"p95 {p95 * 1000:.2f} ms, p99 {p99 * 1000:.2f} ms")
    assert p95 < 0.05


# ─── Round 1, F2: saves out of order ─────────────────────────────────


async def test_F2_an_older_save_landing_last_makes_the_file_behind_again(collab_db, docs, manager):
    room = await open_room(manager, docs)
    a, b = SimClient(1), SimClient(2)
    pa, pb = make_peer(room, "Ana", can_save=True), make_peer(room, "Bea", can_save=True)
    await connect(room, a, pa)
    await seed(room, a, pa)
    await connect(room, b, pb)
    await room.handle(pa, a.edit_text(0, "old "))
    rev_a = room.rev
    await room.handle(pb, b.edit_text(0, "NEW "))
    await pump(room, (pa, a), (pb, b))
    rev_b = room.rev
    f = docs / "plan.md"
    f.write_text(b.text(), encoding="utf-8")
    room.note_saved(rev_b, rooms.file_fingerprint(f))
    f.write_text("old # Plan\n", encoding="utf-8")
    room.note_saved(rev_a, rooms.file_fingerprint(f))
    assert room.saved_rev == rev_a < room.rev
    assert room.save_refusal(rev_a - 1) and room.save_refusal(room.rev + 1)
    assert room.save_refusal(room.rev) is None
    room.maybe_ask_for_save(10 ** 9)
    assert room.save_peer is not None


async def test_only_one_save_request_is_out_at_a_time(collab_db, docs, manager, monkeypatch):
    monkeypatch.setattr(rooms, "SAVE_IDLE_SEC", 0.0)
    room = await open_room(manager, docs)
    a, b = SimClient(1), SimClient(2)
    pa, pb = make_peer(room, "Ana", can_save=True), make_peer(room, "Bea", can_save=True)
    await connect(room, a, pa)
    await seed(room, a, pa)
    await connect(room, b, pb)
    await room.handle(pa, a.edit_text(0, "x"))
    room.maybe_ask_for_save(10 ** 6)
    first = room.save_peer
    await room.handle(pb, b.edit_text(0, "y"))
    room.maybe_ask_for_save(10 ** 6 + 1)
    assert room.save_peer is first


# ─── Round 1, F3: offline copies ─────────────────────────────────────


async def _stale_visit(room, name, device, epoch, text_):
    junk = SimClient()
    junk.edit_text(0, text_)
    p = make_peer(room, name, epoch=epoch)
    p.device = device
    room.join(p)
    await pump(room, (p, junk))
    room.leave(p)


async def test_F3_offline_copies_are_one_per_device_capped_and_never_evict_history(
        collab_db, docs, manager, monkeypatch):
    monkeypatch.setattr(store, "MAX_SNAPSHOTS", 5)
    room, a, pa = await seeded_room(manager, docs)
    old_epoch = room.epoch
    (docs / "plan.md").write_text("# Plan, edited elsewhere\n", encoding="utf-8")
    room.rebuild_wanted = True
    await room.tick()
    row = await store.get_by_path("plan.md")
    room2 = await manager.open(row, docs / "plan.md")
    await _stale_visit(room2, "Liar", "dev-liar", 987654, "junk")
    for i in range(5):
        await _stale_visit(room2, "Phone", "dev-phone", old_epoch, f"try {i} ")
    for i in range(store.MAX_OFFLINE_COPIES + 3):
        await _stale_visit(room2, f"D{i}", f"dev-{i}", old_epoch, "x" * 500)
    snaps = await store.list_snapshots(row.doc_id, limit=100000)
    copies = [s for s in snaps if s.kind == "offline_copy"]
    assert len(copies) == store.MAX_OFFLINE_COPIES
    assert all("Liar" not in s.editors for s in copies)
    real = [s.kind for s in snaps if s.kind != "offline_copy"]
    assert "from_file" in real and "before_rebuild" in real


# ─── Round 1, F4 / M3: shutting down while someone opens ─────────────


async def test_F4_reopening_during_the_last_one_out_waits_and_keeps_the_final_edit(
        collab_db, docs, manager, monkeypatch):
    room, a, pa = await seeded_room(manager, docs)
    await room.tick()
    await room.handle(pa, a.edit_text(0, "FINAL "))
    gate = asyncio.Event()
    real_append = store.append_updates

    async def slow_append(*args, **kw):
        await gate.wait()
        return await real_append(*args, **kw)

    monkeypatch.setattr(store, "append_updates", slow_append)
    room.leave(pa)
    shut = asyncio.create_task(manager.release(room))
    await settle()
    row = await store.get_by_path("plan.md")
    reopen = asyncio.create_task(manager.open(row, docs / "plan.md"))
    await settle()
    assert not reopen.done()
    gate.set()
    await shut
    room2 = await reopen
    assert room2 is not room
    assert "FINAL" in await server_text(room2)


async def test_M3_a_room_being_shut_is_never_handed_out(collab_db, docs, manager):
    room = await open_room(manager, docs)
    room.closing = True
    row = await store.get_by_path("plan.md")
    opening = asyncio.create_task(manager.open(row, docs / "plan.md"))
    await settle()
    assert not opening.done()
    await room.shut()
    assert (await opening) is not room


# ─── Round 1, LOW items ──────────────────────────────────────────────


async def test_presence_carries_the_server_s_name_and_a_reconnect_is_heard(collab_db, docs, manager):
    room = await open_room(manager, docs)
    a, b = SimClient(11), SimClient(22)
    pa, pb = make_peer(room, "Ana"), make_peer(room, "Ben")
    await connect(room, a, pa)
    await connect(room, b, pb)
    await room.handle(pa, a.presence_frame({"user": {"name": "The Admin", "color": "#000"},
                                            "cursor": {"x": 1}}, clock=5))
    await pump(room, (pa, a), (pb, b))
    assert '"name":"Ana"' in b.presence[11] and "The Admin" not in b.presence[11]
    assert '"cursor"' in b.presence[11]
    room.leave(pa)
    pa2 = make_peer(room, "Ana")
    a2 = SimClient(11)
    await connect(room, a2, pa2)
    await room.handle(pa2, a2.presence_frame({"user": {"name": "Ana"}}, clock=5))
    await pump(room, (pa2, a2), (pb, b))
    assert 11 in b.presence


async def test_a_restore_that_fails_leaves_the_document_alone(collab_db, docs, manager):
    room, a, pa = await seeded_room(manager, docs)
    await room.handle(pa, a.edit_text(0, "keep me "))
    before = await server_text(room)
    seen = a.updates_seen
    with pytest.raises(rooms.RestoreFailed):
        await room.restore(b"\x05not a yjs update", "Ana")
    await pump(room, (pa, a))
    assert await server_text(room) == before
    assert a.updates_seen == seen


async def test_view_only_sessions_have_their_own_small_cap(collab_db, docs, manager, monkeypatch):
    monkeypatch.setattr(rooms, "MAX_VIEWERS_PER_DOC", 1)
    room = await open_room(manager, docs)
    room.join(make_peer(room, "V1", readonly=True))
    assert not room.has_room_for(readonly=True)
    assert room.has_room_for(readonly=False)


async def test_view_only_sessions_cannot_open_the_rooms_kept_for_editors(
        collab_db, docs, manager, monkeypatch):
    monkeypatch.setattr(rooms, "MAX_ROOMS", 2)
    monkeypatch.setattr(rooms, "ROOMS_KEPT_FOR_EDITORS", 1)
    (docs / "b.md").write_text("b", encoding="utf-8")
    await open_room(manager, docs)
    row = await store.get_or_create("b.md")
    with pytest.raises(rooms.RoomsFull):
        await manager.open(row, docs / "b.md", readonly=True)
    assert await manager.open(row, docs / "b.md") is not None


async def test_the_outside_change_check_waits_for_a_save_in_flight(collab_db, docs, manager):
    from web.backend.api import collab as collab_api

    room, a, pa = await seeded_room(manager, docs)
    await room.handle(pa, a.edit_text(0, "mine "))

    class Req:
        headers = {"x-domovoi-collab": f"{room.doc_id};{room.epoch};{room.rev}"}

    room.next_file_check = 0
    async with collab_api.saving(Req(), docs / "plan.md"):
        (docs / "plan.md").write_text("mine # Plan\n", encoding="utf-8")
        tick = asyncio.create_task(room.tick())
        await settle()
        assert not tick.done()
    await tick
    assert not room.closed and room.saved_rev == room.rev
