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
2. writes the daemon's config and key files from the friend list, and
   the listener's files through :func:`write_listener_files`, their only
   writer (M2: it reads the database under its own lock each time, so a
   reconcile in flight can't put back an older friend list);
3. starts, stops, restarts or reloads the container
   (:mod:`domovoi.friend_houses.tor_runtime`), reloads at most once a
   minute (spike S1: each one briefly interrupts the onion);
4. keeps the inbound listener running in its own container while the
   house's onion exists (:mod:`domovoi.friend_houses.listener`, run by
   :mod:`domovoi.friend_houses.listener_runtime`), and writes the files it
   reads: the current friend list and this house's hello.

The container guard asks :func:`tor_wanted` what to expect of the
``domovoi-tor`` container, and pins the listener's image.

Away chat (1.5.0, domovoi/away/) rides the same reconcile: its onion
(``hs-away``) is wanted while "Chat from away" is on, the internet answer
isn't "never" and at least one phone has enrolled, whether or not Friend
Houses is on. The phones' Tor keys go in ``hs-away/authorized_clients``,
their Noise keys in the listener's ``phones.json`` (written first, so a
phone removed is refused from its next handshake), and the listener's
``away.sock`` hands each request to :mod:`domovoi.away.bridge`.

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
_LISTENER: dict[str, Any] = {"proc": None, "task": None, "stopping": False, "image": None,
                             # (Friend Houses' sockets, away chat's socket) it serves
                             "mode": (True, False)}
# Away chat: "starting" while its onion is being brought up, "ready" once
# Tor runs it, None when it doesn't; ``authorized`` is the phones' file ids
# Tor has been given, ``keys`` the (file id, Tor key) pairs it was given,
# ``publishing`` file id -> (monotonic deadline, Noise key) for a phone
# whose key Tor took recently (away_status), ``phones`` the enrolments
# last applied.
_AWAY: dict[str, Any] = {"state": None, "authorized": frozenset(), "keys": frozenset(), "publishing": {},
                         "phones": (), "watch": None}
AWAY_WATCH_SEC = 30.0
# After Tor takes a phone's client key, it builds and uploads a new
# descriptor for the away onion, and the phone can't find the onion until
# that descriptor is out (40-90 s seen on phones, batching included). Tor
# runs with no control port (tor_config: core never talks to one), and its
# notice-level log doesn't say when an upload lands, so the upload can't be
# seen: away_status says "publishing" for these windows instead, or until a
# request from that phone arrives over the onion (it found it).
AWAY_PUBLISH_AFTER_RELOAD_SEC = 60.0
AWAY_PUBLISH_AFTER_START_SEC = 120.0  # a start or restart bootstraps first


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
    if "subnet=172.31.254.0/24" not in text or "bridge=domovoi-fh0" not in text:
        return "missing"
    # The unit's watcher rewrites the file every minute while the rules are
    # in place; an old one means nobody is watching (audit R6 FH-14).
    import re as _re

    m = _re.search(r"^checked=(\d+)$", text, _re.MULTILINE)
    if m is None or time.time() - int(m.group(1)) > FIREWALL_STALE_SEC:
        return "missing"
    return "in_place"


FIREWALL_STALE_SEC = 300


def _firewall_blocks_start() -> bool:
    """On a Linux server, Tor doesn't start without the firewall rules
    (audit R6 FH-14), unless the administrator manages the firewall
    themself (DOMOVOI_MANAGE_FH_FIREWALL=0, the installer's opt-out)."""
    import os

    if sys.platform == "win32" or os.environ.get("DOMOVOI_MANAGE_FH_FIREWALL") == "0":
        return False
    return firewall_state() == "missing"


def status() -> dict[str, Any]:
    """For the admin status route: plain facts, no secrets."""
    keys = fid.load() if sys.platform != "win32" else None
    return {
        "status": _STATE["status"],
        # While on, a listener that can't be set up is said here: the page
        # shows ``detail`` (friends can't reach the house meanwhile).
        "detail": _STATE["detail"] or (_LISTENER.get("problem") if _STATE["status"] == "on" else None),
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
        except Exception as e:  # noqa: BLE001 — reported on the status, and retried
            _STATE["status"], _STATE["detail"] = "error", str(e)[:300]
            log.exception("Friend Houses: reconcile failed")
            _retry_reconcile_later()


RECONCILE_RETRY_SEC = 120.0


def _retry_reconcile_later() -> None:
    """A failed reconcile (a Docker error, say) is tried again by itself,
    so Tor never keeps an old friend list in force (audit R6 FH-10)."""
    handle = _STATE.get("retry_handle")
    if handle is not None and not handle.cancelled():
        handle.cancel()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _STATE["retry_handle"] = loop.call_later(RECONCILE_RETRY_SEC, schedule_reconcile)


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
    # The plugins this friend may mail (its "apps" in friends.json): set by
    # write_listener_files from the toggle and the allowed pairs.
    apps: tuple[str, ...] = ()


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


async def _away_inputs() -> tuple[bool, list[Any]]:
    """Whether "Chat from away" is on, and the current phones (none while
    it is off): domovoi/away/store.py."""
    from domovoi.away import store as away_store
    from domovoi.db.session import session_scope

    async with session_scope() as s:
        on = await away_store.setting_on(s)
        phones = await away_store.current_phones(s) if on else []
    return on, phones


def away_status(device_id: str | None = None) -> str | None:
    """Away chat's state for the web's ``GET /api/away/status``: ``ready``
    once Tor runs the away onion (with ``device_id``: and has that phone's
    key, and has had time to publish it), ``publishing`` for a while after
    Tor took that phone's key (:data:`AWAY_PUBLISH_AFTER_RELOAD_SEC`: the
    descriptor carrying it is probably still on its way, and the phone
    can't find the onion yet), ``starting`` while it is being brought up or
    that phone's key is waiting for the next batched reload, None when it
    isn't running."""
    state = _AWAY["state"]
    if state != "ready" or device_id is None:
        return state
    from domovoi.away.store import Phone

    file_id = Phone(device_id, "", b"").file_id
    if file_id not in _AWAY["authorized"]:
        return "starting"
    publishing = _AWAY["publishing"].get(file_id)
    if publishing is not None and time.monotonic() < publishing[0]:
        return "publishing"
    return "ready"


def away_reached(device_id: str, noise_hex: str) -> None:
    """A request from this phone came over the onion: it found the
    descriptor, so it isn't "publishing" any more. Only for the Noise key
    the phone enrolled with when Tor took its key (an enrolment since then
    waits out its own window)."""
    from domovoi.away.store import Phone

    file_id = Phone(device_id, "", b"").file_id
    publishing = _AWAY["publishing"].get(file_id)
    if publishing is not None and publishing[1] == noise_hex:
        _AWAY["publishing"] = {k: v for k, v in _AWAY["publishing"].items() if k != file_id}


def _away_applied(phones: list[Any], action: str) -> None:
    """Tor runs the away onion with ``phones``' keys, just applied by
    ``action``: each key Tor didn't have before (every key, after a start
    or a restart) is "publishing" for its window."""
    now = time.monotonic()
    keys = frozenset((p.file_id, p.tor_auth_pub) for p in phones)
    fresh_start = action in ("start", "restart")
    window = AWAY_PUBLISH_AFTER_START_SEC if fresh_start else AWAY_PUBLISH_AFTER_RELOAD_SEC
    fresh = keys if fresh_start else keys - _AWAY["keys"]
    ids = {file_id for file_id, _ in keys}
    publishing = {k: v for k, v in _AWAY["publishing"].items() if k in ids and v[0] > now}
    noise = {p.file_id: p.noise_pub.hex() for p in phones}
    for file_id, _ in fresh:
        publishing[file_id] = (now + window, noise[file_id])
    _AWAY.update(state="ready", authorized=frozenset(ids), keys=keys, publishing=publishing)


def _away_down() -> None:
    _AWAY.update(state=None, authorized=frozenset(), keys=frozenset(), publishing={})


async def _reconcile() -> None:
    from domovoi import egress
    from domovoi.config import settings
    from domovoi.container_guard import holds
    from domovoi.friend_houses import tor_runtime as rt

    enabled = bool(getattr(settings, "friend_houses_enabled", False))
    if sys.platform == "win32":
        _STATE.update(status="unavailable" if enabled else "off",
                      detail="Friend Houses runs on the server's Linux side" if enabled else None)
        _away_down()
        return
    policy = egress.policy()
    peers: list[_Peer] = []
    invites: dict[str, float] = {}
    away_on, phones = False, []
    # "never" is decided before the database is read, so an outage can't
    # keep Tor running against the internet answer.
    if policy != "never":
        if enabled:
            peers, invites = await _inputs()
        away_on, phones = await _away_inputs()
    w = tc.wanted(enabled=enabled, internet_policy=policy, friends=len(peers),
                  open_invites=len(invites), away_on=away_on, away_phones=len(phones))
    before = _STATE["applied"]
    action = tc.plan_change(before, w)
    fh_wanted = w.main_onion or w.pairing_onion

    if not w.run:
        _stop_retries()  # first: nothing below may keep the house on Tor (R6 FH-2)
        _stop_away_watch()
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
        _away_down()
        _AWAY["phones"] = ()
        return
    # From here Tor runs. hs-away is in the torrc only while a phone has a
    # client key on disk (tc.wanted, and the check below): an onion with no
    # clients is open (S1), so the last phone going takes it away with a
    # restart (or the stop above), never a reload.

    if fh_wanted and fid.load() is None:
        fid.create()
    if not fh_wanted:
        _stop_retries()  # Tor runs for away chat alone: no friends' work
    folder = _folder()
    folder.ensure()
    if w.away_onion:
        _ensure_away_keys(folder)
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
    # The listener's lists first: a removed friend is refused from its next
    # connection however long Tor takes, or if Tor fails (audit R6 FH-10).
    # Read fresh from the database by the one writer (M2), never from the
    # snapshot above.
    await write_listener_files()
    # The client keys first, then the torrc that names the onions: a write
    # that fails leaves Tor's config as it was (review L2).
    _sync_key_files(folder, peers)
    _sync_phone_files(folder, phones if w.away_onion else [])
    if w.away_onion and not folder.phones_authorized():
        raise RuntimeError("the phones' Tor keys aren't on disk; the away onion isn't configured")
    folder.write_torrc(w)
    _AWAY["phones"] = _away_fingerprint(away_on, phones)

    if _firewall_blocks_start():
        log.warning("Friend Houses: not starting its Tor container without the firewall rules "
                    "(%s is missing or old)", FIREWALL_STATE)
        if await rt.state() == "running":
            with holds.holding(GUARD_HOLD, "Friend Houses is stopping", settle_sec=60):
                await rt.stop()
        _STATE.update(applied=None, onion=None, pairing=None, was_on=False,
                      status="error" if enabled else "off",
                      detail="The firewall rules that keep Friend Houses away from your home network "
                             "aren't in place. Update Domovoi, or run its installer again."
                             if enabled else None)
        _away_down()
        _retry_reconcile_later()
        return
    if enabled:
        _STATE.update(status="starting", detail=None)
    if w.away_onion and _AWAY["state"] != "ready":
        _AWAY["state"] = "starting"
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
    if w.away_onion:
        _away_applied(phones, action)
        _start_away_watch()
    else:
        _away_down()
        _stop_away_watch()

    onion = await _wait_for_onion(folder, "hs-main") if w.main_onion else None
    _STATE["onion"] = onion
    _STATE["pairing"] = await _wait_for_onion(folder, "hs-pairing") if w.pairing_onion else None
    await write_listener_files(onion=onion)
    await _ensure_listener(folder, mode=(fh_wanted, w.away_onion))
    if fh_wanted:
        _start_retries()
    came_online = onion is not None and not _STATE.get("was_on")
    if fh_wanted:
        _STATE.update(status="on", detail=None, was_on=onion is not None)
    else:
        _STATE.update(status="stopped" if enabled else "off",
                      detail=_why_stopped(policy, len(peers)) if enabled else None, was_on=False)
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


def _sync_phone_files(folder: tc.TorFolder, phones: list[Any]) -> None:
    """Make ``hs-away/authorized_clients`` say exactly which phones are
    enrolled (one ``<phone>.auth`` each; :class:`domovoi.away.store.Phone`)."""
    wanted_ids = {p.file_id for p in phones}
    for stale in folder.phones_authorized() - wanted_ids:
        folder.deauthorize_phone(stale)
    for p in phones:
        folder.authorize_phone(p.file_id, p.tor_auth_pub)


def _ensure_away_keys(folder: tc.TorFolder) -> None:
    """The house's away keys exist (the web process usually made them at
    the first enrolment), and Tor has the away onion's key in its format
    before it first starts, so the address phones were given is the one
    Tor publishes."""
    from domovoi.away import keys as away_keys

    away_keys.noise_key(create=True)
    seed = away_keys.onion_seed(create=True)
    assert seed is not None
    away_keys.write_tor_keys(folder.data / "hs-away", seed)


def _away_fingerprint(on: bool, phones: list[Any]) -> tuple:
    return (on, tuple((p.device_id, p.tor_auth_pub, p.noise_pub) for p in phones))


def _start_away_watch() -> None:
    """While the away onion runs: every :data:`AWAY_WATCH_SEC` seconds,
    reconcile when the enrolments or the setting changed and nobody said
    so (the web process tells the core on every change; this catches a
    word that didn't arrive)."""
    task = _AWAY.get("watch")
    if task is None or task.done():
        _AWAY["watch"] = asyncio.get_running_loop().create_task(_away_watch(), name="away-chat-watch")


def _stop_away_watch() -> None:
    task = _AWAY.get("watch")
    if task is not None and not task.done() and task is not asyncio.current_task():
        task.cancel()
    _AWAY["watch"] = None


async def _away_watch() -> None:
    while True:
        await asyncio.sleep(AWAY_WATCH_SEC)
        try:
            on, phones = await _away_inputs()
        except Exception as e:  # noqa: BLE001 — tried again on the next pass
            log.warning("away chat: checking the phones failed (%s)", type(e).__name__)
            continue
        if _away_fingerprint(on, phones) != _AWAY["phones"]:
            schedule_reconcile()


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


#: The listener files' own lock (M2). current_epoch() runs under it too.
_FILES_LOCK = asyncio.Lock()
_KEEP = object()


async def current_epoch() -> bytes:
    """This run's sending epoch, starting a new one after a rollback
    (identity.resolve_epoch). Under the listener-file lock, so it is never
    an unlocked read-then-write (M1)."""
    async with _FILES_LOCK:
        return await _current_epoch_locked()


async def _current_epoch_locked(*, fresh_marker: bool = False) -> bytes:
    import secrets

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
        elif fresh_marker:
            # M1: a fresh marker at every core start, database first, then
            # the file. A dump restored later carries an older marker than
            # the file, so it starts a new epoch; a crash between the two
            # writes does too, which is safe.
            marker = secrets.token_hex(16)
            await store.set_marker(s, marker)
    if changed or fresh_marker:
        path.parent.mkdir(parents=True, exist_ok=True)
        tc._write_private(path, marker + "\n")
    if changed:
        log.info("Friend Houses: started a new sending epoch")
        await _epoch_changed()
    return epoch


async def _epoch_changed() -> None:
    """This house's epoch changed: every friend with apps on is told again."""
    try:
        from domovoi.db.session import session_scope
        from domovoi.friend_houses import plugin_mail

        async with session_scope() as s:
            await plugin_mail.set_caps_needed(s, all_apps_on=True)
    except Exception:  # noqa: BLE001 — the 3-hour refresh tells them anyway
        log.exception("Friend Houses: marking friends for a new CAPS failed")


async def refresh_marker() -> None:
    """Core start (M1): resolve the epoch, then write a fresh restore marker
    (database, then file). Only on a house that set Friend Houses up."""
    if sys.platform == "win32" or fid.load() is None:
        return
    try:
        async with _FILES_LOCK:
            await _current_epoch_locked(fresh_marker=True)
    except Exception:  # noqa: BLE001 — the next start tries again
        log.exception("Friend Houses: writing the restore marker failed")


def _write_friend_list(peers: list[_Peer]) -> Path:
    """The listener's friend list. Friends are written over houses this
    one only asked, so an asked house can never take a friend's entry
    (audit R6 FH-11). Called only by :func:`write_listener_files`."""
    folder = _listener_folder()
    folder.mkdir(parents=True, exist_ok=True)
    tc._chmod(folder, 0o700)
    doc: dict[str, dict] = {}
    for p in sorted(peers, key=lambda p: not p.pending):  # pending first, friends last
        doc[p.session_key.hex()] = {"house": p.house_key.hex(), "pending": p.pending,
                                    "apps": [] if p.pending else sorted(set(p.apps))}
    tc._write_private(folder / fl.FRIENDS_FILE, json.dumps(doc))
    return folder


async def write_listener_files(*, onion: Any = _KEEP) -> None:
    """The ONLY writer of the listener's files (M2): the friend list with
    each friend's ``apps``, the houses this one asked (they may only deliver
    an accept), the open friend files, and, when ``onion`` is given, this
    house's hello. It takes its own lock and reads the database inside it
    every time, so a reconcile in flight can't overwrite a newer state
    with the friend list it read minutes earlier. Each friend's ``apps``
    is part of what the listener compares on every frame, so a change
    closes that friend's inbound connection."""
    from domovoi.db.session import session_scope
    from domovoi.friend_houses import plugin_mail

    async with _FILES_LOCK:
        peers, invites = await _inputs()
        async with session_scope() as s:
            apps = await plugin_mail.listener_apps(s)
        peers = [_Peer(p.id, p.reach_public, p.reach_private, p.onion_key, p.session_key, p.house_key,
                       p.pending, tuple(apps.get(p.id, ())) if not p.pending else ()) for p in peers]
        folder = _write_friend_list(peers)
        _write_invites(invites)
        await _write_phones(folder)
        if onion is _KEEP:
            return
        path = folder / fl.HELLO_FILE
        if onion is None:
            path.unlink(missing_ok=True)
            _STATE["hello"] = None
            return
        keys = fid.load()
        if keys is None:
            return
        hello = fs.Hello(keys.address_statement(onion, int(time.time())), await _current_epoch_locked())
        _STATE["hello"] = hello
        tc._write_private(path, hello.to_bytes())


async def _write_phones(folder: Path) -> None:
    """The listener's ``phones.json``: whether away chat is on and each
    current phone's Noise key (hex) with its device id. Called only by
    :func:`write_listener_files`, under its lock, read fresh each time. A
    database that can't be read writes nobody, never an older list."""
    from domovoi import egress

    doc: dict[str, Any] = {"on": False, "phones": {}}
    if sys.platform != "win32" and egress.policy() != "never":
        try:
            on, phones = await _away_inputs()
        except Exception as e:  # noqa: BLE001 — nobody gets in
            log.warning("away chat: the phone list couldn't be read (%s); refusing every phone",
                        type(e).__name__)
            on, phones = False, []
        doc = {"on": on, "phones": {p.noise_pub.hex(): p.device_id for p in phones}}
    from domovoi.away import listener as al

    tc._write_private(folder / al.PHONES_FILE, json.dumps(doc))


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
# in hex, or an away chat request with its picture (1 MiB, base64), plus
# room. asyncio's default (64 KiB) let one large message end every event
# after it (audit R6 FH-2).
EVENT_LINE_MAX = 2 * 1024 * 1024

async def _ensure_listener(folder: tc.TorFolder, mode: tuple[bool, bool] = (True, False)) -> None:
    """The listener running, serving ``mode``: (Friend Houses' sockets,
    away chat's socket). A listener started for the other mode is
    restarted with the new one."""
    proc = _LISTENER["proc"]
    running = proc is not None and proc.returncode is None
    if running and _LISTENER.get("mode") == mode:
        return
    if running or _LISTENER.get("task") is not None:
        await _stop_listener()
    _LISTENER["mode"] = mode
    _LISTENER["stopping"] = False
    _LISTENER["task"] = asyncio.get_running_loop().create_task(
        _run_listener(folder), name="friend-houses-listener"
    )


# What the docker CLI that runs the listener may see of core's environment:
# enough to find Docker, nothing else (no database address, no tokens).
# The listener itself gets none of it: ``docker run`` passes no variables.
_DOCKER_ENV = ("PATH", "LANG", "LC_ALL", "TZ", "HOME", "XDG_RUNTIME_DIR",
               "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "SYSTEMROOT")


def _listener_env() -> dict[str, str]:
    """The environment of the ``docker run`` that starts the listener's
    container (audit R6 FH-12)."""
    import os

    env = {k: os.environ[k] for k in _DOCKER_ENV if k in os.environ}
    env.setdefault("LANG", "C.UTF-8")
    return env


def _listener_command(folder: tc.TorFolder) -> list[str]:
    """``docker run -i --rm ...``: the listener in its own container, no
    network, read-only (:mod:`domovoi.friend_houses.listener_runtime`)."""
    import os

    from domovoi.friend_houses import listener_runtime as lrt

    from domovoi.away import keys as away_keys

    uid, gid = (os.getuid(), os.getgid()) if hasattr(os, "getuid") else (10001, 10001)
    fh, away = _LISTENER.get("mode", (True, False))
    return ["docker", *lrt.run_args(image_id=_LISTENER["image"], keys=fid.keys_path() if fh else None,
                                    files=_listener_folder(), sockets=folder.listen,
                                    uid=uid, gid=gid,
                                    away_key=away_keys.noise_path() if away else None)]


LISTENER_PROBLEM = ("Friends can't reach this house right now: its Friend Houses listener "
                    "couldn't be set up. Domovoi keeps trying; this often clears once the "
                    "internet is back.")


async def _prepare_listener() -> None:
    """Build the listener's image when needed and clear out any container
    left behind. Raises DockerFailed or OSError."""
    from domovoi.friend_houses import listener_runtime as lrt

    _LISTENER["image"] = await lrt.prepare()


async def _run_listener(folder: tc.TorFolder) -> None:
    from domovoi.friend_houses.tor_runtime import DockerFailed

    backoff = 1.0
    while not _LISTENER["stopping"]:
        try:
            await _prepare_listener()
        except (DockerFailed, OSError) as e:
            log.warning("Friend Houses: the listener's container couldn't be set up: %s", e)
            _LISTENER["problem"] = LISTENER_PROBLEM
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300.0)
            continue
        _LISTENER["problem"] = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *_listener_command(folder),
                env=_listener_env(),
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
        _stop_away_relays()  # its connections are gone with it
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
            if not isinstance(event, dict):
                continue
            try:
                handle_event(event)
            except Exception:
                log.exception("Friend Houses: an event from the listener failed")
    except Exception as e:  # noqa: BLE001 — L15: a RecursionError too; never a reader-less listener
        log.warning("Friend Houses: the listener's events stopped (%s); restarting it", type(e).__name__)
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
    if kind == "plugin_mail":
        # Its own lane, never the message path, never the event pool (D7,
        # D12): plugin mail never reaches people's messages.
        from domovoi.friend_houses import plugin_mail

        plugin_mail.on_event(event)
        return
    if kind == "caps":
        _on_caps(event)
        return
    if kind == "connected":
        try:
            house_key, epoch = bytes.fromhex(str(event.get("house", ""))), \
                bytes.fromhex(str(event.get("epoch", "")))
        except ValueError:
            house_key, epoch = b"", b""
        if len(house_key) == 32 and len(epoch) == fid.EPOCH_BYTES:
            from domovoi.friend_houses import plugin_wire

            plugin_wire.KNOWN.saw_epoch(house_key, epoch)  # a restore forgets its apps
            from domovoi.friend_houses import plugin_mail

            plugin_mail.on_connected(house_key, epoch)  # the friend's epoch, persisted (L3)
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
    elif kind in ("request", "accepted", "invite_closed"):
        from domovoi.friend_houses import friending

        _event(friending.on_event(event), "friend-houses-event")
    elif kind == "message":
        _event(_store_and_answer(event), "friend-houses-message")
    elif kind in ("away", "away_closed"):
        # Away chat (1.5.0): a phone's request, or its connection ended.
        # Its own lane and its own bounds (domovoi/away/bridge.py), never
        # the friends' event pool.
        from domovoi.away import bridge

        bridge.on_event(event)
        return
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
    if msg_id:
        await _answer_listener(f"{'stored' if stored else 'refused'} {msg_id}")


#: How long one answer to the listener may take to write (review D4).
PIPE_WRITE_SEC = 5.0


async def send_listener_line(line: str) -> bool:
    """One longer line to the listener's stdin (away chat's answers: an
    ASCII JSON line, ``domovoi.away.bridge``), with the same deadline as
    :func:`_answer_listener`. False when there is no listener to tell."""
    proc = _LISTENER["proc"]
    if proc is None or proc.stdin is None or proc.returncode is not None:
        return False
    try:
        proc.stdin.write((line + chr(10)).encode("ascii"))
        await asyncio.wait_for(proc.stdin.drain(), PIPE_WRITE_SEC)
    except (ConnectionError, RuntimeError, asyncio.TimeoutError):
        return False
    return True


async def _answer_listener(line: str) -> None:
    """One ASCII line to the listener's stdin, with a deadline: a listener
    that stops reading can't hold a core task for ever."""
    proc = _LISTENER["proc"]
    if proc is None or proc.stdin is None or proc.returncode is not None:
        return
    try:
        proc.stdin.write((line[:80] + chr(10)).encode("ascii"))
        await asyncio.wait_for(proc.stdin.drain(), PIPE_WRITE_SEC)
    except (ConnectionError, RuntimeError, asyncio.TimeoutError):
        pass


def _on_caps(event: dict) -> None:
    """A friend's CAPS (the listener parsed it; checked again here)."""
    from domovoi.friend_houses import envelope as fe
    from domovoi.friend_houses import plugin_wire

    try:
        house = bytes.fromhex(str(event.get("house", "")))
        epoch = bytes.fromhex(str(event.get("epoch", "")))
        proto = int(str(event.get("proto", "")))
        raw = str(event.get("apps", ""))
        slugs = tuple(raw.split(",")) if raw else ()
    except ValueError:
        return
    if (len(house) != 32 or len(epoch) != fid.EPOCH_BYTES or not fs.PROTO <= proto <= 0xFFFF
            or len(slugs) > plugin_wire.MAX_SLUGS or not all(fe.valid_slug(s) for s in slugs)
            or list(slugs) != sorted(set(slugs))):
        log.warning("Friend Houses: a CAPS event failed its checks and was dropped")
        return
    plugin_wire.KNOWN.caps(house, plugin_wire.Caps(proto, slugs), epoch)
    from domovoi.friend_houses import plugin_mail

    plugin_mail.on_caps_event(house, proto, slugs, epoch)  # persisted: peer_proto, peer_caps (L8)


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


# The longest one stage of the retry loop may run. Each stage bounds its
# own steps too; this keeps any one of them from holding up the others.
STAGE_DEADLINE_SEC = 600.0


async def _after_reconcile(fn) -> None:
    async with _LOCK:  # wait for a reconcile in flight: the onion must exist
        pass
    try:
        await asyncio.wait_for(fn(), STAGE_DEADLINE_SEC)
    except asyncio.TimeoutError:
        log.warning("Friend Houses: %s took too long; next round",
                    getattr(fn, "__name__", "a retry stage"))
    except Exception:  # noqa: BLE001 — retried on the next pass
        log.exception("Friend Houses: delivering pending requests failed")


async def _after_reconcile_then(fn) -> None:
    await asyncio.sleep(5)  # let the listener settle first
    await _after_reconcile(fn)


async def _retry_loop() -> None:
    from domovoi.friend_houses import friending, messages, presence
    from domovoi.handlers import friend_houses as voice

    # The friend names the voice patterns know ("ask the Smiths …"), read
    # once at start; a turn reads them again when they're a minute old.
    await _after_reconcile(voice.refresh_names)
    while True:
        await asyncio.sleep(RETRY_EVERY_SEC)
        await _after_reconcile(friending.deliver_pending)
        await _after_reconcile(messages.flush_all)
        # Presence: ping friends not heard from in 10 minutes, and tell any
        # still waiting for this house's new address (after a block).
        await _after_reconcile(presence.check_all)
        await _after_reconcile(friending.announce_held)


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
    # The plugin mail senders stop too, and each closes its own held
    # connection (row 8 of the slice-4 re-check: nothing else would).
    from domovoi.friend_houses import plugin_sender

    plugin_sender.stop_all()


def _stop_away_relays() -> None:
    """Away chat's relays end with the listener process they answer."""
    from domovoi.away import bridge

    bridge.stop_all()


async def _stop_listener() -> None:
    _LISTENER["stopping"] = True
    _stop_away_relays()
    proc = _LISTENER["proc"]
    if proc is not None and proc.returncode is None:
        proc.terminate()  # the docker CLI passes it on to the container
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except asyncio.TimeoutError:
            proc.kill()
            await _remove_listener_container()
    task = _LISTENER["task"]
    if task is not None:
        try:
            await asyncio.wait_for(task, 15)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
        except Exception:  # its error must never stop "off" from working (R6 FH-2)
            log.exception("Friend Houses: the listener task had failed")
    _LISTENER.update(proc=None, task=None, problem=None)


async def _remove_listener_container() -> None:
    """Best effort: a docker CLI killed hard can leave its container
    running (the next start removes it too)."""
    from domovoi.friend_houses import listener_runtime as lrt

    try:
        await lrt.remove()
    except Exception as e:  # noqa: BLE001 — "off" must keep working
        log.warning("Friend Houses: the listener's container wasn't removed: %s", e)


async def shutdown() -> None:
    """Core teardown: stop the listener. The Tor container keeps running so
    a core restart doesn't take the house offline; boot reconciles it."""
    _stop_retries()
    _stop_away_watch()
    from domovoi.away import bridge

    bridge.stop_all()
    from domovoi.friend_houses import plugin_mail

    try:
        await plugin_mail.shutdown()  # clean: in-flight rows go back uncharged (H1)
    except Exception:  # noqa: BLE001 — the listener still stops
        log.exception("Friend Houses: stopping plugin mail failed")
    await _stop_listener()
