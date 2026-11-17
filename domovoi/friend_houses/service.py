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


async def reconcile() -> None:
    async with _LOCK:
        try:
            await _reconcile()
        except Exception as e:  # noqa: BLE001 — reported on the status, retried on the next change
            _STATE["status"], _STATE["detail"] = "error", str(e)[:300]
            log.exception("Friend Houses: reconcile failed")


async def _reconcile() -> None:
    from domovoi import egress
    from domovoi.config import settings
    from domovoi.container_guard import holds
    from domovoi.db.session import session_scope
    from domovoi.friend_houses import store
    from domovoi.friend_houses import tor_runtime as rt

    enabled = bool(getattr(settings, "friend_houses_enabled", False))
    if sys.platform == "win32":
        _STATE.update(status="unavailable" if enabled else "off",
                      detail="Friend Houses runs on the server's Linux side" if enabled else None)
        return
    policy = egress.policy()
    friends: list[store.FriendRow] = []
    if enabled:
        async with session_scope() as s:
            friends = await store.active_friends(s)
    w = tc.wanted(enabled=enabled, internet_policy=policy, friends=len(friends), open_invites=0)
    before = _STATE["applied"]
    action = tc.plan_change(before, w)

    if not w.run:
        await _stop_listener()
        # At boot (nothing applied yet) a container left running from before
        # is stopped too, but only on a house that ever set Friend Houses
        # up: an untouched house never even asks Docker.
        if action == "stop" or (before is None and _folder().root.exists()
                                and await rt.state() == "running"):
            with holds.holding(GUARD_HOLD, "Friend Houses is stopping", settle_sec=60):
                await rt.stop()
        _STATE.update(applied=w, onion=None,
                      status="off" if not enabled else "stopped",
                      detail=None if not enabled else _why_stopped(policy, len(friends)))
        return

    keys = fid.load() or fid.create()
    folder = _folder()
    folder.ensure()
    folder.write_torrc(w)
    _sync_key_files(folder, friends)

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
                folder.write_torrc(w)
                _sync_key_files(folder, friends)
            await rt.reload()
            _STATE["last_reload"] = time.monotonic()
    _STATE["applied"] = w

    if w.main_onion:
        onion = await _wait_for_onion(folder)
        _STATE["onion"] = onion
        await _write_listener_files(keys, onion, friends)
        await _ensure_listener(folder)
    else:
        _STATE["onion"] = None
        await _stop_listener()
    _STATE.update(status="on", detail=None)


def _why_stopped(policy: str, friends: int) -> str:
    if policy == "never":
        return "The internet answer is \"never\", so the private connection is stopped."
    if friends == 0:
        return "No friends yet, so nothing needs to run."
    return "Stopped."


def _sync_key_files(folder: tc.TorFolder, friends: list) -> None:
    """Make Tor's key folders say exactly what the friend list says."""
    wanted_ids = {f.id for f in friends}
    for stale in folder.authorized() - wanted_ids:
        folder.deauthorize(stale)
        folder.drop_key_for(stale)
    for f in friends:
        folder.authorize(f.id, f.reach_public)
        if f.reach_private is not None:
            folder.hold_key_for(f.id, f.onion_key, f.reach_private)


async def _wait_for_onion(folder: tc.TorFolder) -> bytes:
    deadline = time.monotonic() + ONION_WAIT_SEC
    while time.monotonic() < deadline:
        onion = folder.onion_key()
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


async def _write_listener_files(keys: fid.HouseKeys, onion: bytes, friends: list) -> None:
    folder = _listener_folder()
    folder.mkdir(parents=True, exist_ok=True)
    tc._chmod(folder, 0o700)
    hello = fs.Hello(keys.address_statement(onion, int(time.time())), await current_epoch())
    tc._write_private(folder / fl.FRIENDS_FILE,
                      json.dumps({f.session_key.hex(): f.house_key.hex() for f in friends}))
    path = folder / fl.HELLO_FILE
    tmp = path.with_name(".hello.tmp")
    tmp.write_bytes(hello.to_bytes())
    tc._chmod(tmp, 0o600)
    tmp.replace(path)


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
    """What the listener reported. F1 only logs; messages (F3) and the
    outbox flush on "I'm back" hook in here."""
    kind = event.get("event")
    if kind in ("listening", "connected", "im_back"):
        house = str(event.get("house", ""))[:16]
        log.info("Friend Houses: %s%s", kind, f" ({house}…)" if house else "")


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
    await _stop_listener()
