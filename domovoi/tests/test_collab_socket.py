"""The co-editing doors (web/backend/api/collab.py): who gets a socket, who
gets a read-only one, the path checks behind a doc id, the REST history
routes, and the Documents save/delete hooks.

The tiers are the Documents tiers (documents.py), so this module pins the
same matrix: household token or admin Bearer edits; the dashboard cookie
and ``?device_token=`` read; nothing at all is refused; a blocked device
reads but does not edit. Auth runs over ``install_fake_db``'s fakes; the
co-editing tables are the lane's (``requires_db``).

Sockets go through Starlette's TestClient on ONE event loop (a client
entered with ``with``), so two of them can share a room.
"""

from __future__ import annotations

import base64
import concurrent.futures
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from starlette.websockets import WebSocketDisconnect

import web.backend.api.files as files_api
from domovoi.config import settings
from domovoi.db.session import engine
from domovoi.tests.auth_testkit import COOKIE, HEADER, bearer, install_fake_db
from domovoi.tests.collab_sim import SimClient, control
from domovoi.tests.conftest import requires_db
from web.backend.api import collab as collab_api
from web.backend.api import documents as documents_api
from web.backend.collab import rooms, store
from web.backend.main import WS_DEVICE_TOKEN_SUBPROTOCOL_B64

pytestmark = requires_db

TOKEN = "household-token"
ADMIN = "admin-session"
RW = {"X-Requested-With": "tests"}


def sub(token: str) -> str:
    return WS_DEVICE_TOKEN_SUBPROTOCOL_B64 + base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    import asyncio

    async def wipe():
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE collab_snapshots, collab_updates, collab_docs CASCADE"))

    asyncio.run(wipe())
    monkeypatch.setattr(rooms, "MANAGER", rooms.RoomManager())

    async def not_blocked(device_id):
        return None

    monkeypatch.setattr(files_api, "_block_status", not_blocked)
    yield
    asyncio.run(wipe())


@pytest.fixture
def claimed(monkeypatch):
    return install_fake_db(monkeypatch, admin=True, sessions={ADMIN}, device_token=TOKEN)


@pytest.fixture
def docs(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    (tmp_path / "plan.md").write_text("# Plan\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not co-edited", encoding="utf-8")
    (tmp_path / "id_rsa.md").write_text("secret-shaped", encoding="utf-8")
    return tmp_path


def app() -> FastAPI:
    a = FastAPI()
    a.include_router(collab_api.router)
    a.include_router(documents_api.router)
    return a


@pytest.fixture
def client(claimed, docs):
    with TestClient(app(), base_url="http://testserver") as c:
        yield c


def doc_id(client, path="plan.md", headers=None) -> str:
    r = client.get("/api/collab/doc", params={"path": path}, headers=headers or {HEADER: TOKEN})
    assert r.status_code == 200, r.text
    return r.json()["doc_id"]


_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def recv(ws, timeout=5.0):
    """One frame, or the close; never hangs the suite."""
    fut = _POOL.submit(ws.receive)
    msg = fut.result(timeout=timeout)
    return msg


def frames_until(ws, sim, pred, limit=30):
    """Feed frames to ``sim`` (answering as a client would) until ``pred``."""
    for _ in range(limit):
        if pred():
            return
        msg = recv(ws)
        if msg["type"] == "websocket.close":
            raise AssertionError(f"closed: {msg}")
        data = msg.get("bytes")
        if data is None:
            continue
        for reply in sim.feed(data):
            ws.send_bytes(reply)
    assert pred()


def wait_closed(did, timeout=10.0):
    """The server finishes a room (flush, version, compaction) after the
    last socket is gone; wait for it before reading what it wrote."""
    import time

    end = time.monotonic() + timeout
    while did in rooms.MANAGER.rooms and time.monotonic() < end:
        time.sleep(0.05)
    room = rooms.MANAGER.rooms.get(did)
    assert did not in rooms.MANAGER.rooms, (room.peers, room.closed, room.holds, [p.closed for p in room.peers])


def close_code(ws) -> int:
    for _ in range(10):
        msg = recv(ws)
        if msg["type"] == "websocket.close":
            return msg["code"]
    raise AssertionError("socket stayed open")


def hello(ws, sim) -> dict:
    frames_until(ws, sim, lambda: sim.last_control("hello") is not None)
    return sim.last_control("hello")


# ─── Who gets a socket ────────────────────────────────────────────────


def test_no_credential_is_refused_and_told_not_to_retry(client):
    did = doc_id(client)
    with client.websocket_connect(f"/api/collab/{did}") as ws:
        assert close_code(ws) == rooms.CLOSE_UNAUTHORIZED   # 44xx: y-websocket stops


def test_a_wrong_token_is_refused(client):
    did = doc_id(client)
    with client.websocket_connect(f"/api/collab/{did}", subprotocols=[sub("nope")]) as ws:
        assert close_code(ws) == rooms.CLOSE_UNAUTHORIZED


def test_the_browser_s_token_subprotocol_edits_and_is_echoed(client):
    did = doc_id(client)
    with client.websocket_connect(f"/api/collab/{did}", subprotocols=[sub(TOKEN)]) as ws:
        assert ws.accepted_subprotocol == sub(TOKEN)
        h = hello(ws, SimClient())
        assert h["readonly"] is False and h["seed"] is True


def test_the_app_s_header_and_an_admin_bearer_edit(client):
    did = doc_id(client)
    for headers in ({HEADER: TOKEN}, bearer(ADMIN)):
        with client.websocket_connect(f"/api/collab/{did}", headers=headers) as ws:
            assert hello(ws, SimClient())["readonly"] is False


def test_the_dashboard_cookie_alone_reads(client):
    did = doc_id(client)
    client.cookies.set(COOKIE, ADMIN)
    try:
        with client.websocket_connect(f"/api/collab/{did}") as ws:
            assert hello(ws, SimClient())["readonly"] is True
    finally:
        client.cookies.clear()


def test_the_cookie_from_another_service_on_the_machine_is_refused(client):
    did = doc_id(client)
    client.cookies.set(COOKIE, ADMIN)
    try:
        with client.websocket_connect(
            f"/api/collab/{did}",
            headers={"Origin": "http://192.168.1.20:8096", "Host": "192.168.1.20:6369"},
        ) as ws:
            assert close_code(ws) == rooms.CLOSE_FORBIDDEN
        with client.websocket_connect(
            f"/api/collab/{did}",
            headers={"Origin": "http://192.168.1.20:6369", "Host": "192.168.1.20:6369"},
        ) as ws:
            assert hello(ws, SimClient())["readonly"] is True
    finally:
        client.cookies.clear()


def test_the_query_token_reads_but_never_edits(client):
    did = doc_id(client)
    with client.websocket_connect(f"/api/collab/{did}?device_token={TOKEN}") as ws:
        assert hello(ws, SimClient())["readonly"] is True


def test_a_blocked_device_reads_but_does_not_edit(client, monkeypatch):
    async def blocked(device_id):
        return "An admin blocked this device from changing files."

    monkeypatch.setattr(files_api, "_block_status", blocked)
    did = doc_id(client)
    with client.websocket_connect(f"/api/collab/{did}?device_id=browser-kid",
                                  subprotocols=[sub(TOKEN)]) as ws:
        assert hello(ws, SimClient())["readonly"] is True


def test_an_unknown_doc_id_is_not_found(client):
    with client.websocket_connect("/api/collab/dNOPE", subprotocols=[sub(TOKEN)]) as ws:
        assert close_code(ws) == rooms.CLOSE_NOT_FOUND


def test_a_doc_whose_file_is_gone_is_not_found(client, docs):
    did = doc_id(client)
    (docs / "plan.md").unlink()
    with client.websocket_connect(f"/api/collab/{did}", subprotocols=[sub(TOKEN)]) as ws:
        assert close_code(ws) == rooms.CLOSE_NOT_FOUND


def test_too_many_people_in_one_document(client, monkeypatch):
    monkeypatch.setattr(rooms, "MAX_PEERS_PER_DOC", 1)
    did = doc_id(client)
    with client.websocket_connect(f"/api/collab/{did}", subprotocols=[sub(TOKEN)]) as one:
        hello(one, SimClient())
        with client.websocket_connect(f"/api/collab/{did}", subprotocols=[sub(TOKEN)]) as two:
            assert close_code(two) == rooms.CLOSE_BUSY


# ─── The path behind a doc id ─────────────────────────────────────────


@pytest.mark.parametrize("path,status", [
    ("../outside.md", 400),
    ("/etc/passwd.md", 400),
    ("C:/Windows/win.md", 400),
    ("id_rsa.md", 404),            # secret-shaped: withheld like /api/documents/text
    ("missing.md", 404),
    ("notes.txt", 415),            # not something co-editing opens
])
def test_doc_ids_are_only_handed_out_for_paths_documents_would_open(client, path, status):
    r = client.get("/api/collab/doc", params={"path": path}, headers={HEADER: TOKEN})
    assert r.status_code == status, r.text


def test_doc_needs_the_read_tier(client):
    assert client.get("/api/collab/doc", params={"path": "plan.md"}).status_code == 401


def test_one_path_one_stable_id_and_what_the_caller_may_do(client):
    a = client.get("/api/collab/doc", params={"path": "plan.md"}, headers={HEADER: TOKEN}).json()
    b = client.get("/api/collab/doc", params={"path": "./plan.md"},
                   headers={HEADER: TOKEN}).json()
    assert a["doc_id"] == b["doc_id"] and a["readonly"] is False
    assert a["ws"] == f"/api/collab/{a['doc_id']}"
    client.cookies.set(COOKIE, ADMIN)
    try:
        c = client.get("/api/collab/doc", params={"path": "plan.md"}).json()
    finally:
        client.cookies.clear()
    assert c["readonly"] is True


# ─── End to end ───────────────────────────────────────────────────────


def test_two_sockets_converge_and_the_version_shows_in_history(client):
    did = doc_id(client)
    a, b = SimClient(1), SimClient(2)
    with client.websocket_connect(f"/api/collab/{did}?epoch=1", subprotocols=[sub(TOKEN)]) as wa:
        hello(wa, a)
        for f in a.hello_frames():
            wa.send_bytes(f)
        frames_until(wa, a, lambda: a.synced)
        wa.send_bytes(a.edit_text(0, "# Plan\n"))
        wa.send_bytes(control({"t": "seeded"}))
        frames_until(wa, a, lambda: a.last_control("ready") is not None)
        with client.websocket_connect(f"/api/collab/{did}?epoch=1", headers={HEADER: TOKEN}) as wb:
            hb = hello(wb, b)
            assert hb["ready"] is True and hb["seed"] is False
            for f in b.hello_frames():
                wb.send_bytes(f)
            frames_until(wb, b, lambda: b.synced and b.text() == "# Plan\n")
            wb.send_bytes(b.edit_text(7, "Ben's line\n"))
            frames_until(wa, a, lambda: "Ben's line" in a.text())
            wa.send_bytes(a.edit_text(0, "Draft "))
            frames_until(wb, b, lambda: b.text().startswith("Draft "))
            assert a.text() == b.text()
    wait_closed(did)
    hist = client.get("/api/collab/history", params={"path": "plan.md"},
                      headers={HEADER: TOKEN}).json()
    kinds = [v["kind"] for v in hist["versions"]]
    assert kinds[0] == "leave" and "from_file" in kinds
    latest = hist["versions"][0]
    assert sorted(latest["editors"]) == ["Someone"]
    v = client.get(f"/api/collab/history/{latest['id']}", params={"path": "plan.md"},
                   headers={HEADER: TOKEN}).json()
    assert v["text"].startswith("Draft # Plan")
    assert base64.b64decode(v["update"])


def test_a_version_is_only_served_under_its_own_document(client, docs):
    (docs / "other.md").write_text("x", encoding="utf-8")
    did = doc_id(client)
    import asyncio

    sid = asyncio.run(store.add_snapshot(did, 1, "auto", b"\x00\x00", ["A"]))
    doc_id(client, "other.md")
    r = client.get(f"/api/collab/history/{sid}", params={"path": "other.md"},
                   headers={HEADER: TOKEN})
    assert r.status_code == 404


# ─── Restore ──────────────────────────────────────────────────────────


def test_restore_needs_the_device_tier(client):
    did = doc_id(client)
    import asyncio

    sid = asyncio.run(store.add_snapshot(did, 1, "auto", b"\x00\x00", ["A"]))
    body = {"path": "plan.md", "version_id": sid}
    assert client.post("/api/collab/restore", json=body, headers=RW).status_code == 401
    client.cookies.set(COOKIE, ADMIN)
    try:
        # The cookie never authorises a change (documents.py's DEVICE tier).
        assert client.post("/api/collab/restore", json=body, headers=RW).status_code == 403
    finally:
        client.cookies.clear()


def test_restore_with_nobody_connected_marks_the_file_behind(client):
    did = doc_id(client)
    a = SimClient(1)
    with client.websocket_connect(f"/api/collab/{did}", subprotocols=[sub(TOKEN)]) as wa:
        hello(wa, a)
        for f in a.hello_frames():
            wa.send_bytes(f)
        frames_until(wa, a, lambda: a.synced)
        wa.send_bytes(a.edit_text(0, "# Plan\n"))
        wa.send_bytes(control({"t": "seeded"}))
        frames_until(wa, a, lambda: a.last_control("ready") is not None)
        wa.send_bytes(a.edit_text(7, "later\n"))
    hist = client.get("/api/collab/history", params={"path": "plan.md"},
                      headers={HEADER: TOKEN}).json()
    first = next(v for v in hist["versions"] if v["kind"] == "from_file")
    r = client.post("/api/collab/restore", json={"path": "plan.md", "version_id": first["id"]},
                    headers={HEADER: TOKEN, **RW})
    assert r.status_code == 200, r.text
    import asyncio

    row = asyncio.run(store.get_by_path("plan.md"))
    assert row.rev > row.saved_rev
    from web.backend.collab import content

    assert content.preview_text(content.doc_from_update(row.state)) == "# Plan"


# ─── The Documents doors call in ──────────────────────────────────────


def test_a_collab_save_records_the_clean_file(client, docs):
    did = doc_id(client)
    a = SimClient(1)
    with client.websocket_connect(f"/api/collab/{did}", subprotocols=[sub(TOKEN)]) as wa:
        hello(wa, a)
        for f in a.hello_frames():
            wa.send_bytes(f)
        frames_until(wa, a, lambda: a.synced)
        wa.send_bytes(a.edit_text(0, "# Plan\n"))
        wa.send_bytes(control({"t": "seeded"}))
        frames_until(wa, a, lambda: a.last_control("ready") is not None)
        wa.send_bytes(a.edit_text(7, "more\n"))
        frames_until(wa, a, lambda: True)
        room = rooms.MANAGER.rooms[did]
        rev = room.rev
        r = client.put("/api/documents/text/plan.md", json={"text": "# Plan\nmore\n"},
                       headers={HEADER: TOKEN, "X-Domovoi-Collab": f"{did};{room.epoch};{rev}", **RW})
        assert r.status_code == 200, r.text
        frames_until(wa, a, lambda: a.last_control("saved") is not None)
        assert room.saved_rev == rev and not room.rebuild_wanted
        # A save from anything else makes the room look at the file again.
        r = client.put("/api/documents/text/plan.md", json={"text": "someone else"},
                       headers={HEADER: TOKEN, **RW})
        assert r.status_code == 200
        assert room.rebuild_wanted


def test_deleting_a_document_deletes_its_shared_state_and_history(client, docs, monkeypatch):
    from domovoi import admin_auth

    did = doc_id(client)
    import asyncio

    asyncio.run(store.add_snapshot(did, 1, "auto", b"\x00\x00", ["A"]))
    r = client.post("/api/documents/delete", json={"rel_paths": ["plan.md"]},
                    headers={**bearer(ADMIN), **RW})
    assert r.status_code == 200, r.text
    assert asyncio.run(store.get(did)) is None
    del admin_auth


def test_moving_a_document_keeps_its_id(client, docs):
    did = doc_id(client)
    import asyncio

    (docs / "sub").mkdir()
    (docs / "plan.md").rename(docs / "sub" / "plan.md")
    asyncio.run(collab_api.note_moved("plan.md", "sub/plan.md"))
    assert doc_id(client, "sub/plan.md") == did
