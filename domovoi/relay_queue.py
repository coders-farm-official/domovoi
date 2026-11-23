"""No raw internet URL sits in a room's music queue (L20, review 2026-11-23).

A room's player (an MPD container) fetches whatever its queue holds, by
itself, out of reach of every check in the core. So an internet stream
reaches a player only as a relay URL on the core
(domovoi/player_relay.py), which makes the checked fetch. This module keeps
it that way for the queue as a whole, not only the newest play:

* **Nothing raw goes in.** :func:`refuses_queue_uri` is asked by the MPD
  protocol client before every ``add`` / ``addid`` (whatever called it), by
  the MPD client's ``play_url`` / ``prepare_url`` and by the stub, and
  ``sdk.playback.play_url`` turns a raw internet URL into a relay URL first.
  An "internet URL" is one with a scheme and a host that is not on this
  network (:func:`domovoi.egress.is_local_host`), under every internet
  answer; a library file, the FM tuner's stream on this network and a relay
  URL the core minted are not.
* **Nothing raw stays.** :func:`scan_room` looks at a room's queue at core
  start (:func:`schedule_boot_scan`, every room, after the
  ``INTERNET_ACCESS=never`` purge of domovoi/mpd_internet.py), after every
  start of a room's player (``mpd_provisioner._after_start``) and when a
  player is seen to have restarted (domovoi/player_restarts.py). An http(s)
  entry that is not a relay URL is rewritten to a fresh relay URL for that
  room when the core can tell what it was (a relay record for that address,
  else the room's play history: the radio plugin's stations as ``radio``,
  any other plugin's play as ``file``), and removed otherwise. A relay URL
  the core has no record of (it can never play) is removed; one minted for
  another room, or naming an address the player no longer reaches the core
  by, is re-minted for this room. Log lines name a hash of a URL, never the
  URL.
* **A hand-off re-mints.** A relay token is bound to one room, so a queue
  that moves to another room's player (a group's new host,
  domovoi/music_groups.py) is translated by :func:`carry_over`: each relay
  entry becomes a fresh relay URL for the receiving room from the record
  of what it stands for, and a raw internet URL is refused (left out).

How a queued relay entry stays playable past its token's life (a spent
radio connection, a file token's hours, a restart): the relay's own
records and renewal (domovoi/player_relay.py, "Queue entries").

Everything here is best effort: an unreachable room is logged and skipped,
and nothing raises into the caller.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlsplit

from domovoi import egress, player_relay

log = logging.getLogger(__name__)

BOOT_RETRY_SEC = 90.0
RETRY_INTERVAL_SEC = 3.0

#: The bundled radio plugin's now-playing source: its plays are live streams.
RADIO_SOURCE = "radio"

_PENDING: set[asyncio.Task] = set()


def _host(uri: str) -> str | None:
    try:
        return urlsplit(uri.strip()).hostname
    except ValueError:
        return None


def is_internet_uri(uri: object) -> bool:
    """A queue entry the player would fetch from the internet itself: a URL
    whose host is not on this network and that is not a relay URL this
    core minted. Library files (relative paths) never match."""
    if not isinstance(uri, str) or "://" not in uri:
        return False
    if player_relay.is_relay_url(uri):
        return False
    host = _host(uri)
    if not host:
        return False
    return not egress.is_local_host(host)


def refuses_queue_uri(uri: object) -> bool:
    """True (and logged, with a hash of the URL) when ``uri`` must not be
    queued in any room's player: the MPD protocol client's backstop."""
    if not is_internet_uri(uri):
        return False
    log.warning("music queue: refused an internet address that isn't relayed (%s)",
                player_relay.url_hash(str(uri)))
    return True


def _dead_relay(uri: str) -> bool:
    """A relay URL whose token this core neither holds nor has a record of."""
    return player_relay.token_of(uri) is not None and not player_relay.is_relay_url(uri)


def _route_prefix(route: Any) -> str:
    return route.url(player_relay._PREFIX)


async def _history_kind(room_id: str, url: str) -> str | None:
    """What the room's play history says ``url`` was: ``radio`` for the
    radio plugin's station, ``file`` for any other plugin's play; None when
    it was never played there (or the history can't be read)."""
    try:
        from sqlalchemy import text

        from domovoi.db.session import session_scope

        async with session_scope() as s:
            row = (await s.execute(text(
                "SELECT source FROM media_plays WHERE room_id = :r AND stream_url = :u "
                "ORDER BY started_at DESC, id DESC LIMIT 1"
            ), {"r": room_id, "u": url})).first()
    except Exception as e:  # noqa: BLE001 - unknown is "remove"
        log.debug("music queue: room=%s play history unavailable: %s", room_id, e)
        return None
    if row is None:
        return None
    return "radio" if str(row[0]) == RADIO_SOURCE else "file"


async def _kind_of(room_id: str, url: str) -> str | None:
    for rec in reversed(list(player_relay._RECORDS.values())):
        if rec.url == url and rec.room_id == room_id:
            return rec.kind
    return await _history_kind(room_id, url)


async def _mint(room_id: str, url: str, kind: str, route: Any) -> str | None:
    from domovoi import net_safety

    try:
        await net_safety.avet_outbound_url(url, require_resolution=False)
        return player_relay.mint(room_id, url, route, kind=kind)
    except Exception as e:  # noqa: BLE001 - not relayable: the entry goes
        log.info("music queue: room=%s couldn't relay %s (%s)",
                 room_id, player_relay.url_hash(url), type(e).__name__)
        return None


def _one(value: Any) -> str | None:
    """A tag as one string (MPD repeats a key as a list)."""
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value) if value else None


async def _replace(client: Any, entry: dict[str, Any], new_uri: str,
                   current_id: int | None, state: str) -> bool:
    """Put ``new_uri`` right after ``entry`` (its title and artist kept),
    move the player onto it when ``entry`` was the current song, then
    delete ``entry``. False when the player refused the new entry."""
    pos = int(entry.get("pos") or 0)
    added = await client.queue_add_files([new_uri], position=pos + 1)
    new = added[0] if added else None
    if not new or new.get("id") is None:
        return False
    new_id = int(new["id"])
    tag = getattr(client, "queue_tag", None)
    if tag is not None:
        try:
            await tag(new_id, title=_one(entry.get("title")), artist=_one(entry.get("artist")))
        except Exception as e:  # noqa: BLE001 - untitled, still playable
            log.debug("music queue: tags not copied: %s", e)
    if current_id == entry.get("id") and state in ("play", "pause"):
        await client.play_id(new_id, paused=state == "pause")
    await client.queue_remove([int(entry["id"])])
    return True


async def scan_room(room_id: str, client: Any) -> dict[str, int]:
    """Rewrite or remove the entries of ``room_id``'s own queue that its
    player would fetch from the internet itself, or that can never play
    (module docstring). Returns ``{"rewritten": n, "removed": m}``. Raises
    when the player can't be reached (the caller decides on a retry)."""
    out = {"rewritten": 0, "removed": 0}
    entries = await client.queue_list()
    current_id: int | None = None
    state = "stop"
    try:
        current = await client.current_song()
        if current and current.get("id") is not None:
            current_id = int(current["id"])
        state = await client.state()
    except Exception as e:  # noqa: BLE001 - treated as stopped
        log.debug("music queue: room=%s state unknown: %s", room_id, e)
    route = None
    route_looked = False
    doomed: list[int] = []
    for entry in entries:
        uri = str(entry.get("file") or "")
        if "://" not in uri or entry.get("id") is None:
            continue
        rec = player_relay.record_for(uri)
        if rec is None and _dead_relay(uri):
            doomed.append(int(entry["id"]))
            log.info("music queue: room=%s removed a relay entry with no record (%s)",
                     room_id, player_relay.url_hash(uri))
            continue
        if rec is None and not is_internet_uri(uri):
            continue  # a library file, a stream on this network, a live relay
        if not route_looked:
            route = await player_relay._route_for(room_id)
            route_looked = True
        if rec is not None:
            if rec.room_id == room_id and (route is None or uri.startswith(_route_prefix(route))):
                continue  # this room's own, and renewable when it plays
            target, kind = rec.url, rec.kind
        else:
            target, kind = uri, await _kind_of(room_id, uri)
        new_uri = None
        if route is not None and kind is not None:
            new_uri = await _mint(room_id, target, kind, route)
        if new_uri is not None and await _replace(client, entry, new_uri, current_id, state):
            out["rewritten"] += 1
            log.info("music queue: room=%s entry %s now plays through the relay",
                     room_id, player_relay.url_hash(target))
            continue
        doomed.append(int(entry["id"]))
        log.info("music queue: room=%s removed an internet entry the core can't relay (%s)",
                 room_id, player_relay.url_hash(uri))
    if doomed:
        if current_id in doomed and state in ("play", "pause"):
            await client.stop()
        out["removed"] = len(await client.queue_remove(doomed))
    try:
        keep = {
            player_relay.digest_of(t)
            for t in (player_relay.token_of(str(e.get("file") or ""))
                      for e in await client.queue_list())
            if t is not None
        }
        player_relay.forget_records(room_id, keep=keep)
    except Exception as e:  # noqa: BLE001
        log.debug("music queue: room=%s records not pruned: %s", room_id, e)
    if out["rewritten"] or out["removed"]:
        log.info("music queue: room=%s: %d entr(ies) moved onto the relay, %d removed",
                 room_id, out["rewritten"], out["removed"])
    return out


async def check_room(room_id: str, client: Any) -> None:
    """The never-purge, then :func:`scan_room`. Raises when unreachable."""
    from domovoi import mpd_internet

    await mpd_internet.purge_room(room_id, client)
    await scan_room(room_id, client)


async def scan_all(*, retry_sec: float = 0.0) -> None:
    """:func:`check_room` for every room's own player; a room that can't be
    reached yet is asked again for ``retry_sec``. Never raises."""
    try:
        from domovoi.clients.mpd import iter_mpd_clients

        rooms = iter_mpd_clients()
    except Exception as e:  # noqa: BLE001
        log.warning("music queue: couldn't list the music rooms: %s", e)
        return
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, retry_sec)
    for room_id, client in rooms:
        while True:
            try:
                await check_room(room_id, client)
                break
            except Exception as e:  # noqa: BLE001 - a container still starting
                if loop.time() + RETRY_INTERVAL_SEC > deadline:
                    log.warning("music queue: couldn't check room=%s's queue: %s",
                                room_id, type(e).__name__)
                    break
                await asyncio.sleep(RETRY_INTERVAL_SEC)


def _keep(task: asyncio.Task) -> asyncio.Task:
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)
    return task


def schedule_boot_scan() -> asyncio.Task | None:
    """Core start: every room's queue checked in the background, retrying
    rooms whose players are still starting. Never delays the boot."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return _keep(loop.create_task(scan_all(retry_sec=BOOT_RETRY_SEC), name="music-queue-boot-scan"))


def schedule_room_scan(room_id: str, client: Any) -> asyncio.Task | None:
    """One room's queue checked in the background (its player restarted)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None

    async def _run() -> None:
        try:
            await scan_room(room_id, client)
        except Exception as e:  # noqa: BLE001
            log.warning("music queue: couldn't check room=%s's queue: %s", room_id, type(e).__name__)

    return _keep(loop.create_task(_run(), name=f"music-queue-scan-{room_id}"))


async def carry_over(old: str, new: str, uris: list[str], start_index: int) -> tuple[list[str], bool]:
    """``old``'s queue as ``new``'s player may take it: each relay entry
    re-minted for ``new`` (tokens are room-bound and are never copied), a
    raw internet URL refused, a dead relay entry left out. A left-out entry
    is ``""`` in its place (``prepare_files`` skips it). The bool is False
    when the entry at ``start_index`` was left out (its place in the song
    then means nothing)."""
    out = list(uris)
    start_ok = True
    pending: list[tuple[int, player_relay.Record]] = []
    for i, uri in enumerate(uris):
        if not uri or "://" not in uri:
            continue
        rec = player_relay.record_for(uri)
        if rec is not None:
            pending.append((i, rec))
            continue
        if _dead_relay(uri) or is_internet_uri(uri):
            out[i] = ""
            log.warning("groups: hand-off %s -> %s: refused a queue entry that isn't relayed (%s)",
                        old, new, player_relay.url_hash(uri))
            start_ok = start_ok and i != start_index
    if pending:
        route = await player_relay._route_for(new)
        # The start entry last: a mint ends the room's earlier grant, and
        # the start entry is the one fetched at once.
        for i, rec in sorted(pending, key=lambda p: p[0] == start_index):
            fresh = await _mint(new, rec.url, rec.kind, route) if route is not None else None
            out[i] = fresh or ""
            if fresh is None:
                start_ok = start_ok and i != start_index
    return out, start_ok


def reset_for_tests() -> None:
    _PENDING.clear()
