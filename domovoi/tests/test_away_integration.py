"""Away chat end to end over a loopback stub (domovoi/away/loopback.py).

A phone speaking the real wire (preface, Noise IK with its enrolled key,
hello, framed messages) to the real away listener on 127.0.0.1; the
listener's events go to the core's real relay (domovoi/away/bridge.py),
which reaches the web process's real away bridge (in-process ASGI) and the
real chat code against the test database, and the answers come back
framed. No Tor, no Docker: what the onion adds is in front of this.

The in-process ASGI transport hands the relay a reply only once it is
whole, so the streaming test serves the web app with uvicorn on a real
loopback socket instead (``AwayLoopback(web_socket=True)``): deltas arrive
one by one, and ``chat.stream`` picks a reply up after a dropped
connection. ``away.leave`` is the core's own answer, end to end.

Marked like the Friend Houses loopback tests (``requires_db``); the full
run is CI's.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

import web.backend.api.chat as chat_api
from domovoi.away import bridge
from domovoi.away import keys as away_keys
from domovoi.away import store as away_store
from domovoi.away import wire
from domovoi.away.loopback import AwayLoopback
from domovoi.db.session import session_scope
from domovoi.friend_houses import service as svc
from domovoi.friend_houses import tor_config as tc
from domovoi.friend_houses.noise import public_bytes
from web.backend import away_bridge
from domovoi.tests.auth_testkit import _db, web_client  # noqa: F401 — fixture
from domovoi.tests.away_kit import away_db, claimed, enroll, phone  # noqa: F401 — fixture
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_away_bridge import house  # noqa: F401 — fixture

pytestmark = [requires_db, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def fresh_relay(monkeypatch):
    monkeypatch.setitem(bridge._TOKEN, "value", None)
    yield
    bridge.stop_all()


async def test_a_phone_chats_from_away(house, tmp_path) -> None:  # noqa: F811
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p = await phone(c, base)
        answer = await enroll(c, p)
        house_key = away_keys.noise_key()
        assert answer["house_noise_pub"] == away_keys.b64url(away_keys.noise_public(house_key))
        async with AwayLoopback(tmp_path / "listener", house_key=house_key) as loop:
            await svc._write_phones(loop.folder)          # what the core tells the listener
            ph = loop.phone(p["noise"])
            assert await ph.connect(loop.port) == (1, wire.MAX_PICTURE)
            await ph.request(1, {"op": "chat.send", "chat_id": None,
                                 "client_id": "0b7c4a52-3f1e-4d6a-9e2b-7c1d0a9f8e31",
                                 "text": "How far away is the moon?"})
            out = await ph.answer(1, timeout=30)
            kinds = [k for k, _ in out]
            assert kinds[0] == wire.RESP and kinds[-1] == wire.EVENT
            resp, done = out[0][1], out[-1][1]
            assert resp["user_message"]["content"] == "How far away is the moon?"
            assert done == {"type": "done", "message": done["message"]}
            assert done["message"]["content"] == "you said: How far away is the moon?"
            # Its chats, and only its own (private to the phone).
            await ph.request(2, {"op": "chat.list"})
            (kind, listing), = await ph.answer(2)
            assert kind == wire.RESP and [t["id"] for t in listing["threads"]] == [resp["chat_id"]]
            # A request the set doesn't have: ERR, and the connection stays.
            await ph.request(3, {"op": "chat.delete", "chat_id": resp["chat_id"]})
            assert (await ph.answer(3))[0] == (wire.ERR, {"code": "bad_request", "detail": "unknown op"})
            # The phone turns away chat off: the open connection is closed.
            r = await c.request("DELETE", "/api/away/enroll", headers=p["headers"])
            assert r.json() == {"ok": True}
            await svc._write_phones(loop.folder)
            loop.state.watch_once()
            bye = await ph.recv()
            assert (bye.kind, bye.json()) == (wire.HOUSE_BYE, {"code": "revoked"})
            assert await ph.closed()
            with pytest.raises(asyncio.IncompleteReadError):
                await loop.phone(p["noise"]).connect(loop.port)


async def test_a_revoked_phone_mid_connection_gets_bye_from_the_house(house, tmp_path) -> None:  # noqa: F811
    """The listener's list can lag; the web's own check answers BYE too."""
    async with web_client() as c:
        base, admin, _ = await claimed(c)
        p = await phone(c, base)
        await enroll(c, p)
        async with AwayLoopback(tmp_path / "listener", house_key=away_keys.noise_key()) as loop:
            await svc._write_phones(loop.folder)
            ph = loop.phone(p["noise"])
            await ph.connect(loop.port)
            await c.request("DELETE", f"/api/away/phones/{p['id']}", headers=admin)
            await ph.request(1, {"op": "chat.list"})   # the listener's list still has it
            bye = await ph.recv(30)
            assert (bye.kind, bye.json()) == (wire.HOUSE_BYE, {"code": "revoked"})


# ─── away.leave: the phone turns away chat off for itself, from away ─────


@pytest.fixture
def kicked(monkeypatch):
    """Each reconcile a leave asks for (Tor's keys), recorded, not run,
    with its delay shortened to half a second."""
    from domovoi.away import routes

    calls: list[int] = []
    monkeypatch.setattr(routes, "kick", lambda: calls.append(1))
    monkeypatch.setattr(bridge, "LEAVE_KICK_DELAY_SEC", 0.5)
    yield calls
    handle = bridge._KICK_LATER["handle"]
    if handle is not None:
        handle.cancel()
    bridge._KICK_LATER["handle"] = None


async def _enrolled(device_id: str) -> bool:
    async with session_scope() as s:
        return await away_store.is_enrolled(s, device_id)


async def _settle(cond, timeout: float = 5.0) -> None:
    async def wait():
        while not cond():
            await asyncio.sleep(0.02)
    await asyncio.wait_for(wait(), timeout)


async def test_a_phone_leaves_from_away_and_only_it(house, tmp_path, monkeypatch, kicked) -> None:  # noqa: F811
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p, q = await phone(c, base), await phone(c, base)
        await enroll(c, p)
        await enroll(c, q)
        async with AwayLoopback(tmp_path / "listener", house_key=away_keys.noise_key()) as loop:
            # The core's one writer of the listener's files writes this one.
            monkeypatch.setattr(svc, "_listener_folder", lambda: loop.folder)
            await svc.write_listener_files()
            leaver, stayer = loop.phone(p["noise"]), loop.phone(q["noise"])
            await leaver.connect(loop.port)
            await stayer.connect(loop.port)
            await leaver.request(1, {"op": "away.leave"})
            assert await leaver.answer(1) == [(wire.RESP, {})]
            bye = await leaver.recv()
            assert (bye.kind, bye.json()) == (wire.HOUSE_BYE, {"code": "revoked"})
            assert await leaver.closed()
            # The reconcile (which, for the last phone, stops the listener)
            # comes after the answer and the BYE are out, not before.
            assert kicked == []
            # Only the caller: its row is gone, the other phone's stays.
            assert not await _enrolled(p["id"]) and await _enrolled(q["id"])
            listed = json.loads((loop.folder / "phones.json").read_text())
            assert listed == {"on": True, "phones": {public_bytes(q["noise"]).hex(): q["id"]}}
            await _settle(lambda: kicked == [1])                      # then Tor's keys
            # The other phone carries on.
            await stayer.request(2, {"op": "chat.list"})
            assert (await stayer.answer(2))[0][0] == wire.RESP
            # Coming back after leaving is refused in silence.
            with pytest.raises(asyncio.IncompleteReadError):
                await loop.phone(p["noise"]).connect(loop.port)
            # At home, the phone hears it is no longer enrolled.
            r = await c.get("/api/away/status", headers=p["headers"])
            assert r.json()["enrolled"] is False


async def test_a_leave_can_only_ever_remove_the_caller(house, tmp_path, monkeypatch, kicked) -> None:  # noqa: F811
    """The row goes only when the device AND the Noise key it proved match."""
    monkeypatch.setattr(svc, "_listener_folder", lambda: tmp_path / "listener")
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p, q = await phone(c, base), await phone(c, base)
        await enroll(c, p)
        await enroll(c, q)
    assert not await bridge.leave(q["id"], public_bytes(p["noise"]).hex())   # p's key, q's device
    assert not await bridge.leave(p["id"], public_bytes(q["noise"]).hex())
    assert not await bridge.leave("android-nobody", public_bytes(p["noise"]).hex())
    assert await _enrolled(p["id"]) and await _enrolled(q["id"])
    assert await bridge.leave(p["id"], public_bytes(p["noise"]).hex())
    assert not await _enrolled(p["id"]) and await _enrolled(q["id"])
    assert not await bridge.leave(p["id"], public_bytes(p["noise"]).hex())   # again: the same answer


async def test_the_last_phone_leaving_takes_hs_away_away(house, tmp_path, monkeypatch, kicked) -> None:  # noqa: F811
    """S1: an onion with no clients is open. With the last phone gone the
    plan has no away onion: a stop (nothing else runs) or a restart
    (friends' onions stay), never a reload."""
    monkeypatch.setattr(svc, "_listener_folder", lambda: tmp_path / "listener")
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p = await phone(c, base)
        await enroll(c, p)
    on, phones = await svc._away_inputs()
    alone = tc.wanted(enabled=False, internet_policy="always", friends=0, open_invites=0,
                      away_on=on, away_phones=len(phones))
    with_friends = tc.wanted(enabled=True, internet_policy="always", friends=1, open_invites=0,
                             away_on=on, away_phones=len(phones))
    assert alone.away_onion and with_friends.away_onion
    assert await bridge.leave(p["id"], public_bytes(p["noise"]).hex())
    on, phones = await svc._away_inputs()
    assert (on, phones) == (True, [])
    after_alone = tc.wanted(enabled=False, internet_policy="always", friends=0, open_invites=0,
                            away_on=on, away_phones=len(phones))
    after_friends = tc.wanted(enabled=True, internet_policy="always", friends=1, open_invites=0,
                              away_on=on, away_phones=len(phones))
    assert not after_alone.run and tc.plan_change(alone, after_alone) == "stop"
    assert not after_friends.away_onion and tc.plan_change(with_friends, after_friends) == "restart"
    assert "hs-away" not in tc.render_torrc(after_friends)
    # The reconcile that applies it comes a moment later, and once.
    assert kicked == []
    await _settle(lambda: kicked == [1])
    await asyncio.sleep(0.6)
    assert kicked == [1]


async def test_a_leave_whose_list_rewrite_fails_still_happens(house, tmp_path, monkeypatch, kicked) -> None:  # noqa: F811
    """Once the row is gone the leave has happened (the web's gate refuses
    the phone): RESP {}, the BYE, and the reconcile, which writes the
    listener's list again, even though rewriting it here failed."""
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p = await phone(c, base)
        await enroll(c, p)
        async with AwayLoopback(tmp_path / "listener", house_key=away_keys.noise_key()) as loop:
            await svc._write_phones(loop.folder)

            async def broken() -> None:
                raise OSError("disk full")

            monkeypatch.setattr(svc, "write_listener_files", broken)
            ph = loop.phone(p["noise"])
            await ph.connect(loop.port)
            await ph.request(1, {"op": "away.leave"})
            assert await ph.answer(1) == [(wire.RESP, {})]
            assert (await ph.recv()).json() == {"code": "revoked"}
            assert not await _enrolled(p["id"])
            await _settle(lambda: kicked == [1])
            # A second leave (the phone never heard): the same answer, and
            # nothing left to remove.
            assert not await bridge.leave(p["id"], public_bytes(p["noise"]).hex())
            # The stale list still has it, but the web's gate refuses it.
            again = loop.phone(p["noise"])
            await again.connect(loop.port)
            await again.request(2, {"op": "chat.list"})
            assert (await again.recv(30)).json() == {"code": "revoked"}


async def test_a_leave_the_database_refuses_is_unavailable_and_nothing_changes(
        house, tmp_path, monkeypatch, kicked) -> None:  # noqa: F811
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p = await phone(c, base)
        await enroll(c, p)
        async with AwayLoopback(tmp_path / "listener", house_key=away_keys.noise_key()) as loop:
            await svc._write_phones(loop.folder)

            async def down(*_a, **_k) -> bool:
                raise OSError("database unavailable")

            monkeypatch.setattr(away_store, "revoke_own", down)
            ph = loop.phone(p["noise"])
            await ph.connect(loop.port)
            await ph.request(1, {"op": "away.leave"})
            (kind, body), = await ph.answer(1)
            assert (kind, body["code"]) == (wire.ERR, "unavailable")
            await ph.send(wire.PING)
            assert (await ph.recv()).kind == wire.PONG                 # still connected
            assert await _enrolled(p["id"])
            await asyncio.sleep(0.6)
            assert kicked == []


# ─── streaming over a real socket ────────────────────────────────────────


async def _until_text(ph, req: int, want: str, timeout: float = 15.0) -> list[dict]:
    """The events for ``req`` until the deltas' joined text is ``want``."""
    events: list[dict] = []
    text = ""
    while text != want:
        m = await ph.recv(timeout)
        assert m.req == req and m.kind == wire.EVENT, m
        event = m.json()
        assert event["type"] == "delta", event
        events.append(event)
        text += event["text"]
        assert want.startswith(text), (text, want)
    return events


async def test_replies_stream_over_a_real_socket_and_resume_after_a_drop(house, tmp_path, monkeypatch) -> None:  # noqa: F811
    words = ["one", "two", "three", "four", "five", "six", "seven"]
    gates = [asyncio.Event(), asyncio.Event()]
    produced: list[str] = []

    async def stream(messages, model, **kw):
        for i, w in enumerate(words):
            if i == 3:
                await gates[0].wait()
            if i == 5:
                await gates[1].wait()
            await asyncio.sleep(0.02)
            produced.append(w)
            yield (" " if i else "") + w

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", stream)
    # What the environment said before: the loopback puts it back.
    monkeypatch.setenv("WEB_PORT", "16369")
    monkeypatch.delenv("WEB_HOST", raising=False)
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p = await phone(c, base)
        await enroll(c, p)
        async with AwayLoopback(tmp_path / "listener", house_key=away_keys.noise_key(), web_socket=True) as loop:
            # The relay's own HTTP client, to uvicorn on a free loopback port.
            assert bridge.httpx_client is None
            assert bridge.web_url() == f"http://127.0.0.1:{loop.web_port}"
            assert loop.web_port not in (6369, 6370, 6432, 11434)
            await svc._write_phones(loop.folder)
            ph = loop.phone(p["noise"])
            await ph.connect(loop.port)
            await ph.request(1, {"op": "chat.send", "chat_id": None,
                                 "client_id": "0b7c4a52-3f1e-4d6a-9e2b-7c1d0a9f8e32", "text": "count"})
            resp = await ph.recv(15)
            assert resp.kind == wire.RESP
            sent = resp.json()
            # The reply is held after three words: they arrive anyway, one
            # delta at a time, before it is done (a relay that buffered
            # would send nothing until the end).
            first = await _until_text(ph, 1, "one two three")
            assert len(first) > 1
            run = away_bridge.running_run(p["id"])
            assert run is not None and run.running and produced == words[:3]
            # The connection drops: the relay for it is cancelled, the reply goes on.
            ph.close()
            await _settle(lambda: not bridge._RELAYS)
            gates[0].set()
            await _settle(lambda: run.text == "one two three four five")
            assert run.running
            # Back again: chat.stream picks up the same reply.
            again = loop.phone(p["noise"])
            await again.connect(loop.port)
            await again.request(2, {"op": "chat.stream", "chat_id": sent["chat_id"]})
            head = await again.recv(15)
            assert (head.kind, head.json()) == (wire.RESP, {"streaming": True, "message_id": sent["message_id"]})
            caught_up = await again.recv(15)
            assert caught_up.json() == {"type": "delta", "text": "one two three four five"}
            gates[1].set()
            rest = await _until_text(again, 2, " six seven")
            assert len(rest) > 1
            done = await again.recv(15)
            assert done.kind == wire.EVENT and done.req == 2
            assert done.json() == {"type": "done", "message": done.json()["message"]}
            assert done.json()["message"]["content"] == "one two three four five six seven"
            # done is the last for that request: the next message is the PONG.
            await again.send(wire.PING)
            assert (await again.recv(5)).kind == wire.PONG
            # The reply was stored whole.
            await again.request(3, {"op": "chat.history", "chat_id": sent["chat_id"]})
            (kind, history), = await again.answer(3)
            assert kind == wire.RESP
            assert [m["content"] for m in history["messages"]] == ["count", "one two three four five six seven"]
    assert os.environ.get("WEB_PORT") == "16369" and "WEB_HOST" not in os.environ   # put back
