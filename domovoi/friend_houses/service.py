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


# Written by the installer's domovoi-fh-firewall.service after it applied
# its rules (scripts/linux/fh-firewall.sh): the Tor container's network can
# reach the internet only, not this box's own ports or the home network
# (threat-model finding 22). The core can't read the firewall itself (that
# needs root), so this root-owned file is how it knows.
FIREWALL_STATE = Path("/run/domovoi-fh-firewall/applied")


def firewall_state() -> str:
    """``in_place``, ``missing`` (a Linux server without the rules: an install
    from before them, or DOMOVOI_MANAGE_FH_FIREWALL=0), or ``unknown`` (not
    a Linux server)."""
    if sys.platform == "win32":
        return "unknown"
    try:
        text = FIREWALL_STATE.read_text(encoding="ascii")
    except OSError:
        return "missing"
    return "in_place" if "subnet=172.31.254.0/24" in text and "bridge=domovoi-fh0" in text else "missing"


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
        "firewall": firewall_state(),
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
        _stop_retries()  # first: nothing below may keep the house on Tor (R6 FH-2)
        try:
            await _stop_listener()
        except Exception:
            log.exception("Friend Houses: stopping the listener failed; stopping Tor anyway")
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
    # A block asked for a new address (blocking.py): delete this house's
    # onion keys so Tor makes a new one, restart rather than reload, and
    # come online again so every friend is told the new address.
    new_address = bool(_STATE.pop("new_address", False)) and w.main_onion
    if new_address:
        folder.forget_onion("hs-main")
        _STATE["was_on"] = False
        if action == "reload":
            action = "restart"
    folder.write_torrc(w)
    _sync_key_files(folder, peers)
    _write_invites(invites)

    if firewall_state() == "missing":
        log.warning("Friend Houses: starting its Tor container without the firewall rules "
                    "(%s is missing): update Domovoi or run the installer again", FIREWALL_STATE)
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


async def move_to_new_address() -> bool:
    """Give this house a new onion address (after a block). False when the
    private connection isn't running a house onion right now; the friends
    marked announce_needed are told on its next start either way."""
    old = _STATE.get("onion")
    if not tor_wanted() or old is None:
        return False
    _STATE["new_address"] = True
    await reconcile()
    return _STATE.get("onion") not in (None, old)


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


# The longest line the listener can print: an event with a 63 KiB envelope
# in hex, plus room. asyncio's default (64 KiB) let one large message end
# every event after it (audit R6 FH-2).
EVENT_LINE_MAX = 1024 * 1024

async def _ensure_listener(folder: tc.TorFolder) -> None:
    proc = _LISTENER["proc"]
    if proc is not None and proc.returncode is None:
        return
    _LISTENER["stopping"] = False
    _LISTENER["task"] = asyncio.get_running_loop().create_task(
        _run_listener(folder), name="friend-houses-listener"
    )


def _listener_command(folder: tc.TorFolder) -> list[str]:
    return [sys.executable, "-I", "-m", "domovoi.friend_houses.listener",
            "--keys", str(fid.keys_path()), "--folder", str(_listener_folder()),
            "--socket", str(folder.main_socket),
            "--pairing-socket", str(folder.pairing_socket)]


async def _run_listener(folder: tc.TorFolder) -> None:
    backoff = 1.0
    while not _LISTENER["stopping"]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *_listener_command(folder),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=EVENT_LINE_MAX,  # an envelope is up to 63 KiB, so ~126 KiB of hex
            )
        except OSError:
            log.exception("Friend Houses: the listener couldn't start")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
            continue
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
    """Every event the listener prints. Should the pipe fail (a line over
    the limit, say), the listener is stopped so the loop above starts a new
    one, instead of a live process whose events nobody reads (R6 FH-2)."""
    assert proc.stdout is not None
    try:
        async for raw in proc.stdout:
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            try:
                handle_event(event)
            except Exception:
                log.exception("Friend Houses: an event from the listener failed")
    except (ValueError, asyncio.LimitOverrunError, ConnectionError) as e:
        log.warning("Friend Houses: the listener's events stopped (%s); restarting it", e)
        if proc.returncode is None:
            proc.kill()


async def _read_log(proc: asyncio.subprocess.Process) -> None:
    assert proc.stderr is not None
    try:
        async for raw in proc.stderr:
            log.info("Friend Houses listener: %s", raw.decode(errors="replace").rstrip()[:300])
    except (ValueError, asyncio.LimitOverrunError, ConnectionError):
        if proc.returncode is None:
            proc.kill()


def handle_event(event: dict) -> None:
    """What the listener reported. Friend requests and accepts go to
    :mod:`domovoi.friend_houses.friending`, which checks them again before
    acting; messages (F3) and the outbox flush on "I'm back" hook in here."""
    kind = event.get("event")
    if kind in ("listening", "connected", "im_back"):
        house = str(event.get("house", ""))[:16]
        log.info("Friend Houses: %s%s", kind, f" ({house}…)" if house else "")
    if kind in ("connected", "im_back"):
        # Presence, and a friend's newer address (presence.py).
        from domovoi.friend_houses import presence

        try:
            house_key = bytes.fromhex(str(event.get("house", "")))
            statement = bytes.fromhex(str(event.get("statement", ""))) or None
        except ValueError:
            house_key, statement = b"", None
        if len(house_key) == 32:
            _event(presence.saw(house_key, statement), "friend-houses-presence")
    elif kind in ("request", "accepted"):
        from domovoi.friend_houses import friending

        _event(friending.on_event(event), "friend-houses-event")
    elif kind == "message":
        _event(_store_and_answer(event), "friend-houses-message")
    if kind == "im_back":
        from domovoi.friend_houses import messages

        try:
            _event(messages.flush(bytes.fromhex(str(event.get("house", "")))), "friend-houses-flush")
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


# At most this many listener events are handled at once. Each opens its own
# database connection (NullPool), so a flood from one house mustn't use up
# Postgres for the whole core (audit R6 FH-3). One over the limit is
# dropped: a message isn't acknowledged and is sent again, a request or an
# accept is retried by the other house, presence is seen next time.
EVENT_TASKS_MAX = 24
_EVENT_NAMES = ("friend-houses-presence", "friend-houses-event", "friend-houses-message",
                "friend-houses-flush")


def _event(coro, name: str) -> None:
    busy = sum(1 for t in _PENDING if not t.done() and t.get_name() in _EVENT_NAMES)
    if busy >= EVENT_TASKS_MAX:
        coro.close()
        log.warning("Friend Houses: %d events already running; dropped a %s", busy, name)
        return
    spawn(coro, name)


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
    from domovoi.friend_houses import friending, messages, presence

    while True:
        await asyncio.sleep(RETRY_EVERY_SEC)
        await _after_reconcile(friending.deliver_pending)
        await _after_reconcile(messages.flush_all)
        # Presence: ping friends not heard from in 10 minutes, and tell any
        # still waiting for this house's new address (after a block).
        await _after_reconcile(presence.check_all)


async def _shared_lists_loop() -> None:
    """Shared lists (F4): every few seconds, send what changed in a shared
    list here. A cheap query while nothing is shared."""
    from domovoi.friend_houses import shared_lists

    while True:
        await asyncio.sleep(shared_lists.SYNC_EVERY_SEC)
        try:
            if await shared_lists.any_active():
                await shared_lists.sync_local()
        except Exception:  # noqa: BLE001 — tried again on the next pass
            log.exception("Friend Houses: checking shared lists failed")


def _start_retries() -> None:
    loop = asyncio.get_running_loop()
    task = _RETRY["task"]
    if task is None or task.done():
        _RETRY["task"] = loop.create_task(_retry_loop(), name="friend-houses-retry")
    lists_task = _RETRY.get("lists")
    if lists_task is None or lists_task.done():
        _RETRY["lists"] = loop.create_task(_shared_lists_loop(), name="friend-houses-shared-lists")


def _stop_retries() -> None:
    for key in ("task", "lists"):
        task = _RETRY.get(key)
        if task is not None and not task.done():
            task.cancel()
        _RETRY[key] = None


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
        except Exception:  # its error must never stop "off" from working (R6 FH-2)
            log.exception("Friend Houses: the listener task had failed")
    _LISTENER.update(proc=None, task=None)


async def shutdown() -> None:
    """Core teardown: stop the listener. The Tor container keeps running so
    a core restart doesn't take the house offline; boot reconciles it."""
    _stop_retries()
    await _stop_listener()
