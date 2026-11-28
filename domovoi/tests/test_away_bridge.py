"""Away chat through the house's own chat code (web/backend/away_bridge.py),
against the test database.

The web's away bridge route is called as the core calls it (loopback, the
household token, this start's bridge secret); its answer lines are the
wire's RESP / EVENT / ERR / BYE. Pinned:

* the same answer as a home send, for a member and for a proven-admin
  turn (an admin grant counts for that one turn, and its session ends with
  the reply); a bad grant sends nothing;
* no guest path: a device that isn't a current phone (never enrolled, a
  shared screen, revoked) and a house with away chat off get BYE;
* the private-chats rule (another device's chat is ``not_found``);
* ``client_id`` idempotency, one running reply per device, ``chat.stream``
  reattaching, ``chat.cancel`` keeping the partial reply;
* a picture decoded and written again, its metadata gone.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

import web.backend.api.chat as chat_api
from domovoi.away import keys as away_keys
from domovoi.db.session import session_scope
from domovoi.friend_houses import service as svc
from domovoi.friend_houses.noise import public_bytes
from domovoi.tests.auth_testkit import _db, bearer, web_client  # noqa: F401 — fixture
from domovoi.tests.away_kit import KEY, away_db, claimed, enroll, lines, phone  # noqa: F401 — fixture
from domovoi.tests.conftest import requires_db
from web.backend import away_bridge
from web.backend.api import away as away_api

pytestmark = [requires_db, pytest.mark.asyncio]


@pytest.fixture
def house(away_db, monkeypatch, tmp_path):
    """Echoing model, quiet core, temp uploads."""
    async def tell(_request):
        return True

    async def state(_device):
        return "ready"

    monkeypatch.setattr(away_api, "tell_core", tell)
    monkeypatch.setattr(away_api, "core_state", state)
    monkeypatch.setattr(svc, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(chat_api, "UPLOADS_DIR", tmp_path / "chat_uploads")
    gate = {"slow": None}

    async def stream(messages, model, **kw):
        last = next(m for m in reversed(messages) if m.get("role") == "user")
        words = f"you said: {last.get('content')}".split(" ")
        for i, w in enumerate(words):
            if gate["slow"] is not None and i == 1:
                await gate["slow"].wait()
            yield (" " if i else "") + w

    async def no_title(*a, **kw):
        return None

    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", stream)
    monkeypatch.setattr(chat_api, "schedule_title_job", lambda *_a, **_k: None)
    away_keys.reset_bridge_secret()
    away_keys.bridge_secret()
    return SimpleNamespace(gate=gate)


async def _setup(c) -> dict[str, Any]:
    base, admin, token = await claimed(c)
    p = await phone(c, base)
    await enroll(c, p)
    return {"base": base, "admin": admin, "token": token, "phone": p}


async def _away(c, h: dict[str, Any], request: dict[str, Any], *, picture: bytes = b"",
                device: str | None = None, noise: bytes | None = None) -> list[dict[str, Any]]:
    """The bridge's answer lines, as the core receives them."""
    import base64

    p = h["phone"]
    body = {"device_id": device or p["id"], "noise_pub": (noise or public_bytes(p["noise"])).hex(),
            "request": request, "picture": base64.b64encode(picture).decode() if picture else ""}
    r = await c.post("/api/away/bridge", json=body,
                     headers={**h["base"], away_api.BRIDGE_HEADER: away_keys.bridge_secret()})
    assert r.status_code == 200, r.text
    return lines(r.content)


def _send(text_: str, **over) -> dict[str, Any]:
    return {"op": "chat.send", "chat_id": None, "client_id": str(uuid.uuid4()), "text": text_} | over


def _final(out: list[dict[str, Any]]) -> dict[str, Any]:
    assert out[-1]["end"] is True
    return out[-1]["b"]


def _sse(body: str) -> list[tuple[str, Any]]:
    events = []
    for block in body.split("\n\n"):
        name, data = "message", ""
        for ln in block.split("\n"):
            if ln.startswith("event: "):
                name = ln[7:].strip()
            elif ln.startswith("data: "):
                data += ln[6:]
        if data:
            events.append((name, json.loads(data)))
    return events


async def _home(c, headers: dict[str, str], text_: str) -> dict[str, Any]:
    """A home send from the same phone: its stored assistant message."""
    t = (await c.post("/api/chat/threads", json={}, headers=headers)).json()
    r = await c.post(f"/api/chat/threads/{t['id']}/messages", json={"content": text_}, headers=headers)
    assert r.status_code == 200, r.text
    done = [d for e, d in _sse(r.text) if e == "done"]
    return done[-1]


# ─── the gate ────────────────────────────────────────────────────────────


async def test_the_bridge_route_takes_only_the_core(house) -> None:
    async with web_client() as c:
        h = await _setup(c)
        body = {"device_id": h["phone"]["id"], "noise_pub": "00" * 32, "request": {"op": "chat.list"}}
        assert (await c.post("/api/away/bridge", json=body, headers=h["base"])).status_code == 401
        wrong = {**h["base"], away_api.BRIDGE_HEADER: "f" * 64}
        assert (await c.post("/api/away/bridge", json=body, headers=wrong)).status_code == 401
        no_token = {"X-Requested-With": "x", away_api.BRIDGE_HEADER: away_keys.bridge_secret()}
        assert (await c.post("/api/away/bridge", json=body, headers=no_token)).status_code == 401
    from httpx import ASGITransport, AsyncClient

    from web.backend.main import app

    async with AsyncClient(transport=ASGITransport(app=app, client=("192.168.1.30", 5)),
                           base_url="http://test") as lan:
        r = await lan.post("/api/away/bridge", json=body,
                           headers={**h["base"], away_api.BRIDGE_HEADER: away_keys.bridge_secret()})
        assert r.status_code == 401  # loopback only, even on the home network


async def test_no_guest_path(house) -> None:
    async with web_client() as c:
        h = await _setup(c)
        # A device that never enrolled, or names another phone's key.
        other = await phone(c, h["base"])
        out = await _away(c, h, {"op": "chat.list"}, device=other["id"])
        assert out == [{"k": "bye", "b": {"code": "revoked"}, "end": True}]
        out = await _away(c, h, {"op": "chat.list"}, noise=public_bytes(other["noise"]))
        assert out[0]["b"] == {"code": "revoked"}
        # A shared screen is never a member's phone.
        await c.patch(f"/api/devices/{h['phone']['id']}/shared-screen", json={"shared_screen": True},
                      headers=h["admin"])
        assert (await _away(c, h, {"op": "chat.list"}))[0]["b"] == {"code": "revoked"}
        await c.patch(f"/api/devices/{h['phone']['id']}/shared-screen", json={"shared_screen": False},
                      headers=h["admin"])
        await enroll(c, h["phone"])
        await c.put("/api/away/setting", json={"on": False}, headers=h["admin"])
        assert (await _away(c, h, {"op": "chat.list"}))[0]["b"] == {"code": "away_off"}


async def test_a_bad_request_never_reaches_the_chat(house) -> None:
    async with web_client() as c:
        h = await _setup(c)
        out = await _away(c, h, {"op": "chat.list", "scope": "all"})
        assert out == [{"k": "err", "b": {"code": "bad_request", "detail": "unknown key"}, "end": True}]


# ─── the same answers as at home ─────────────────────────────────────────


async def test_a_member_gets_the_home_answer(house) -> None:
    async with web_client() as c:
        h = await _setup(c)
        out = await _away(c, h, _send("What's on the shopping list?"))
        resp = out[0]
        assert resp["k"] == "resp" and resp["end"] is False
        assert resp["b"]["user_message"]["content"] == "What's on the shopping list?"
        assert resp["b"]["user_message"]["mine"] is True
        deltas = "".join(x["b"]["text"] for x in out if x["k"] == "event" and x["b"]["type"] == "delta")
        done = _final(out)
        assert done["type"] == "done"
        home = await _home(c, h["phone"]["headers"], "What's on the shopping list?")
        assert done["message"]["content"] == home["content"] == deltas
        assert done["message"]["model"] == home["model"]
        # A new chat away is private to the phone, as at home.
        lst = _final(await _away(c, h, {"op": "chat.list"}))
        mine = {t["id"]: t for t in lst["threads"]}
        assert mine[resp["b"]["chat_id"]]["mine"] is True and mine[resp["b"]["chat_id"]]["shared"] is False


async def test_a_proven_admin_turn_gets_the_admin_answer_for_that_turn_only(house) -> None:
    question = "how do I add a room?"
    async with web_client() as c:
        h = await _setup(c)
        p = h["phone"]
        r = await c.post("/api/auth/phone-grant", json={"device_id": p["id"], "label": "Pixel"},
                         headers={**h["admin"], KEY: p["headers"][KEY]})
        assert r.status_code == 200, r.text
        grant = r.json()["grant"]
        member_away = _final(await _away(c, h, _send(question)))["message"]["content"]
        admin_away = _final(await _away(c, h, _send(question, admin_grant=grant)))["message"]["content"]
        member_home = (await _home(c, p["headers"], question))["content"]
        admin_home = (await _home(c, {**p["headers"], **bearer(h["token"])}, question))["content"]
        assert member_away == member_home
        assert admin_away == admin_home
        assert admin_away != member_away
        # The turn's admin session ended with the reply; the grant itself stays.
        async with session_scope() as s:
            phone_sessions = (await s.execute(text(
                "SELECT count(*) FROM admin_sessions WHERE label LIKE 'phone:%'"))).scalar_one()
        assert phone_sessions == 0
        # The next turn without the grant is a member's again.
        assert _final(await _away(c, h, _send(question)))["message"]["content"] == member_home


@pytest.mark.parametrize("grant,code", [("f" * 64, "admin_grant_invalid")])
async def test_a_bad_grant_sends_nothing(house, grant, code) -> None:
    async with web_client() as c:
        h = await _setup(c)
        before = _final(await _away(c, h, {"op": "chat.list"}))["threads"]
        out = await _away(c, h, _send("anything", admin_grant=grant))
        assert out == [{"k": "err", "b": {"code": code, "detail": out[0]["b"]["detail"]}, "end": True}]
        assert _final(await _away(c, h, {"op": "chat.list"}))["threads"] == before


async def test_an_expired_grant_is_said_so(house) -> None:
    async with web_client() as c:
        h = await _setup(c)
        p = h["phone"]
        grant = (await c.post("/api/auth/phone-grant", json={"device_id": p["id"], "label": "Pixel"},
                              headers={**h["admin"], KEY: p["headers"][KEY]})).json()["grant"]
        async with session_scope() as s:
            await s.execute(text("UPDATE admin_phone_grants SET expires_at = now() - interval '1 minute' "
                                 "WHERE device_id = :d"), {"d": p["id"]})
        out = await _away(c, h, _send("anything", admin_grant=grant))
        assert out[0]["b"]["code"] == "admin_grant_expired"


# ─── the private-chats rule ──────────────────────────────────────────────


async def test_another_devices_chat_is_not_found(house) -> None:
    async with web_client() as c:
        h = await _setup(c)
        other = await phone(c, h["base"], "browser")
        t = (await c.post("/api/chat/threads", json={"title": "Diary"}, headers=other["headers"])).json()
        for req in ({"op": "chat.history", "chat_id": t["id"]}, {"op": "chat.stream", "chat_id": t["id"]},
                    {"op": "chat.cancel", "chat_id": t["id"]}, _send("hi", chat_id=t["id"]),
                    {"op": "chat.history", "chat_id": 999_999_999}):
            out = await _away(c, h, req)
            assert out[-1]["b"]["code"] == "not_found", (req, out)
        assert t["id"] not in [x["id"] for x in _final(await _away(c, h, {"op": "chat.list"}))["threads"]]


async def test_history_pages_oldest_first_with_placeholders_for_pictures(house) -> None:
    async with web_client() as c:
        h = await _setup(c)
        first = await _away(c, h, _send("one"))
        chat_id = first[0]["b"]["chat_id"]
        for word in ("two", "three"):
            await _away(c, h, _send(word, chat_id=chat_id))
        page = _final(await _away(c, h, {"op": "chat.history", "chat_id": chat_id, "limit": 2}))
        assert [m["role"] for m in page["messages"]] == ["user", "assistant"] and page["more"] is True
        assert page["messages"][0]["content"] == "three"
        older = _final(await _away(c, h, {"op": "chat.history", "chat_id": chat_id,
                                          "before_id": page["messages"][0]["id"], "limit": 50}))
        assert [m["content"] for m in older["messages"] if m["role"] == "user"] == ["one", "two"]
        assert older["more"] is False and older["thread"]["id"] == chat_id


async def test_list_pages_with_a_cursor(house) -> None:
    async with web_client() as c:
        h = await _setup(c)
        ids = []
        for word in ("a", "b", "c"):
            ids.append((await _away(c, h, _send(word)))[0]["b"]["chat_id"])
        first = _final(await _away(c, h, {"op": "chat.list", "limit": 2}))
        assert len(first["threads"]) == 2 and first["next"]
        rest = _final(await _away(c, h, {"op": "chat.list", "limit": 2, "cursor": first["next"]}))
        seen = [t["id"] for t in first["threads"] + rest["threads"]]
        assert sorted(seen) == sorted(ids) and rest["next"] is None
        assert (await _away(c, h, {"op": "chat.list", "cursor": "bm90LWpzb24"}))[-1]["b"]["code"] == "bad_request"


# ─── sends: idempotency, one at a time, stream, cancel ───────────────────


async def test_the_same_client_id_posts_once(house) -> None:
    async with web_client() as c:
        h = await _setup(c)
        req = _send("only once")
        first = await _away(c, h, req)
        again = await _away(c, h, req)
        assert again[0]["b"]["message_id"] == first[0]["b"]["message_id"]
        assert _final(again)["type"] == "done"
        assert _final(again)["message"]["content"] == _final(first)["message"]["content"]
        hist = _final(await _away(c, h, {"op": "chat.history", "chat_id": first[0]["b"]["chat_id"]}))
        assert [m["content"] for m in hist["messages"] if m["role"] == "user"] == ["only once"]


async def test_one_reply_at_a_time_then_stream_and_cancel(house) -> None:
    house.gate["slow"] = asyncio.Event()
    async with web_client() as c:
        h = await _setup(c)
        sending = asyncio.ensure_future(_away(c, h, _send("tell me a long story")))
        for _ in range(200):
            run = away_bridge.running_run(h["phone"]["id"])
            if run is not None and run.text:
                break
            await asyncio.sleep(0.02)
        assert run is not None
        busy = await _away(c, h, _send("and another"))
        assert busy[0]["b"]["code"] == "busy" and busy[0]["b"]["chat_id"] == run.chat_id
        # Reattach: what's written so far, then the rest.
        streaming = asyncio.ensure_future(_away(c, h, {"op": "chat.stream", "chat_id": run.chat_id}))
        await asyncio.sleep(0.2)
        cancelled = await _away(c, h, {"op": "chat.cancel", "chat_id": run.chat_id})
        assert cancelled[0]["b"] == {"cancelled": True}
        house.gate["slow"].set()
        sent, followed = await sending, await streaming
        assert followed[0]["b"] == {"streaming": True, "message_id": run.message_id}
        assert followed[1]["b"] == {"type": "delta", "text": "you"}
        for out in (sent, followed):
            final = _final(out)
            assert final["type"] == "error" and final["code"] == "cancelled"
            assert final["message"]["content"] == "you"  # the partial reply is kept
        # Nothing running now: stream says so, cancel finds nothing.
        assert _final(await _away(c, h, {"op": "chat.stream", "chat_id": run.chat_id})) == {"streaming": False}
        assert _final(await _away(c, h, {"op": "chat.cancel", "chat_id": run.chat_id})) == {"cancelled": False}


async def test_a_reply_goes_on_when_nobody_listens(house) -> None:
    house.gate["slow"] = asyncio.Event()
    async with web_client() as c:
        h = await _setup(c)
        sending = asyncio.ensure_future(_away(c, h, _send("keep going")))
        for _ in range(200):
            run = away_bridge.running_run(h["phone"]["id"])
            if run is not None:
                break
            await asyncio.sleep(0.02)
        sending.cancel()  # the phone dropped (the core's relay was cancelled)
        await asyncio.sleep(0.05)
        house.gate["slow"].set()
        await asyncio.wait_for(run.task, 10)
        hist = _final(await _away(c, h, {"op": "chat.history", "chat_id": run.chat_id}))
        assert hist["messages"][-1]["content"] == "you said: keep going"


# ─── pictures ────────────────────────────────────────────────────────────


def _photo_with_exif() -> bytes:
    from PIL import Image

    im = Image.new("RGB", (64, 48), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"          # Make
    exif[0x0112] = 6                     # orientation: rotated
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80, exif=exif.tobytes(), comment=b"secret note")
    return buf.getvalue()


async def test_a_picture_is_decoded_and_written_again_without_metadata(house, monkeypatch) -> None:
    from PIL import Image

    async def tags(*a, **kw):
        return [{"name": chat_api.core_settings.ollama_vision_model}]

    async def no_system(*a, **kw):
        return None

    monkeypatch.setattr(chat_api.ollama_client, "tags_or_none", tags)
    monkeypatch.setattr(chat_api.ollama_client, "model_system_or_none", no_system)
    data = _photo_with_exif()
    pic = {"type": "jpeg", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    async with web_client() as c:
        h = await _setup(c)
        out = await _away(c, h, _send("", picture=pic), picture=data)
        assert out[0]["k"] == "resp", out
        assert out[0]["b"]["user_message"]["images"] == [{"kind": "picture"}]
        stored = list(chat_api.UPLOADS_DIR.glob("*.jpg"))
        stored = [p for p in stored if "-" not in p.stem]
        assert len(stored) == 1
        raw = stored[0].read_bytes()
        assert raw != data and b"PhoneMaker" not in raw and b"secret note" not in raw
        with Image.open(io.BytesIO(raw)) as im:
            assert im.format == "JPEG" and not im.getexif() and im.size == (48, 64)  # upright, then stripped


async def test_a_picture_that_isnt_one_is_refused(house) -> None:
    data = b"\xff\xd8\xff\xe0" + b"not really a jpeg" * 10
    pic = {"type": "jpeg", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    async with web_client() as c:
        h = await _setup(c)
        out = await _away(c, h, _send("look", picture=pic), picture=data)
        assert out == [{"k": "err", "b": {"code": "unsupported_picture", "detail": out[0]["b"]["detail"]},
                        "end": True}]
        assert not list(chat_api.UPLOADS_DIR.glob("*")) if chat_api.UPLOADS_DIR.exists() else True


# ─── review fixes (2026-11-27) ───────────────────────────────────────────


async def test_a_grant_session_for_one_turn_lasts_fifteen_minutes_at_most(house) -> None:
    """L4: the admin session a turn's grant becomes ends with the reply, and
    in 15 minutes whatever happens."""
    from datetime import datetime, timedelta, timezone

    from domovoi import admin_auth

    async with web_client() as c:
        h = await _setup(c)
        p = h["phone"]
        grant = (await c.post("/api/auth/phone-grant", json={"device_id": p["id"], "label": "Pixel"},
                              headers={**h["admin"], KEY: p["headers"][KEY]})).json()["grant"]
        async with session_scope() as s:
            token, _ = await admin_auth.exchange_phone_grant(s, grant, p["id"],
                                                             max_session_sec=away_bridge.ADMIN_TURN_MAX_SEC)
            hard = (await s.execute(text("SELECT hard_expires_at FROM admin_sessions WHERE token_hash = :h"),
                                    {"h": admin_auth.token_sha256(token)})).scalar_one()
        assert hard <= datetime.now(timezone.utc) + timedelta(minutes=15, seconds=5)


async def test_the_bridge_secret_compare_never_raises(tmp_path, monkeypatch) -> None:
    """L5: a header with characters outside ASCII is a plain no, not a 500."""
    from domovoi import admin_auth

    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    away_keys.reset_bridge_secret()
    secret = away_keys.bridge_secret()
    assert away_keys.bridge_matches(secret)
    assert not away_keys.bridge_matches("é" * 64)
    assert not away_keys.bridge_matches(secret[:-1] + "\u00ff")
    away_keys.reset_bridge_secret()


async def test_a_picture_over_16_megapixels_is_too_large(house) -> None:
    """L9: pictures from away are decoded only up to 16 megapixels."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (5000, 4000), (0, 0, 0)).save(buf, "JPEG", quality=10)
    data = buf.getvalue()
    assert len(data) < 1_048_576
    pic = {"type": "jpeg", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    async with web_client() as c:
        h = await _setup(c)
        out = await _away(c, h, _send("look", picture=pic), picture=data)
        assert out[0]["b"]["code"] == "too_large"


async def test_the_user_turn_id_comes_from_the_send_itself(house) -> None:
    """L9: the RESP's message_id is the row send_message stored, even when
    the same phone has a later message in the chat."""
    async with web_client() as c:
        h = await _setup(c)
        first = await _away(c, h, _send("one"))
        hist = _final(await _away(c, h, {"op": "chat.history", "chat_id": first[0]["b"]["chat_id"]}))
        users = [m for m in hist["messages"] if m["role"] == "user"]
        assert first[0]["b"]["message_id"] == users[-1]["id"] == first[0]["b"]["user_message"]["id"]


# ─── a failure never ends as a silent stream ─────────────────────────────


@pytest.mark.parametrize("where", ["send", "gate", "list"])
async def test_an_unexpected_failure_is_an_err_never_a_silent_stream(house, monkeypatch, caplog, where) -> None:
    """A request whose answer just stopped was "unavailable" to the core
    (domovoi/away/bridge.py: no last line), which the phone shows as "Your
    house is busy": the failure is now said (ERR ``internal``, the last
    line) and logged by its type, never its content."""
    secret_words = "the secret words of the message"

    async def boom(*_a, **_k):
        raise RuntimeError(secret_words)

    async with web_client() as c:
        h = await _setup(c)
        if where == "send":
            monkeypatch.setattr(chat_api, "send_message", boom)
            request = _send("hello there")
        elif where == "gate":
            monkeypatch.setattr(away_bridge, "gate", boom)
            request = _send("hello there")
        else:
            monkeypatch.setattr(chat_api, "list_threads", boom)
            request = {"op": "chat.list"}
        with caplog.at_level("WARNING", logger="web.backend.away_bridge"):
            out = await _away(c, h, request)
    assert out == [{"k": "err", "b": {"code": "internal", "detail": "Something went wrong at the house."},
                    "end": True}]
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in said and secret_words not in said
    assert away_bridge.running_run(h["phone"]["id"]) is None


async def test_only_the_wires_events_reach_the_phone(house, monkeypatch) -> None:
    """The home send's other SSE events (``status``: the typed chat's
    house_command, a draft's drafting; ``replace``; a job's) are the home
    page's: the phone gets delta, done and error only, and ``replace`` still
    sets the text a chat.stream catches up with."""
    from fastapi.responses import StreamingResponse

    async def send_message(thread_id, body, request, viewer=None):
        async with session_scope() as s:
            uid = (await s.execute(text(
                "INSERT INTO chat_messages (thread_id, role, content, device_id) VALUES (:t, 'user', :c, :d) "
                "RETURNING id"), {"t": thread_id, "c": body.content, "d": body.device_id})).scalar_one()
            row = (await s.execute(text(
                "INSERT INTO chat_messages (thread_id, role, content) VALUES (:t, 'assistant', 'Fixed.') "
                "RETURNING id, role, content"), {"t": thread_id})).mappings().one()
            await s.commit()
        request.state.chat_user_message_id = int(uid)

        async def sse():
            yield f"event: status\ndata: {json.dumps({'state': 'house_command'})}\n\n"
            yield f"event: delta\ndata: {json.dumps({'text': 'Wrong'})}\n\n"
            yield f"event: replace\ndata: {json.dumps({'text': 'Fixed.'})}\n\n"
            yield f"event: job\ndata: {json.dumps({'id': 1})}\n\n"
            yield f"event: done\ndata: {json.dumps(dict(row))}\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")

    monkeypatch.setattr(chat_api, "send_message", send_message)
    async with web_client() as c:
        h = await _setup(c)
        out = await _away(c, h, _send("set a timer for 5 minutes"))
    assert [(x["k"], x["b"].get("type")) for x in out] == [("resp", None), ("event", "delta"), ("event", "done")]
    assert out[-1]["b"]["message"]["content"] == "Fixed."
    run = away_bridge._RUNS[h["phone"]["id"]]
    assert run.text == "Fixed."
