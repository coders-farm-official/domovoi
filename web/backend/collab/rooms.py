"""Live co-editing rooms: one per document someone has open.

A room holds the document's shared state in memory (a pycrdt ``Doc``),
relays edits and presence between the sockets in it, and keeps Postgres up
to date. It exists while someone is connected (or while a restore runs) and
is dropped when the last one leaves, after a final flush.

The jobs a room does on its one-second tick:

* **persist**: edits received since the last tick are merged into ONE row
  of ``collab_updates``; every ``COMPACT_ROWS`` rows (or ``COMPACT_BYTES``)
  the whole state replaces ``collab_docs.state`` and the rows go;
* **ask for the clean file**: when the state has edits the file does not
  (``rev > saved_rev``) and nobody has typed for ``SAVE_IDLE_SEC``, the room
  sends a ``save`` control message to ONE writable client, the one that
  edited last (see "the save path" in ``web/backend/collab/__init__.py``);
* **version history**: a snapshot every ``SNAPSHOT_EVERY_SEC`` while there
  are new edits, and one when the last editor leaves;
* **outside changes**: every ``FILE_CHECK_SEC`` the file's size and mtime
  are compared with the ones the state was saved or built from; when they
  differ and its SHA-256 does too, the file was changed outside the editor
  (an upload over it, another app, a save from an editor that is not
  co-editing) and the room is rebuilt from the file (``rebuild``).

Everything here runs on the event loop: pycrdt documents are not shared
across threads, and every operation on one is quick.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from pycrdt import (
    Doc,
    create_sync_message,
    create_update_message,
    handle_sync_message,
    merge_updates,
)

from web.backend.collab import content, store
from web.backend.collab.protocol import (
    EMPTY_UPDATE,
    MSG_AUTH,
    MSG_AWARENESS,
    MSG_CONTROL,
    MSG_QUERY_AWARENESS,
    MSG_SYNC,
    SYNC_STEP1,
    SYNC_STEP2,
    SYNC_UPDATE,
    AwarenessEntry,
    ProtocolError,
    awareness_message,
    control_message,
    encode_awareness_update,
    parse_awareness_update,
    parse_control,
    read_var_bytes,
    read_var_uint,
)

log = logging.getLogger(__name__)

# ─── Limits and timings ──────────────────────────────────────────────────
# One message: a pasted photo arrives as one update (the editor scales
# photos to about 2000 px first), and an offline phone's first sync carries
# everything it typed. uvicorn refuses frames over 16 MiB before we see them.
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
# The whole shared state of one document (the file limit the save routes
# take is 64 MiB of request body).
MAX_DOC_BYTES = 48 * 1024 * 1024
MAX_PEERS_PER_DOC = 16
MAX_ROOMS = 64
MAX_SOCKETS_PER_SOURCE = 24
# Messages: a steady 60 a second with bursts to 300 (typing fast is ~15).
RATE_PER_SEC = 60.0
RATE_BURST = 300.0
# Bytes: 24 MiB in any 10 seconds.
BYTES_WINDOW_SEC = 10.0
BYTES_PER_WINDOW = 24 * 1024 * 1024
MAX_AWARENESS_STATE = 8 * 1024
MAX_CLIENT_IDS_PER_PEER = 4
SEND_QUEUE_MAX = 512

TICK_SEC = 1.0
SAVE_IDLE_SEC = 2.5
SAVE_TIMEOUT_SEC = 20.0
SNAPSHOT_EVERY_SEC = 300.0
FILE_CHECK_SEC = 30.0
COMPACT_ROWS = 200
COMPACT_BYTES = 2 * 1024 * 1024

# Close codes. y-websocket stops reconnecting on 4400-4499 and retries on
# everything else, so a refusal that reconnecting cannot fix is in 44xx.
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_NOT_FOUND = 4404
CLOSE_RESET = 4409
CLOSE_TOO_LARGE = 4413
CLOSE_UNSUPPORTED = 4415
CLOSE_BAD_MESSAGE = 4400
CLOSE_THROTTLED = 4429
CLOSE_RATE = 4508          # too fast: transient, the client retries
CLOSE_BUSY = 4503          # too many people / documents open: transient
CLOSE_SHUTDOWN = 1012      # the server is restarting


def file_fingerprint(path: Path) -> tuple[str, int, int]:
    """``(sha256, size, mtime_ns)`` of the file now."""
    st = path.stat()
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest(), st.st_size, st.st_mtime_ns


class TokenBucket:
    def __init__(self, rate: float, burst: float) -> None:
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.at = time.monotonic()

    def take(self, n: float = 1.0) -> bool:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.at) * self.rate)
        self.at = now
        if self.tokens < n:
            return False
        self.tokens -= n
        return True


@dataclass(eq=False)
class Peer:
    """One socket in a room."""

    send_bytes: Callable[[bytes], Awaitable[None]]
    close: Callable[[int, str], Awaitable[None]]
    name: str
    color: str
    readonly: bool
    source: str
    epoch: int                       # the epoch the client says its local copy is from
    archive: bool = False            # stale epoch: collect its offline copy, then reset
    session: str = ""
    client_ids: set[int] = field(default_factory=set)
    last_edit: float = 0.0
    can_save: bool = False
    closed: bool = False
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=SEND_QUEUE_MAX))
    bucket: TokenBucket = field(default_factory=lambda: TokenBucket(RATE_PER_SEC, RATE_BURST))
    window_at: float = field(default_factory=time.monotonic)
    window_bytes: int = 0
    writer: Optional[asyncio.Task] = None

    def send(self, data: bytes) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull:
            # A client that cannot keep up is not waited for: everyone else
            # in the room would wait with it. It reconnects and resyncs.
            self.kill(CLOSE_RATE, "too far behind")

    def kill(self, code: int, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        # Drop what was queued, then close: the close must go out even when
        # the queue was full.
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover
                break
        self.queue.put_nowait(("close", code, reason))

    def kill_after(self, code: int, reason: str) -> None:
        """Close once what is already queued has gone out (a reset notice
        must arrive before the close does)."""
        if self.closed:
            return
        self.closed = True
        try:
            self.queue.put_nowait(("close", code, reason))
        except asyncio.QueueFull:
            self.closed = False
            self.kill(code, reason)

    async def run_writer(self) -> None:
        try:
            while True:
                item = await self.queue.get()
                if isinstance(item, tuple):
                    if item[0] == "stop":
                        return
                    _, code, reason = item
                    try:
                        await self.close(code, reason)
                    except Exception:  # noqa: BLE001 — already gone
                        pass
                    return
                await self.send_bytes(item)
        except Exception:  # noqa: BLE001 — the socket went away mid-send
            self.closed = True

    def stop(self) -> None:
        """The socket is gone: end the writer without sending anything."""
        self.closed = True
        try:
            self.queue.put_nowait(("stop",))
        except asyncio.QueueFull:
            if self.writer is not None:
                self.writer.cancel()

    def within_rate(self, nbytes: int) -> bool:
        if not self.bucket.take():
            return False
        now = time.monotonic()
        if now - self.window_at > BYTES_WINDOW_SEC:
            self.window_at = now
            self.window_bytes = 0
        self.window_bytes += nbytes
        return self.window_bytes <= BYTES_PER_WINDOW


class Room:
    def __init__(self, row: store.DocRow, target: Path) -> None:
        self.doc_id = row.doc_id
        self.rel_path = row.rel_path
        self.target = target
        self.epoch = row.epoch
        self.ydoc = Doc()
        self.peers: list[Peer] = []
        self.holds = 0
        self.awareness: dict[int, AwarenessEntry] = {}
        self.awareness_clock: dict[int, int] = {}
        self.pending: list[bytes] = []
        self.pending_bytes = 0
        self.rows_since_compact = 0
        self.bytes_since_compact = 0
        self.rev = row.rev
        self.saved_rev = row.saved_rev
        self.seeded = row.seeded
        self.seed_peer: Optional[Peer] = None
        self.seed_fp: Optional[tuple[str, int, int]] = None
        self.file_fp: Optional[tuple[str, int, int]] = (
            (row.file_sha256, row.file_size or 0, row.file_mtime_ns or 0)
            if row.file_sha256 else None
        )
        self.editors_since_snapshot: set[str] = set()
        self.changed_since_snapshot = False
        self.last_snapshot_at = time.monotonic()
        self.last_change_at = 0.0
        self.save_peer: Optional[Peer] = None
        self.save_asked_at = 0.0
        self.save_asked_rev = -1
        self.next_file_check = time.monotonic() + FILE_CHECK_SEC
        self.approx_size = 0
        self.lock = asyncio.Lock()
        self.closed = False
        self.rebuild_wanted = False

    # ── loading ──
    def load(self, state: Optional[bytes], updates: list[bytes]) -> None:
        if state:
            self.ydoc.apply_update(state)
        for u in updates:
            self.ydoc.apply_update(u)
        self.approx_size = len(self.ydoc.get_update())
        self.rows_since_compact = len(updates)

    @property
    def writable_peers(self) -> list[Peer]:
        return [p for p in self.peers if not p.readonly and not p.archive and not p.closed]

    def broadcast(self, data: bytes, *, skip: Optional[Peer] = None) -> None:
        for p in self.peers:
            if p is not skip and not p.archive:
                p.send(data)

    def control_all(self, payload: dict[str, Any]) -> None:
        self.broadcast(control_message(payload))

    # ── joining and leaving ──
    def hello(self, peer: Peer) -> dict[str, Any]:
        return {
            "t": "hello",
            "session": peer.session,
            "epoch": self.epoch,
            "readonly": peer.readonly,
            "ready": self.seeded,
            "seed": peer is self.seed_peer,
            "user": {"name": peer.name, "color": peer.color},
        }

    def join(self, peer: Peer) -> None:
        self.peers.append(peer)
        if peer.archive:
            peer.send(control_message({"t": "hello", "session": peer.session,
                                       "epoch": self.epoch, "stale": True,
                                       "readonly": True, "ready": False, "seed": False,
                                       "user": {"name": peer.name, "color": peer.color}}))
            # An empty state vector: the client answers with EVERYTHING it
            # holds, which is what its offline copy is made of.
            peer.send(create_sync_message(Doc()))
            return
        if not self.seeded and self.seed_peer is None and not peer.readonly:
            self.grant_seed(peer, announce=False)
        peer.send(control_message(self.hello(peer)))
        peer.send(create_sync_message(self.ydoc))
        if self.awareness:
            peer.send(awareness_message(encode_awareness_update(list(self.awareness.values()))))

    def grant_seed(self, peer: Peer, *, announce: bool) -> None:
        self.seed_peer = peer
        try:
            self.seed_fp = file_fingerprint(self.target)
        except OSError:
            self.seed_fp = None
        if announce:
            peer.send(control_message({"t": "seed"}))

    def leave(self, peer: Peer) -> None:
        if peer in self.peers:
            self.peers.remove(peer)
        if not peer.closed:
            peer.stop()
        peer.closed = True
        gone = [self.awareness.pop(cid) for cid in list(peer.client_ids) if cid in self.awareness]
        if gone:
            for e in gone:
                e.clock += 1
                e.state = "null"
                self.awareness_clock[e.client_id] = e.clock
            self.broadcast(awareness_message(encode_awareness_update(gone)))
        if self.seed_peer is peer:
            self.seed_peer = None
            if not self.seeded:
                nxt = next(iter(self.writable_peers), None)
                if nxt is not None:
                    self.grant_seed(nxt, announce=True)
        if self.save_peer is peer:
            self.save_peer = None
            self.save_asked_rev = -1

    # ── messages ──
    async def handle(self, peer: Peer, data: bytes) -> None:
        if peer.closed:
            return
        if len(data) > MAX_MESSAGE_BYTES:
            peer.kill(CLOSE_TOO_LARGE, "message too large")
            return
        if not peer.within_rate(len(data)):
            peer.kill(CLOSE_RATE, "too many changes too fast")
            return
        try:
            kind, pos = read_var_uint(data, 0)
            if kind == MSG_SYNC:
                await self._sync(peer, data, pos)
            elif kind == MSG_AWARENESS:
                self._awareness(peer, data, pos)
            elif kind == MSG_QUERY_AWARENESS:
                if self.awareness:
                    peer.send(awareness_message(
                        encode_awareness_update(list(self.awareness.values()))))
            elif kind == MSG_CONTROL:
                await self._control(peer, parse_control(data, pos))
            elif kind == MSG_AUTH:
                pass
            else:
                raise ProtocolError(f"unknown message type {kind}")
        except ProtocolError as e:
            log.info("collab %s: bad message from %s: %s", self.doc_id, peer.source, e)
            peer.kill(CLOSE_BAD_MESSAGE, "not a co-editing message")
        except Exception as e:  # noqa: BLE001 — pycrdt refusing an update
            log.info("collab %s: refused message from %s: %s", self.doc_id, peer.source, e)
            peer.kill(CLOSE_BAD_MESSAGE, "that change could not be applied")

    async def _sync(self, peer: Peer, data: bytes, pos: int) -> None:
        step, pos2 = read_var_uint(data, pos)
        if peer.archive:
            if step in (SYNC_STEP2, SYNC_UPDATE):
                payload, _ = read_var_bytes(data, pos2)
                await self.archive_offline_copy(peer, payload)
            return
        if step == SYNC_STEP1:
            read_var_bytes(data, pos2)  # validate before pycrdt asserts on it
            reply = handle_sync_message(data[pos:], self.ydoc)
            if reply is not None:
                peer.send(reply)
            return
        if step not in (SYNC_STEP2, SYNC_UPDATE):
            raise ProtocolError("unknown sync step")
        update, _ = read_var_bytes(data, pos2)
        if update == EMPTY_UPDATE:
            return
        if peer.readonly:
            # A view-only session's edits go nowhere. The editor is read-only
            # on that side too, so this is a stale tab or a client lying.
            return
        if self.approx_size + len(update) > MAX_DOC_BYTES:
            peer.send(control_message({"t": "limit", "reason": "too_large"}))
            peer.kill_after(CLOSE_TOO_LARGE, "document too large")
            return
        self.apply_and_relay(update, by=peer)

    def apply_and_relay(self, update: bytes, *, by: Optional[Peer], name: str | None = None) -> None:
        self.ydoc.apply_update(update)
        self._record(update, by=by, name=name)

    def _record(self, update: bytes, *, by: Optional[Peer], name: str | None = None) -> None:
        """An update already in ``self.ydoc``: relay it, queue it for
        Postgres, count it."""
        self.approx_size += len(update)
        self.broadcast(create_update_message(update), skip=by)
        self.pending.append(update)
        self.pending_bytes += len(update)
        self.rev += 1
        now = time.monotonic()
        self.last_change_at = now
        if by is not None:
            by.last_edit = now
        # The seed is the file itself, not anybody's edit.
        if by is not None and by is self.seed_peer and not self.seeded:
            return
        self.changed_since_snapshot = True
        who = name or (by.name if by is not None else None)
        if who:
            self.editors_since_snapshot.add(who)

    def _awareness(self, peer: Peer, data: bytes, pos: int) -> None:
        """Presence, with y-protocols' own rules: an entry counts only when
        its clock is newer than the last one seen for that client id. The
        stock provider re-sends whatever it hears (other people's presence
        included), so a stale copy of someone else's entry is normal and
        is dropped quietly; a client id belongs to the socket that first
        announced it, and nobody else may change it."""
        update, _ = read_var_bytes(data, pos)
        accepted: list[AwarenessEntry] = []
        for e in parse_awareness_update(update):
            if len(e.state) > MAX_AWARENESS_STATE:
                raise ProtocolError("presence too large")
            owner = next((p for p in self.peers if e.client_id in p.client_ids), None)
            if owner is not None and owner is not peer:
                continue
            last = self.awareness_clock.get(e.client_id)
            fresh = last is None or e.clock > last or (
                e.clock == last and e.state == "null" and e.client_id in self.awareness)
            if not fresh:
                continue
            if owner is None:
                if e.state == "null":
                    continue
                if len(peer.client_ids) >= MAX_CLIENT_IDS_PER_PEER:
                    raise ProtocolError("too many client ids")
                peer.client_ids.add(e.client_id)
            self.awareness_clock[e.client_id] = e.clock
            if e.state == "null":
                self.awareness.pop(e.client_id, None)
            else:
                self.awareness[e.client_id] = e
            accepted.append(e)
        if accepted:
            # To everyone, the sender too: the stock client reconnects when
            # it has heard nothing for 30 s, and its own presence renewing
            # every 15 s is what it hears when it is alone in a document.
            self.broadcast(awareness_message(encode_awareness_update(accepted)))

    async def _control(self, peer: Peer, msg: dict[str, Any]) -> None:
        t = msg["t"]
        if t == "seeded":
            if peer is self.seed_peer and not self.seeded:
                await self.finish_seed()
        elif t == "seed-failed":
            # The picked client could not load the file: someone else tries.
            if peer is self.seed_peer and not self.seeded:
                self.seed_peer = None
                nxt = next((p for p in self.writable_peers if p is not peer), None)
                if nxt is not None:
                    self.grant_seed(nxt, announce=True)
        elif t == "saver":
            peer.can_save = bool(msg.get("on"))
        elif t == "save-now":
            # The client is leaving (closing the document, the tab going
            # away): if the file is behind, it saves now rather than after
            # the idle wait, since it will not be here for that.
            if (not peer.readonly and peer.can_save and self.seeded
                    and self.rev > self.saved_rev):
                self.save_peer = peer
                self.save_asked_at = time.monotonic()
                self.save_asked_rev = self.rev
                peer.send(control_message({"t": "save", "rev": self.rev, "epoch": self.epoch,
                                           "doc": self.doc_id}))
            else:
                peer.send(control_message({"t": "saved", "rev": self.saved_rev,
                                           "behind": self.rev > self.saved_rev,
                                           "reply": "save-now"}))
        elif t == "save-failed":
            if peer is self.save_peer:
                self.save_peer = None
                self.save_asked_rev = -1
                peer.can_save = False

    async def finish_seed(self) -> None:
        self.seeded = True
        self.seed_peer = None
        self.saved_rev = self.rev
        self.file_fp = self.seed_fp
        await self.flush()
        await store.mark_seeded(self.doc_id, self.epoch, self.seed_fp, self.rev)
        state = self.ydoc.get_update()
        await store.add_snapshot(self.doc_id, self.epoch, "from_file", state, [])
        self.changed_since_snapshot = False
        self.editors_since_snapshot.clear()
        self.last_snapshot_at = time.monotonic()
        self.control_all({"t": "ready"})

    async def archive_offline_copy(self, peer: Peer, payload: bytes) -> None:
        """A client came back with a copy from before the file was changed
        outside the editor. Its edits cannot be merged into the rebuilt
        document (they would be applied on top of the file's new text and
        duplicate it), so they are kept as a version of their own, on top of
        the last version of their epoch, and the client starts over."""
        kept = False
        if peer.readonly:
            # Only someone who may edit can add a version; a view-only
            # session with a stale copy just starts over.
            peer.send(control_message({"t": "reset", "epoch": self.epoch,
                                       "reason": "changed_outside", "kept": False}))
            peer.kill_after(CLOSE_RESET, "the document was changed outside the editor")
            return
        try:
            base_snap = await store.latest_snapshot_of_epoch(self.doc_id, peer.epoch)
            base = content.doc_from_update(base_snap.state if base_snap else None)
            before = base.get_update()
            base.apply_update(payload)
            after = base.get_update()
            if after != before and not content.is_empty(base):
                await store.add_snapshot(self.doc_id, peer.epoch, "offline_copy", after,
                                         [peer.name])
                kept = True
        except Exception as e:  # noqa: BLE001
            log.warning("collab %s: could not keep an offline copy: %s", self.doc_id, e)
        peer.send(control_message({"t": "reset", "epoch": self.epoch,
                                   "reason": "changed_outside", "kept": kept}))
        peer.kill_after(CLOSE_RESET, "the document was changed outside the editor")

    # ── persistence ──
    async def flush(self) -> None:
        if not self.pending:
            return
        batch = self.pending
        self.pending = []
        size = self.pending_bytes
        self.pending_bytes = 0
        data = batch[0] if len(batch) == 1 else merge_updates(*batch)
        await store.append_update(self.doc_id, self.epoch, data, self.rev)
        self.rows_since_compact += 1
        self.bytes_since_compact += size
        if self.rows_since_compact >= COMPACT_ROWS or self.bytes_since_compact >= COMPACT_BYTES:
            await self.compact()

    async def compact(self) -> None:
        state = self.ydoc.get_update()
        await store.compact(self.doc_id, self.epoch, state, self.rev)
        self.approx_size = len(state)
        self.rows_since_compact = 0
        self.bytes_since_compact = 0

    async def snapshot(self, kind: str) -> Optional[int]:
        state = self.ydoc.get_update()
        if content.is_empty(self.ydoc) and kind in ("auto", "leave"):
            return None
        sid = await store.add_snapshot(self.doc_id, self.epoch, kind, state,
                                       self.editors_since_snapshot)
        self.editors_since_snapshot = set()
        self.changed_since_snapshot = False
        self.last_snapshot_at = time.monotonic()
        try:
            await store.thin_snapshots(self.doc_id)
        except Exception as e:  # noqa: BLE001 — retention is best effort
            log.warning("collab %s: thinning failed: %s", self.doc_id, e)
        return sid

    # ── the tick ──
    async def tick(self) -> None:
        async with self.lock:
            if self.closed:
                return
            await self.flush()
            now = time.monotonic()
            if (self.changed_since_snapshot and self.seeded
                    and now - self.last_snapshot_at >= SNAPSHOT_EVERY_SEC):
                await self.snapshot("auto")
            self.maybe_ask_for_save(now)
            if self.seeded and (self.rebuild_wanted or now >= self.next_file_check):
                self.next_file_check = now + FILE_CHECK_SEC
                self.rebuild_wanted = False
                if self.file_changed_outside():
                    await self.rebuild_live()

    def maybe_ask_for_save(self, now: float) -> None:
        if not self.seeded or self.rev <= self.saved_rev:
            return
        if self.last_change_at and now - self.last_change_at < SAVE_IDLE_SEC:
            return
        asked_recently = (
            self.save_peer is not None and not self.save_peer.closed
            and now - self.save_asked_at < SAVE_TIMEOUT_SEC
        )
        if asked_recently and self.save_asked_rev == self.rev:
            return
        if asked_recently and self.save_asked_rev != self.rev:
            # Asked for an older rev and still waiting: let that one finish.
            return
        candidates = [p for p in self.writable_peers if p.can_save]
        if not candidates:
            return
        candidates.sort(key=lambda p: p.last_edit, reverse=True)
        # Ask someone else after a timeout, so one stuck client cannot hold
        # the file back for everybody.
        if self.save_peer in candidates and now - self.save_asked_at >= SAVE_TIMEOUT_SEC \
                and len(candidates) > 1:
            candidates.remove(self.save_peer)
        peer = candidates[0]
        self.save_peer = peer
        self.save_asked_at = now
        self.save_asked_rev = self.rev
        peer.send(control_message({"t": "save", "rev": self.rev, "epoch": self.epoch,
                                   "doc": self.doc_id}))

    def note_saved(self, rev: int, fp: tuple[str, int, int]) -> None:
        self.saved_rev = max(self.saved_rev, rev)
        self.file_fp = fp
        if self.save_asked_rev <= rev:
            self.save_peer = None
            self.save_asked_rev = -1
        self.control_all({"t": "saved", "rev": self.saved_rev,
                          "behind": self.rev > self.saved_rev})

    def file_changed_outside(self) -> bool:
        if self.file_fp is None:
            return False
        try:
            st = self.target.stat()
        except OSError:
            return False   # gone: the socket checks refuse new joins; delete hooks forget it
        sha, size, mtime_ns = self.file_fp
        if st.st_size == size and st.st_mtime_ns == mtime_ns:
            return False
        try:
            now_fp = file_fingerprint(self.target)
        except OSError:
            return False
        if now_fp[0] == sha:
            self.file_fp = now_fp
            return False
        return True

    async def restore(self, version_state: bytes, by_name: str) -> Optional[int]:
        """Put an old version back AS A NEW CHANGE: everyone connected sees it
        arrive like any other edit, and the version just before it is kept
        ("before_restore"), so a restore is itself undoable from history.
        Returns the id of the "restored" version."""
        async with self.lock:
            if self.closed:
                raise RoomGone()
            if not self.seeded and self.seed_peer is not None:
                raise StillOpening()
            await self.flush()
            if not content.is_empty(self.ydoc):
                await store.add_snapshot(self.doc_id, self.epoch, "before_restore",
                                         self.ydoc.get_update(), self.editors_since_snapshot)
                self.editors_since_snapshot = set()
            if not self.seeded:
                # Nobody had opened it since it was (re)built from the file:
                # the restored version IS the content now, and the file is
                # behind it until an editor saves.
                try:
                    self.file_fp = file_fingerprint(self.target)
                except OSError:
                    self.file_fp = None
                await store.mark_seeded(self.doc_id, self.epoch, self.file_fp, self.rev)
                self.seeded = True
                self.saved_rev = self.rev
                self.control_all({"t": "ready"})
            sv = self.ydoc.get_state()
            content.restore_into(self.ydoc, content.doc_from_update(version_state))
            update = self.ydoc.get_update(sv)
            self._record(update, by=None, name=by_name)
            # Saving should not wait for "idle": nobody is typing a restore.
            self.last_change_at = 0.0
            await self.flush()
            sid = await self.snapshot("restored")
            self.control_all({"t": "restored", "by": by_name})
            return sid

    async def rebuild_live(self) -> None:
        """The file was changed outside the editor: keep the co-edited version
        in history, start over from the file under a new epoch, and send
        everyone connected back to open it again."""
        await self.flush()
        if not content.is_empty(self.ydoc):
            await store.add_snapshot(self.doc_id, self.epoch, "before_rebuild",
                                     self.ydoc.get_update(), self.editors_since_snapshot)
        new_epoch = await store.rebuild(self.doc_id)
        log.info("collab %s: %s changed outside the editor; rebuilt from the file (epoch %s)",
                 self.doc_id, self.rel_path, new_epoch)
        for p in list(self.peers):
            p.send(control_message({"t": "reset", "epoch": new_epoch,
                                    "reason": "changed_outside", "kept": False}))
            p.kill_after(CLOSE_RESET, "the document was changed outside the editor")
        self.closed = True
        MANAGER.drop(self)

    async def shut(self, *, reason: str = "leave") -> None:
        """Last one out (or the server stopping): flush, keep a version,
        compact, and go."""
        async with self.lock:
            if self.closed:
                return
            self.closed = True
            try:
                await self.flush()
                if self.changed_since_snapshot and self.seeded:
                    await self.snapshot("leave")
                await self.compact()
            except Exception as e:  # noqa: BLE001
                log.warning("collab %s: final flush failed: %s", self.doc_id, e)
            finally:
                MANAGER.drop(self)


class RoomManager:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}
        self._loading: dict[str, asyncio.Lock] = {}
        self._ticker: Optional[asyncio.Task] = None
        self.sockets_by_source: dict[str, int] = {}

    def by_path(self, rel_path: str) -> Optional[Room]:
        for room in self.rooms.values():
            if room.rel_path == rel_path:
                return room
        return None

    def drop(self, room: Room) -> None:
        if self.rooms.get(room.doc_id) is room:
            del self.rooms[room.doc_id]

    async def open(self, row: store.DocRow, target: Path) -> Room:
        """The live room for this document, loading it (and rebuilding it
        from the file first, when the file changed while nobody had it
        open) when there is none."""
        room = self.rooms.get(row.doc_id)
        if room is not None and not room.closed:
            return room
        lock = self._loading.setdefault(row.doc_id, asyncio.Lock())
        async with lock:
            room = self.rooms.get(row.doc_id)
            if room is not None and not room.closed:
                return room
            if len(self.rooms) >= MAX_ROOMS:
                raise RoomsFull()
            fresh = await store.get(row.doc_id) or row
            room = Room(fresh, target)
            room.load(fresh.state, await store.pending_updates(fresh.doc_id, fresh.epoch))
            if fresh.seeded and room.file_changed_outside():
                if not content.is_empty(room.ydoc):
                    await store.add_snapshot(fresh.doc_id, fresh.epoch, "before_rebuild",
                                             room.ydoc.get_update(), [])
                await store.rebuild(fresh.doc_id)
                fresh = await store.get(row.doc_id) or fresh
                room = Room(fresh, target)
            self.rooms[row.doc_id] = room
            self._ensure_ticker()
            return room

    def _ensure_ticker(self) -> None:
        if self._ticker is None or self._ticker.done():
            self._ticker = asyncio.get_running_loop().create_task(self._tick_loop())

    async def _tick_loop(self) -> None:
        while self.rooms:
            await asyncio.sleep(TICK_SEC)
            for room in list(self.rooms.values()):
                try:
                    await room.tick()
                except Exception as e:  # noqa: BLE001 — one room must not stop the rest
                    log.warning("collab %s: tick failed: %s", room.doc_id, e)
        self._ticker = None

    async def release(self, room: Room) -> None:
        if not room.peers and room.holds <= 0:
            # Shielded: the socket handler that calls this may itself be
            # cancelled as the connection goes, and the last edits must
            # still reach Postgres.
            await asyncio.shield(room.shut())

    async def shutdown(self) -> None:
        for room in list(self.rooms.values()):
            for p in list(room.peers):
                p.kill(CLOSE_SHUTDOWN, "the server is restarting")
            await room.shut(reason="shutdown")
        if self._ticker is not None:
            self._ticker.cancel()
            self._ticker = None

    async def forget(self, doc_ids: list[str]) -> None:
        for did in doc_ids:
            room = self.rooms.pop(did, None)
            if room is None:
                continue
            room.closed = True
            for p in list(room.peers):
                p.send(control_message({"t": "gone"}))
                p.kill_after(CLOSE_NOT_FOUND, "the document was deleted")


class RoomsFull(Exception):
    pass


class RoomGone(Exception):
    pass


class StillOpening(Exception):
    pass


MANAGER = RoomManager()
