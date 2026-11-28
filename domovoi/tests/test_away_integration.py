"""Away chat end to end over a loopback stub (domovoi/away/loopback.py).

A phone speaking the real wire (preface, Noise IK with its enrolled key,
hello, framed messages) to the real away listener on 127.0.0.1; the
listener's events go to the core's real relay (domovoi/away/bridge.py),
which reaches the web process's real away bridge (in-process ASGI) and the
real chat code against the test database, and the answers come back
framed. No Tor, no Docker: what the onion adds is in front of this.

Marked like the Friend Houses loopback tests (``requires_db``); the full
run is CI's.
"""

from __future__ import annotations

import pytest

from domovoi.away import bridge
from domovoi.away import keys as away_keys
from domovoi.away import wire
from domovoi.away.loopback import AwayLoopback
from domovoi.friend_houses import service as svc
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
            with pytest.raises(Exception):
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
