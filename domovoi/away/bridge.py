"""The core's relay for away chat: listener -> web process -> listener.

Away chat (1.5.0): the listener (:mod:`domovoi.away.listener`, in its
container) hands the core one checked request at a time, as an ``away``
event on its stdout. The house's chat lives in the web process
(``web/backend/api/chat.py``), so the core passes each request on to the
web's away bridge route (``POST /api/away/bridge``, loopback, with the
household token and this start's bridge secret: :mod:`domovoi.away.keys`),
which runs it through **the same chat code** as a home send, under the
phone's device, and answers one JSON line per message. Each line goes back
to the listener as ``away {...}`` on its stdin, framed there for the phone.

The reply is driven by the web process, not by this relay or the phone's
connection: when the connection ends (``away_closed``), the relays for it
are cancelled here and the web's run carries on and is stored.

One request is the core's own: ``away.leave`` (the phone turns away chat
off for itself, from away) changes the phones, which the core applies, so
it is answered here (:func:`leave`) and never reaches the web process.

Bounded: at most :data:`MAX_RELAYS_PER_CONN` relays per connection and
:data:`MAX_RELAYS` in all (the listener allows four open requests per
connection and sixteen connections); one over is answered ``unavailable``.
Nothing from a message's text or a picture is logged here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from domovoi.away import wire

log = logging.getLogger(__name__)

MAX_RELAYS = 72
MAX_RELAYS_PER_CONN = 5
#: The longest one request may take end to end (a long reply streams for
#: minutes; the web keeps going after this, the phone can chat.stream).
RELAY_DEADLINE_SEC = 900.0
CONNECT_TIMEOUT_SEC = 10.0
LINE_MAX = 4 * 1024 * 1024
BRIDGE_PATH = "/api/away/bridge"
BRIDGE_HEADER = "X-Domovoi-Away-Bridge"

_DEVICE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}\Z")
_HEX64 = re.compile(r"^[0-9a-f]{64}\Z")
_DIGITS = re.compile(r"^[0-9]{1,10}\Z")
#: A connection id: the listener starts them at a random 48-63-bit number
#: in each of its processes (domovoi/away/listener.py).
_CONN = re.compile(r"^[0-9]{1,19}\Z")

_RELAYS: dict[int, set[asyncio.Task]] = {}
#: The HTTP client factory (tests: domovoi/away/loopback.py points it at
#: the web app in-process). None: httpx.AsyncClient.
httpx_client: Any = None
_TOKEN: dict[str, Any] = {"value": None, "at": 0.0}


def web_url() -> str:
    """The web process on this box: loopback, unless WEB_HOST names one
    address only."""
    host = (os.environ.get("WEB_HOST") or "").strip()
    if host in ("", "0.0.0.0", "::", "[::]"):
        host = "127.0.0.1"
    try:
        port = int(os.environ.get("WEB_PORT", "") or 6369)
    except ValueError:
        port = 6369
    if not 0 < port < 65536:
        port = 6369
    # Not setup_qr.web_port(): only the core's terminal output imports the
    # setup-code module (test_setup_qr).
    return f"http://{host}:{port}"


def stop_all() -> None:
    """Cancel every relay. Called when a listener process ends: its
    connections are gone, and an answer still on its way must never reach
    a connection of the next process (review H1)."""
    for tasks in list(_RELAYS.values()):
        for task in list(tasks):
            task.cancel()
    _RELAYS.clear()


def _count() -> int:
    return sum(len(t) for t in _RELAYS.values())


def on_event(event: dict[str, Any]) -> None:
    """An ``away`` or ``away_closed`` event from the listener. Checked
    again here; one that doesn't fit is dropped (the listener is trusted
    less than the core)."""
    kind = event.get("event")
    conn_s = str(event.get("conn", ""))
    if not _CONN.match(conn_s):
        return
    conn = int(conn_s)
    if kind == "away_closed":
        for task in _RELAYS.pop(conn, set()):
            task.cancel()
        return
    req_s, device, noise = str(event.get("req", "")), str(event.get("device", "")), str(event.get("noise", ""))
    if not (_DIGITS.match(req_s) and _DEVICE.match(device) and _HEX64.match(noise)):
        log.warning("away chat: a request event failed its checks and was dropped")
        return
    req = int(req_s)
    if not 1 <= req <= 0xFFFFFFFF:
        return
    try:
        request = wire.parse_request(wire.load_object(str(event.get("request", "")).encode("utf-8")))
    except wire.BadRequest as e:
        _spawn_line(conn, req, "err", {"code": e.code, "detail": e.detail}, end=True)
        return
    picture = str(event.get("picture", ""))
    if (request.picture is None) != (picture == ""):
        log.warning("away chat: a request's picture didn't match its declaration; dropped")
        _spawn_line(conn, req, "err", {"code": "internal", "detail": "the picture went missing"}, end=True)
        return
    tasks = _RELAYS.setdefault(conn, set())
    if len(tasks) >= MAX_RELAYS_PER_CONN or _count() >= MAX_RELAYS:
        _spawn_line(conn, req, "err", {"code": "unavailable", "detail": "the house is busy; try again shortly"},
                    end=True)
        return
    # A request through the listener proves this phone found the onion:
    # its descriptor is out (domovoi.friend_houses.service.away_status).
    from domovoi.friend_houses import service as svc

    svc.away_reached(device, noise)
    if request.op == wire.LEAVE:
        # Answered here, not by the web's chat code: it changes the phones,
        # which are the core's to apply.
        coro = _leave(conn, req, device, noise)
    else:
        payload = {"device_id": device, "noise_pub": noise, "request": request.to_json(), "picture": picture}
        coro = _relay(conn, req, payload)
    task = asyncio.get_running_loop().create_task(coro, name="away-chat-relay")
    tasks.add(task)
    task.add_done_callback(lambda t, c=conn: _forget(c, t))


def _forget(conn: int, task: asyncio.Task) -> None:
    tasks = _RELAYS.get(conn)
    if tasks is not None:
        tasks.discard(task)
        if not tasks:
            _RELAYS.pop(conn, None)


def _spawn_line(conn: int, req: int, kind: str, body: dict[str, Any], *, end: bool) -> None:
    from domovoi.friend_houses import service as svc

    svc.spawn(_send(conn, req, kind, body, end=end), "away-chat-answer")


async def _send(conn: int, req: int, kind: str, body: dict[str, Any], *, end: bool) -> bool:
    from domovoi.friend_houses import service as svc

    line = json.dumps({"c": conn, "r": req, "k": kind, "b": body, "end": end},
                      separators=(",", ":"), ensure_ascii=True)
    return await svc.send_listener_line("away " + line)


async def _household_token() -> str | None:
    """The household token the web route's device tier asks for, read
    from the database at most once a minute."""
    import time

    if _TOKEN["value"] is not None and time.monotonic() - _TOKEN["at"] < 60.0:
        return _TOKEN["value"]
    from domovoi import admin_auth
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        token = await admin_auth.get_device_token(s)
    _TOKEN.update(value=token, at=time.monotonic())
    return token


async def _relay(conn: int, req: int, payload: dict[str, Any]) -> None:
    """One request through the web's chat, its answers back to the phone.
    Whatever fails, the request ends with an ``ERR`` (``unavailable``: the
    house's chat isn't answering right now)."""
    import httpx

    from domovoi.away import keys as away_keys

    ended = False
    try:
        token = await _household_token()
        # X-Requested-With: the web refuses a write without it (its CSRF guard).
        headers = {BRIDGE_HEADER: away_keys.bridge_secret(), "Content-Type": "application/json",
                   "X-Requested-With": "domovoi-core"}
        if token:
            headers["X-Device-Token"] = token
        timeout = httpx.Timeout(RELAY_DEADLINE_SEC, connect=CONNECT_TIMEOUT_SEC)
        async with (httpx_client or httpx.AsyncClient)(timeout=timeout, trust_env=False) as client:
            async with client.stream("POST", web_url() + BRIDGE_PATH, json=payload, headers=headers) as r:
                if r.status_code != 200:
                    log.warning("away chat: the web's bridge answered %s", r.status_code)
                    raise _Unavailable()
                async for raw in _lines(r):
                    try:
                        line = json.loads(raw)
                        kind, body, end = line["k"], line.get("b", {}), line.get("end") is True
                    except (ValueError, KeyError, TypeError):
                        continue
                    if kind not in ("resp", "event", "err", "bye") or not isinstance(body, dict):
                        continue
                    end = end or kind in ("err", "bye")
                    if not await _send(conn, req, kind, body, end=end):
                        return  # no listener to tell: nothing more to do
                    if end:
                        ended = True
                        return
    except asyncio.CancelledError:
        ended = True  # the connection ended: the web's run carries on
        raise
    except _Unavailable:
        pass
    except Exception as e:  # noqa: BLE001 — answered as unavailable
        log.warning("away chat: relaying a request failed (%s)", type(e).__name__)
    finally:
        if not ended:
            try:
                await _send(conn, req, "err", {"code": "unavailable",
                                               "detail": "Your house is busy. Try again shortly."}, end=True)
            except Exception:  # noqa: BLE001 — the listener is gone
                pass


async def leave(device_id: str, noise_hex: str) -> bool:
    """``away.leave``: the phone that proved ``noise_hex`` over the onion
    turns away chat off for itself, the same as its ``DELETE
    /api/away/enroll`` at home, and only for itself (the row goes only when
    the device AND its Noise key match: :func:`domovoi.away.store.revoke_own`).

    Once the DELETE has committed there is no row for that device and key,
    whatever else happens: the web's gate refuses the phone from then on,
    so it is answered ``RESP {}`` (this returns) even when rewriting the
    listener's ``phones.json`` fails; that rewrite, normally done here so
    the phone's next handshake is refused at once, is then left to the
    reconcile. True when a row went; a phone already gone is answered the
    same. Only a database that can't be reached raises (``ERR
    unavailable``: nothing happened, the phone may ask again).

    Tor's client keys follow in a reconcile (:func:`_kick_later`, asked for
    as soon as the row is gone): a batched reload, or, for the last phone,
    the restart or stop that takes ``hs-away`` away. Until then the phone's
    ``.auth`` file stays on disk, so the onion is never left configured
    with no clients (an onion with no clients is open: tor_config, S1)."""
    from domovoi.away import store as away_store
    from domovoi.db.session import session_scope
    from domovoi.friend_houses import service as svc

    noise = bytes.fromhex(noise_hex)
    async with session_scope() as s:
        dropped = await away_store.revoke_own(s, device_id, noise)
    if dropped:
        log.info("away chat: device %r turned it off from away", device_id)
        _kick_later()
    try:
        await svc.write_listener_files()
    except Exception as e:  # noqa: BLE001 — the reconcile writes it again
        log.warning("away chat: the phone list wasn't rewritten after a leave (%s)", type(e).__name__)
    return dropped


#: How long after a leave the reconcile runs. With no friends, the last
#: phone's reconcile stops the listener and Tor at once: the phone's RESP
#: and BYE must have gone out through them first.
LEAVE_KICK_DELAY_SEC = 5.0
_KICK_LATER: dict[str, asyncio.TimerHandle | None] = {"handle": None}


def _kick_later() -> None:
    """Reconcile (Tor's keys) :data:`LEAVE_KICK_DELAY_SEC` from now. A
    leave while one is waiting moves it on, so every leave's answer goes
    out before the reconcile that may stop the listener (it reads the
    phones fresh, so one serves them all)."""
    from domovoi.away import routes

    handle = _KICK_LATER["handle"]
    if handle is not None:
        handle.cancel()
    _KICK_LATER["handle"] = asyncio.get_running_loop().call_later(LEAVE_KICK_DELAY_SEC, _kick_now, routes)


def _kick_now(routes: Any) -> None:
    _KICK_LATER["handle"] = None
    routes.kick()


def _retrieved(task: asyncio.Task) -> None:
    """A leave whose connection ended: its failure, if any, is logged in
    :func:`leave` already."""
    if not task.cancelled():
        task.exception()


async def _leave(conn: int, req: int, device_id: str, noise_hex: str) -> None:
    """One ``away.leave`` request: :func:`leave` (in a task of its own,
    held in the service's task set: a connection that drops meanwhile
    doesn't stop it), then ``RESP {}``, after which the listener closes the
    phone's connections. ``ERR unavailable`` when the database couldn't be
    reached (the phone may ask again)."""
    from domovoi.friend_houses import service as svc

    work = asyncio.get_running_loop().create_task(leave(device_id, noise_hex), name="away-chat-leave")
    svc._PENDING.add(work)
    work.add_done_callback(svc._PENDING.discard)
    work.add_done_callback(_retrieved)
    ended = False
    try:
        await asyncio.shield(work)
        ended = True
        await _send(conn, req, "resp", {}, end=True)
    except asyncio.CancelledError:
        ended = True  # the connection ended; the leave itself carries on
        raise
    except Exception as e:  # noqa: BLE001 — answered as unavailable
        log.warning("away chat: a phone's leave failed (%s)", type(e).__name__)
    finally:
        if not ended:
            try:
                await _send(conn, req, "err", {"code": "unavailable",
                                               "detail": "Your house is busy. Try again shortly."}, end=True)
            except Exception:  # noqa: BLE001 — the listener is gone
                pass


class _Unavailable(Exception):
    pass


async def _lines(response: Any):
    """The response's lines, each at most :data:`LINE_MAX` bytes."""
    buf = b""
    async for chunk in response.aiter_bytes():
        buf += chunk
        if len(buf) > LINE_MAX and b"\n" not in buf:
            raise _Unavailable()
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line.strip():
                yield line
    if buf.strip():
        yield buf
