"""Security review of wf/doc-collab, round 1 (2026-10-26): the findings,
as tests that pin the fixes.

F1  a tiny update crashed the web process (yrs recursion): guard.py
F2  saves landing out of order left the file behind, marked saved
F3  stale-epoch reconnects flooded "offline copies" and evicted history
F4  reopening during last-one-out lost the final edits
M3  a socket could join a room that was being shut
and the LOW items: moves, own save seen as an outside change, view-only
caps, DNS rebinding, presence spoofing, a failed restore half clearing.

The crash reproductions run in a CHILD process: before the fix they kill
the interpreter (0xC00000FD / SIGSEGV), which must never take pytest down.
"""

from __future__ import annotations

import asyncio
import base64
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from pycrdt import Doc, Text, XmlElement, XmlFragment, write_var_uint

from domovoi.tests.collab_sim import SimClient, control
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_collab_rooms import (  # noqa: F401 — fixtures
    collab_db, connect, docs, make_peer, manager, open_room, pump, seed, settle,
)
from web.backend.collab import content, guard, rooms, store
from web.backend.collab.protocol import var_bytes

REPO = Path(__file__).resolve().parents[2]


def _vu(n: int) -> bytes:
    return bytes(write_var_uint(n))


def deep_any_update(depth: int) -> bytes:
    """A Yjs v1 update: one ContentAny item in root array 'x' holding
    [[[[...null...]]]] nested ``depth`` deep. ~2 bytes per level."""
    any_ = bytes([117, 1]) * depth + bytes([126])
    return (_vu(1) + _vu(1) + _vu(77) + _vu(0) + bytes([8]) + _vu(1) + _vu(1) + b"x"
            + _vu(1) + any_ + _vu(0))


def deep_json_update(depth: int) -> bytes:
    """The same nesting as a JSON string (ContentEmbed in root text 't')."""
    js = "[" * depth + "]" * depth
    return (_vu(1) + _vu(1) + _vu(78) + _vu(0) + bytes([5]) + _vu(1) + _vu(1) + b"t"
            + var_bytes(js.encode()) + _vu(0))


def deep_xml_update(depth: int) -> bytes:
    d = Doc(client_id=55)
    node = d.get("default", type=XmlFragment)
    for _ in range(depth):
        node = node.children.append(XmlElement("p"))
    return d.get_update()


def delete_first_struct_of(client: int) -> bytes:
    """No structs; a delete set removing (client, 0) — what deleting the top
    element of a tree looks like on the wire."""
    return _vu(0) + _vu(1) + _vu(client) + _vu(1) + _vu(0) + _vu(1)


# A real Yjs 13.6.33 document (the vendored bundle, in Node): headings, a
# list, bold/italic/link marks, an emoji, a map of JSON values, an array
# with a byte string, deletions — merged into one update.
REAL_YJS = base64.b64decode(
    "ASXe/90EAAcBB2RlZmF1bHQDCXBhcmFncmFwaCgA3v/dBAAJdGV4dEFsaWduAXcEbGVmdAcA3v/dBAAGBgDe/90EAgRib2xkBHRydWWE3v/dBAMQSGVsbG8g8J+YgCB3b3JsZIbe/90EEQRib2xkBG51bGxG3v/dBAMGaXRhbGljBHRydWXG3v/dBBPe/90EAwRsaW5rInsiaHJlZiI6Imh0dHBzOi8veCIsInRhcmdldCI6bnVsbH3G3v/dBAje/90ECQZpdGFsaWMEbnVsbMbe/90EFd7/3QQJBGxpbmsEbnVsbIfe/90EAAMKYnVsbGV0TGlzdAcA3v/dBBcDCGxpc3RJdGVtBwDe/90EGAMJcGFyYWdyYXBoBwDe/90EGQYEAN7/3QQaBml0ZW0gMIfe/90EGAMIbGlzdEl0ZW0HAN7/3QQhAwlwYXJhZ3JhcGgHAN7/3QQiBgQA3v/dBCMGaXRlbSAxh97/3QQhAwhsaXN0SXRlbQcA3v/dBCoDCXBhcmFncmFwaAcA3v/dBCsGBADe/90ELAZpdGVtIDKH3v/dBCoDCGxpc3RJdGVtBwDe/90EMwMJcGFyYWdyYXBoBwDe/90ENAYEAN7/3QQ1Bml0ZW0gM4fe/90EMwMIbGlzdEl0ZW0HAN7/3QQ8AwlwYXJhZ3JhcGgHAN7/3QQ9BgQA3v/dBD4GaXRlbSA0KAEFc2hlZXQFY2VsbHMBdgQBYXUDfQF9AnYBAWJ3AWMBbn4BZnw/wAAAA2JpZ3tCBv7g4agAACcBBXNoZWV0A2FycgAIAN7/3QRGAn0BdwN0d2+D3v/dBEgCAQIEAQR0ZXh0BXBsYWluhN7/3QROBSBtb3JlAd7/3QQCBgMXLg=="
)


# ─── F1: the guard ───────────────────────────────────────────────────


def test_a_real_yjs_document_passes_the_guard():
    index = guard.StructIndex()
    guard.commit(index, guard.check_update(REAL_YJS, index))
    d = Doc()
    d.apply_update(REAL_YJS)
    guard.index_for(d.get_update())          # pycrdt's re-encoding too


def test_nesting_up_to_the_limit_is_fine_and_past_it_is_not():
    guard.index_for(deep_xml_update(guard.MAX_TYPE_DEPTH - 1))
    guard.check_values(deep_any_update(guard.MAX_VALUE_DEPTH - 1))
    with pytest.raises(guard.UpdateRejected):
        guard.index_for(deep_xml_update(guard.MAX_TYPE_DEPTH + 1))
    for blob in (deep_any_update(guard.MAX_VALUE_DEPTH + 1), deep_any_update(10000),
                 deep_json_update(guard.MAX_VALUE_DEPTH + 1)):
        with pytest.raises(guard.UpdateRejected):
            guard.check_values(blob)


def test_type_depth_is_followed_through_neighbours_across_updates():
    """Yjs writes an item's parent only when it has no neighbours, so the
    depth of a nested element added NEXT to an existing one comes from the
    index of what the room already holds."""
    d = Doc(client_id=7)
    index = guard.StructIndex()

    def step(change):
        sv = d.get_state()
        result = change()
        guard.commit(index, guard.check_update(d.get_update(sv), index))
        return result

    node = d.get("default", type=XmlFragment)
    parent = node
    for _ in range(guard.MAX_TYPE_DEPTH):                 # the deepest allowed
        parent, node = node, step(lambda n=node: n.children.append(XmlElement("p")))
    # A sibling of the deepest element: its parent comes from its neighbour.
    sibling = step(lambda: parent.children.append(XmlElement("q")))
    sv = d.get_state()
    sibling.children.append(XmlElement("p"))              # one level too many
    with pytest.raises(guard.UpdateRejected):
        guard.check_update(d.get_update(sv), index)


@pytest.mark.parametrize("blob", [
    REAL_YJS[:-3],                                     # truncated
    REAL_YJS + b"\x00",                                # trailing bytes
    _vu(1) + _vu(1) + _vu(9) + _vu(0) + bytes([9]) + _vu(1) + _vu(1) + b"x"
    + var_bytes(b"guid") + bytes([126]) + _vu(0),      # a sub-document
    _vu(1) + _vu(1) + _vu(9) + _vu(0) + bytes([31]) + _vu(0),   # unknown content
    _vu(1) + _vu(1) + _vu(9) + _vu(0) + bytes([0x84]) + _vu(4) + _vu(4)
    + var_bytes(b"x") + _vu(0),                        # refers to nothing we hold
])
def test_malformed_or_dangling_updates_are_refused(blob):
    with pytest.raises(guard.UpdateRejected):
        guard.check_update(blob, guard.StructIndex())


def _run_child(body: str) -> subprocess.CompletedProcess:
    code = textwrap.dedent(f"""
        import asyncio, sys
        sys.path.insert(0, {str(REPO)!r})
        from pathlib import Path
        from web.backend.collab import rooms, store
        from web.backend.collab.protocol import var_bytes
        from domovoi.tests.test_collab_review import (
            deep_any_update, deep_json_update, deep_xml_update, delete_first_struct_of)
        closes = []
        def peer(epoch=1):
            async def sb(b): pass
            async def cl(c, r): closes.append(c)
            p = rooms.Peer(send_bytes=sb, close=cl, name='n', color='#000', readonly=False,
                           source='s', epoch=epoch, archive=epoch != 1)
            p.writer = asyncio.get_running_loop().create_task(p.run_writer())
            return p
        def room():
            row = store.DocRow('dX', 'a.md', 1, None, True, None, None, None, 0, 0)
            return rooms.Room(row, Path('a.md'))
        async def main():
{textwrap.indent(textwrap.dedent(body), ' ' * 12)}
            for _ in range(5): await asyncio.sleep(0)
            print('survived', closes, flush=True)
        asyncio.run(main())
    """)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=120, cwd=str(REPO))


@pytest.mark.parametrize("make", ["deep_any_update(10000)", "deep_json_update(10000)"])
def test_F1_a_deeply_nested_value_is_refused_not_a_crash(make):
    r = _run_child(f"""
        rm, p = room(), peer()
        await rm.handle(p, bytes([0, 2]) + var_bytes({make}))
        assert rm.rev == 0
    """)
    assert r.returncode == 0 and "survived [4400]" in r.stdout, (r.returncode, r.stdout, r.stderr[-800:])


def test_F1_a_deep_tree_never_gets_in_so_deleting_it_cannot_crash():
    r = _run_child("""
        rm, p = room(), peer()
        await rm.handle(p, bytes([0, 2]) + var_bytes(deep_xml_update(20000)))
        p2 = peer()
        await rm.handle(p2, bytes([0, 2]) + var_bytes(delete_first_struct_of(55)))
        str(rm.ydoc.get('default', type=__import__('pycrdt').XmlFragment))
    """)
    assert r.returncode == 0 and "survived [4400]" in r.stdout, (r.returncode, r.stdout, r.stderr[-800:])


@requires_db
async def test_F1_the_offline_copy_path_reads_the_payload_first(collab_db, docs, manager):
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await seed(room, a, pa)
    (docs / "plan.md").write_text("changed elsewhere\n", encoding="utf-8")
    room.rebuild_wanted = True
    await room.tick()
    row = await store.get_by_path("plan.md")
    room2 = await manager.open(row, docs / "plan.md")
    p = make_peer(room2, "Phone", epoch=row.epoch - 1)
    room2.join(p)
    await room2.handle(p, bytes([0, 1]) + var_bytes(deep_any_update(guard.MAX_VALUE_DEPTH + 1)))
    await settle()
    snaps = await store.list_snapshots(row.doc_id)
    assert not any(s.kind == "offline_copy" for s in snaps)
    assert p.closes and p.closes[0][0] == rooms.CLOSE_RESET


# ─── F2: saves out of order ──────────────────────────────────────────


@requires_db
async def test_F2_an_older_save_landing_last_makes_the_file_behind_again(collab_db, docs, manager):
    """For an editor that saves without the pre-check (note only): the file
    holds the OLDER revision after all, and the room asks for a save."""
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
    assert room.saved_rev == rev_a < room.rev            # the file is behind, and the room knows
    assert room.save_refusal(rev_a - 1) and room.save_refusal(room.rev + 1)
    assert room.save_refusal(room.rev) is None
    room.maybe_ask_for_save(10 ** 9)
    assert room.save_peer is not None


@requires_db
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
    assert room.save_peer is first                      # still waiting on the first


# ─── F3: offline copies ──────────────────────────────────────────────


async def _rebuilt(room, docs, manager):
    (docs / "plan.md").write_text("# Plan, edited elsewhere\n", encoding="utf-8")
    room.rebuild_wanted = True
    await room.tick()
    row = await store.get_by_path("plan.md")
    return await manager.open(row, docs / "plan.md"), row


async def _stale_visit(room, name, device, epoch, text_):
    junk = SimClient()
    junk.edit_text(0, text_)
    p = make_peer(room, name, epoch=epoch)
    p.device = device
    room.join(p)
    await pump(room, (p, junk))
    room.leave(p)


@requires_db
async def test_F3_offline_copies_are_one_per_device_capped_and_never_evict_history(
        collab_db, docs, manager, monkeypatch):
    monkeypatch.setattr(store, "MAX_SNAPSHOTS", 5)
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await seed(room, a, pa)
    old_epoch = room.epoch
    room2, row = await _rebuilt(room, docs, manager)
    # An epoch the document never had (or a newer one) keeps nothing.
    await _stale_visit(room2, "Liar", "dev-liar", 987654, "junk")
    # One device reconnecting over and over: one copy.
    for i in range(5):
        await _stale_visit(room2, "Phone", "dev-phone", old_epoch, f"try {i} ")
    # Many devices: capped.
    for i in range(store.MAX_OFFLINE_COPIES + 5):
        await _stale_visit(room2, f"D{i}", f"dev-{i}", old_epoch, "x" * 500)
    snaps = await store.list_snapshots(row.doc_id, limit=100000)
    copies = [s for s in snaps if s.kind == "offline_copy"]
    assert len(copies) == store.MAX_OFFLINE_COPIES
    assert all("Liar" not in s.editors for s in copies)
    real = [s.kind for s in snaps if s.kind != "offline_copy"]
    assert "from_file" in real and "before_rebuild" in real


# ─── F4 / M3: shutting down while someone opens ──────────────────────


@requires_db
async def test_F4_reopening_during_the_last_one_out_waits_and_keeps_the_final_edit(
        collab_db, docs, manager, monkeypatch):
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await seed(room, a, pa)
    await room.tick()
    await room.handle(pa, a.edit_text(0, "FINAL "))
    gate = asyncio.Event()
    real_append = store.append_update

    async def slow_append(*args, **kw):
        await gate.wait()
        return await real_append(*args, **kw)

    monkeypatch.setattr(store, "append_update", slow_append)
    room.leave(pa)
    shut = asyncio.create_task(manager.release(room))
    await settle()
    row = await store.get_by_path("plan.md")
    reopen = asyncio.create_task(manager.open(row, docs / "plan.md"))
    await settle()
    assert not reopen.done()                    # it waits for the shutdown
    gate.set()
    await shut
    room2 = await reopen
    assert room2 is not room
    assert "FINAL" in str(room2.ydoc.get("text", type=Text))


@requires_db
async def test_M3_a_room_being_shut_is_never_handed_out(collab_db, docs, manager):
    room = await open_room(manager, docs)
    room.closing = True
    row = await store.get_by_path("plan.md")
    opening = asyncio.create_task(manager.open(row, docs / "plan.md"))
    await settle()
    assert not opening.done()
    await room.shut()
    assert (await opening) is not room


# ─── LOW items ───────────────────────────────────────────────────────


@requires_db
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
    # The same client comes back on a new socket with its clock where it was.
    pa2 = make_peer(room, "Ana")
    a2 = SimClient(11)
    await connect(room, a2, pa2)
    await room.handle(pa2, a2.presence_frame({"user": {"name": "Ana"}}, clock=5))
    await pump(room, (pa2, a2), (pb, b))
    assert 11 in b.presence


@requires_db
async def test_a_restore_that_fails_half_way_leaves_the_document_alone(
        collab_db, docs, manager, monkeypatch):
    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await seed(room, a, pa)
    v = room.ydoc.get_update()
    await room.handle(pa, a.edit_text(0, "keep me "))
    before = str(room.ydoc.get("text", type=Text))
    seen = a.updates_seen

    def boom(*_a, **_k):
        raise RuntimeError("copy failed")

    monkeypatch.setattr(content, "_copy_text_runs", boom)
    with pytest.raises(RuntimeError):
        await room.restore(v, "Ana")
    await pump(room, (pa, a))
    assert str(room.ydoc.get("text", type=Text)) == before
    assert a.updates_seen == seen


@requires_db
async def test_view_only_sessions_have_their_own_small_cap(collab_db, docs, manager, monkeypatch):
    monkeypatch.setattr(rooms, "MAX_VIEWERS_PER_DOC", 1)
    room = await open_room(manager, docs)
    room.join(make_peer(room, "V1", readonly=True))
    assert not room.has_room_for(readonly=True)
    assert room.has_room_for(readonly=False)


@requires_db
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


@requires_db
async def test_the_outside_change_check_waits_for_a_save_in_flight(collab_db, docs, manager):
    from web.backend.api import collab as collab_api

    room = await open_room(manager, docs)
    a = SimClient(1)
    pa = make_peer(room, "Ana")
    await connect(room, a, pa)
    await seed(room, a, pa)
    await room.handle(pa, a.edit_text(0, "mine "))

    class Req:
        headers = {"x-domovoi-collab": f"{room.doc_id};{room.epoch};{room.rev}"}

    room.next_file_check = 0
    async with collab_api.saving(Req(), docs / "plan.md"):
        (docs / "plan.md").write_text("mine # Plan\n", encoding="utf-8")
        tick = asyncio.create_task(room.tick())
        await settle()
        assert not tick.done()                   # blocked on the path lock
    await tick
    assert not room.closed and room.saved_rev == room.rev
