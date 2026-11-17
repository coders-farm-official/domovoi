"""Friend Houses in the running core: one place that makes it so.

Friend Houses ("serverless messaging and friend system between households")
is **off by default**. While it's off, nothing runs and nothing connects: no
keys are made, no image is built, no Tor daemon starts. An admin turns it on
with the ``friend_houses_enabled`` setting.

:func:`reconcile` is the only thing that changes what runs. It is scheduled
(never run twice at once) whenever something that matters changes: the
setting, the internet answer, the friend list, and once at boot. It:

1. decides what the Tor daemon should be doing
   (:func:`domovoi.friend_houses.tor_config.wanted`): stopped unless
   Friend Houses is on, the internet answer isn't "never" and there's a
   friend or an open friend file. "Never" **stops the container**, not just
   this module's calls (threat-model finding 26);
2. writes the daemon's config and key files from the friend list;
3. starts, stops, restarts or reloads the container
   (:mod:`domovoi.friend_houses.tor_runtime`), reloads at most once a
   minute (spike S1: each one briefly interrupts the onion);
4. keeps the inbound listener running as its own process while the house's
   onion exists (:mod:`domovoi.friend_houses.listener`), and writes the
   files it reads: the current friend list and this house's hello.

The container guard asks :func:`tor_wanted` what to expect of the
``domovoi-tor`` container.

Windows: the server runs Friend Houses on its Linux side (the WSL distro);
a core started natively on Windows (a developer's box) reports it as
unavailable rather than half-running it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import listener as fl
from domovoi.friend_houses import session as fs
from domovoi.friend_houses import tor_config as tc

log = logging.getLogger(__name__)

GUARD_HOLD = "core:friend-houses"
RELOAD_MIN_INTERVAL_SEC = 60.0
ONION_WAIT_SEC = 90.0

_LOCK = asyncio.Lock()
_PENDING: set[asyncio.Task] = set()
_STATE: dict[str, Any] = {
    "applied": None,        # the last Wanted put into effect
    "last_reload": 0.0,
    "status": "off",        # off | starting | on | stopped | unavailable | error
    "detail": None,
    "onion": None,
    "pairing": None,
    "hello": None,
}
_LISTENER: dict[str, Any] = {"proc": None, "task": None, "stopping": False}


def _root() -> Path:
    from domovoi.admin_auth import CONFIG_DIR

    return CONFIG_DIR / "friend-houses"


def _folder() -> tc.TorFolder:
    return tc.TorFolder(_root() / "tor")


def _listener_folder() -> Path:
    return _root() / "listener"


def _marker_path() -> Path:
    return _root() / "state-marker"


def tor_wanted() -> bool:
    """Whether the Tor container should be running, as last reconciled."""
    applied = _STATE["applied"]
    return bool(applied is not None and applied.run)


def status() -> dict[str, Any]:
    """For the admin status route: plain facts, no secrets."""
    keys = fid.load() if sys.platform != "win32" else None
    return {
        "status": _STATE["status"],
        "detail": _STATE["detail"],
        "fingerprint": keys.fingerprint if keys else None,
        "onion_ready": _STATE["onion"] is not None,
        "pairing_ready": _STATE.get("pairing") is not None,
        "listener_running": _LISTENER["proc"] is not None and _LISTENER["proc"].returncode is None,
    }


def schedule_reconcile() -> None:
    """The reapply hook for ``friend_houses_enabled`` and ``internet_access``,
    and the boot start. A no-op without a running loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(reconcile(), name="friend-houses-reconcile")
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


async def reconcile_within(timeout: float = 60.0) -> bool:
    """Reconcile, but give the caller (an admin's request) back control
    after ``timeout`` seconds while it carries on in the background (the
    first start builds the Tor image). True when it finished in time."""
    task = asyncio.get_running_loop().create_task(reconcile(), name="friend-houses-reconcile")
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def reconcile() -> None:
    async with _LOCK:
        try:
            await _reconcile()
        except Exception as e:  # noqa: BLE001 — reported on the status, retried on the next change
            _STATE["status"], _STATE["detail"] = "error", str(e)[:300]
            log.exception("Friend Houses: reconcile failed")


@dataclass(frozen=True)
class _Peer:
    """One entry in Tor's key folders: a friend, or a house this one asked."""

    id: str
    reach_public: bytes
    reach_private: bytes | None
    onion_key: bytes | None
    session_key: bytes
    house_key: bytes
    pending: bool


async def _inputs() -> tuple[list[_Peer], dict[str, float]]:
    """The friends, the houses this one asked, and the open friend files."""
    from domovoi.db.session import session_scope
    from domovoi.friend_houses import store

    async with session_scope() as s:
        friends = await store.active_friends(s)
        asked = await store.requests(s, direction="out", states=("unsent", "sent"))
        invites = await store.open_invites(s)
    peers = [_Peer(f.id, f.reach_public, f.reach_private, f.onion_key, f.session_key,
                   f.house_key, False) for f in friends]
    peers += [_Peer(r["id"], r["reach_public"], None, None, r["session_key"], r["house_key"], True)
              for r in asked if r["reach_public"] is not None]
    return peers, {i["id"]: i["expires_at"].timestamp() for i in invites}


async def _reconcile() -> None:
    from domovoi import egress
    from domovoi.config import settings
    from domovoi.container_guard import holds
    from domovoi.friend_houses import tor_runtime as rt

    enabled = bool(getattr(settings, "friend_houses_enabled", False))
    if sys.platform == "win32":
        _STATE.update(status="unavailable" if enabled else "off",
                      detail="Friend Houses runs on the server's Linux side" if enabled else None)
        return
    policy = egress.policy()
    peers: list[_Peer] = []
    invites: dict[str, float] = {}
    if enabled:
        peers, invites = await _inputs()
    w = tc.wanted(enabled=enabled, internet_policy=policy, friends=len(peers),
                  open_invites=len(invites))
    before = _STATE["applied"]
    action = tc.plan_change(before, w)

    if not w.run:
        await _stop_listener()
        _stop_retries()
        # At boot (nothing applied yet) a container left running from before
        # is stopped too, but only on a house that ever set Friend Houses
        # up: an untouched house never even asks Docker.
        if action == "stop" or (before is None and _folder().root.exists()
                                and await rt.state() == "running"):
            with holds.holding(GUARD_HOLD, "Friend Houses is stopping", settle_sec=60):
                await rt.stop()
        if _folder().root.exists():
            _folder().forget_onion("hs-pairing")  # the next friend file gets a new address
        _STATE.update(applied=w, onion=None, pairing=None, was_on=False,
                      status="off" if not enabled else "stopped",
                      detail=None if not enabled else _why_stopped(policy, len(peers)))
        return

    keys = fid.load() or fid.create()
    folder = _folder()
    folder.ensure()
    if before is not None and before.pairing_onion and not w.pairing_onion:
        folder.forget_onion("hs-pairing")
    folder.write_torrc(w)
    _sync_key_files(folder, peers)
    _write_invites(invites)

    _STATE.update(status="starting", detail=None)
    with holds.holding(GUARD_HOLD, "Friend Houses is starting its connection", settle_sec=120):
        if action == "start":
            await rt.start(data=folder.data, run=folder.run)
        elif action == "restart":
            await rt.restart()
        elif action == "reload":
            wait = RELOAD_MIN_INTERVAL_SEC - (time.monotonic() - _STATE["last_reload"])
            if wait > 0:
                await asyncio.sleep(wait)  # batch: later changes ride this reload
            await rt.reload()
            _STATE["last_reload"] = time.monotonic()
    _STATE["applied"] = w

    onion = await _wait_for_onion(folder, "hs-main") if w.main_onion else None
    _STATE["onion"] = onion
    _STATE["pairing"] = await _wait_for_onion(folder, "hs-pairing") if w.pairing_onion else None
    await _write_listener_files(keys, onion, peers)
    await _ensure_listener(folder)
    _start_retries()
    came_online = onion is not None and not _STATE.get("was_on")
    _STATE.update(status="on", detail=None, was_on=onion is not None)
    if came_online:
        from domovoi.friend_houses import messages

        # "I'm back": friends flush their outboxes to this house now.
        spawn(_after_reconcile_then(messages.say_im_back), "friend-houses-im-back")


def _why_stopped(policy: str, friends: int) -> str:
    if policy == "never":
        return "The internet answer is \"never\", so the private connection is stopped."
    if friends == 0:
        return "No friends or friend files yet, so nothing needs to run."
    return "Stopped."


def _sync_key_files(folder: tc.TorFolder, peers: list[_Peer]) -> None:
    """Make Tor's key folders say exactly what the friend list says."""
    wanted_ids = {p.id for p in peers}
    held_ids = {p.id for p in peers if p.reach_private is not None and p.onion_key is not None}
    for stale in folder.authorized() - wanted_ids:
        folder.deauthorize(stale)
    for stale in folder.held() - held_ids:
        folder.drop_key_for(stale)
    for p in peers:
        folder.authorize(p.id, p.reach_public)
        if p.id in held_ids:
            assert p.reach_private is not None and p.onion_key is not None
            folder.hold_key_for(p.id, p.onion_key, p.reach_private)


def _write_invites(invites: dict[str, float]) -> None:
    folder = _listener_folder()
    folder.mkdir(parents=True, exist_ok=True)
    tc._chmod(folder, 0o700)
    tc._write_private(folder / fl.INVITES_FILE, json.dumps(invites))


async def _wait_for_onion(folder: tc.TorFolder, service: str = "hs-main") -> bytes:
    deadline = time.monotonic() + ONION_WAIT_SEC
    while time.monotonic() < deadline:
        onion = folder.onion_key(service)
        if onion is not None:
            return onion
        await asyncio.sleep(1.0)
    raise RuntimeError("the private connection didn't create this house's address in time")


async def current_epoch() -> bytes:
    """This run's sending epoch, starting a new one after a rollback
    (identity.resolve_epoch)."""
    from domovoi.db.session import session_scope
    from domovoi.friend_houses import store

    path = _marker_path()
    try:
        file_marker = path.read_text(encoding="ascii").strip() or None
    except OSError:
        file_marker = None
    async with session_scope() as s:
        db_epoch, db_marker = await store.epoch_row(s)
        epoch, marker, changed = fid.resolve_epoch(db_epoch, db_marker, file_marker)
        if changed:
            await store.set_epoch(s, epoch, marker)
    if changed:
        tc._write_private(path, marker + "\n")
        log.info("Friend Houses: started a new sending epoch")
    return epoch


async def _write_listener_files(keys: fid.HouseKeys, onion: bytes | None, peers: list[_Peer]) -> None:
    """The friend list (friends, and houses this one asked: those may only
    deliver an accept) and this house's hello, for the listener."""
    folder = _listener_folder()
    folder.mkdir(parents=True, exist_ok=True)
    tc._chmod(folder, 0o700)
    tc._write_private(folder / fl.FRIENDS_FILE, json.dumps(
        {p.session_key.hex(): {"house": p.house_key.hex(), "pending": p.pending} for p in peers}))
    path = folder / fl.HELLO_FILE
    if onion is None:
        path.unlink(missing_ok=True)
        _STATE["hello"] = None
        return
    hello = fs.Hello(keys.address_statement(onion, int(time.time())), await current_epoch())
    _STATE["hello"] = hello
    tmp = path.with_name(".hello.tmp")
    tmp.write_bytes(hello.to_bytes())
    tc._chmod(tmp, 0o600)
    tmp.replace(path)


async def my_hello() -> fs.Hello | None:
    """This house's hello for outgoing connections (the same one its
    listener answers with), or None while it has no onion."""
    return _STATE.get("hello")


def pairing_onion() -> bytes | None:
    return _STATE.get("pairing")


def socks_path() -> Path:
    return _folder().socks_path


# ─── the listener process ─────────────────────────────────────────────────


async def _ensure_listener(folder: tc.TorFolder) -> None:
    proc = _LISTENER["proc"]
    if proc is not None and proc.returncode is None:
        return
    _LISTENER["stopping"] = False
    _LISTENER["task"] = asyncio.get_running_loop().create_task(
        _run_listener(folder), name="friend-houses-listener"
    )


async def _run_listener(folder: tc.TorFolder) -> None:
    backoff = 1.0
    while not _LISTENER["stopping"]:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-m", "domovoi.friend_houses.listener",
            "--keys", str(fid.keys_path()), "--folder", str(_listener_folder()),
            "--socket", str(folder.main_socket),
            "--pairing-socket", str(folder.pairing_socket),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _LISTENER["proc"] = proc
        started = time.monotonic()
        await asyncio.gather(_read_events(proc), _read_log(proc))
        await proc.wait()
        if _LISTENER["stopping"]:
            break
        log.warning("Friend Houses: the listener stopped (exit %s); starting it again", proc.returncode)
        backoff = 1.0 if time.monotonic() - started > 60 else min(backoff * 2, 60.0)
        await asyncio.sleep(backoff)
    _LISTENER["proc"] = None


async def _read_events(proc: asyncio.subprocess.Process) -> None:
    assert proc.stdout is not None
    async for raw in proc.stdout:
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        handle_event(event)


async def _read_log(proc: asyncio.subprocess.Process) -> None:
    assert proc.stderr is not None
    async for raw in proc.stderr:
        log.info("Friend Houses listener: %s", raw.decode(errors="replace").rstrip()[:300])


def handle_event(event: dict) -> None:
    """What the listener reported. Friend requests and accepts go to
    :mod:`domovoi.friend_houses.friending`, which checks them again before
    acting; messages (F3) and the outbox flush on "I'm back" hook in here."""
    kind = event.get("event")
    if kind in ("listening", "connected", "im_back"):
        house = str(event.get("house", ""))[:16]
        log.info("Friend Houses: %s%s", kind, f" ({house}…)" if house else "")
    elif kind in ("request", "accepted"):
        from domovoi.friend_houses import friending

        spawn(friending.on_event(event), "friend-houses-event")
    elif kind == "message":
        spawn(_store_and_answer(event), "friend-houses-message")
    if kind == "im_back":
        from domovoi.friend_houses import messages

        try:
            spawn(messages.flush(bytes.fromhex(str(event.get("house", "")))), "friend-houses-flush")
        except ValueError:
            pass


async def _store_and_answer(event: dict) -> None:
    """Store a message, then tell the listener whether to acknowledge it.
    The ack goes only after the row is committed: a crash in between means
    the sender sends it again, and the copy is de-duplicated."""
    from domovoi.friend_houses import messages

    stored = False
    try:
        stored = await messages.on_message(event)
    except Exception:  # noqa: BLE001 — not acknowledged: the sender keeps it
        log.exception("Friend Houses: storing a message failed")
    msg_id = _msg_id_of(event)
    proc = _LISTENER["proc"]
    if msg_id and proc is not None and proc.stdin is not None and proc.returncode is None:
        try:
            answer = "stored" if stored else "refused"
            proc.stdin.write((f"{answer} {msg_id}" + chr(10)).encode("ascii"))
            await proc.stdin.drain()
        except (ConnectionError, RuntimeError):
            pass


def _msg_id_of(event: dict) -> str | None:
    try:
        raw = bytes.fromhex(str(event.get("envelope", "")))
        return raw[66:82].hex() if len(raw) >= 82 else None
    except ValueError:
        return None


def spawn(coro, name: str) -> None:
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


# ─── retries: unsent requests and accepts ─────────────────────────────────

RETRY_EVERY_SEC = 300.0
_RETRY: dict[str, Any] = {"task": None}


def schedule_deliveries() -> None:
    """Try pending requests and accepts now (after a reconcile), in the
    background."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    from domovoi.friend_houses import friending

    spawn(_after_reconcile(friending.deliver_pending), "friend-houses-deliver")


async def _after_reconcile(fn) -> None:
    async with _LOCK:  # wait for a reconcile in flight: the onion must exist
        pass
    try:
        await fn()
    except Exception:  # noqa: BLE001 — retried on the next pass
        log.exception("Friend Houses: delivering pending requests failed")


async def _after_reconcile_then(fn) -> None:
    await asyncio.sleep(5)  # let the listener settle first
    await _after_reconcile(fn)


async def _retry_loop() -> None:
    from domovoi.friend_houses import friending, messages

    while True:
        await asyncio.sleep(RETRY_EVERY_SEC)
        await _after_reconcile(friending.deliver_pending)
        await _after_reconcile(messages.flush_all)


def _start_retries() -> None:
    task = _RETRY["task"]
    if task is None or task.done():
        _RETRY["task"] = asyncio.get_running_loop().create_task(_retry_loop(), name="friend-houses-retry")


def _stop_retries() -> None:
    task = _RETRY["task"]
    if task is not None and not task.done():
        task.cancel()
    _RETRY["task"] = None


async def _stop_listener() -> None:
    _LISTENER["stopping"] = True
    proc = _LISTENER["proc"]
    if proc is not None and proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except asyncio.TimeoutError:
            proc.kill()
    task = _LISTENER["task"]
    if task is not None:
        try:
            await asyncio.wait_for(task, 15)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
    _LISTENER.update(proc=None, task=None)


async def shutdown() -> None:
    """Core teardown: stop the listener. The Tor container keeps running so
    a core restart doesn't take the house offline; boot reconciles it."""
    _stop_retries()
    await _stop_listener()
