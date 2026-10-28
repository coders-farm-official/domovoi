"""The document worker: the only place pycrdt runs.

yrs (the Rust core under pycrdt) walks nested structures recursively, and
a deep enough structure overflows the native stack and kills the process
(review rounds 1 and 2, 2026-10-26; design-notes/editors-v1/
UPSTREAM-YRS-RECURSION-NOTE.md). Mirroring yrs's structure tracking in the
web process to predict that proved fragile, so the design is containment:
every co-edited document is held by its own worker subprocess, started
by the web process (web/backend/collab/workers.py), and anything that
touches pycrdt runs here: apply, state vector, diff, encode, compaction,
restore, preview. If yrs crashes, this process dies, the web process
refuses the update that did it, starts a new worker from the last state in
Postgres, and everyone else carries on.

Nesting is still bounded, so that a deep tree never sits in a document
waiting for someone to delete or print it (the operations that crash):
after every apply that brings shared types (or while yrs is holding back
structs it cannot place yet, which a later update may release), the
document is walked with an explicit stack and a work budget. Past
``MAX_TYPE_DEPTH`` the worker throws the document away and rebuilds it
from what it held before, and the update is refused. Creating a deep tree
does not crash yrs; only traversing or deleting it does, and the walk only
asks each node for its children, one level at a time.

Wire format (stdin/stdout, binary): every frame is a 4-byte big-endian
length, that many bytes of JSON header, then the byte strings the header's
``blobs`` lists (their lengths), back to back. Requests carry ``id``,
``op``, ``args``; answers carry ``id``, ``ok``, ``result`` or ``error``.
No pickle: the web process never unpickles anything a document touched.
"""

from __future__ import annotations

import json
import os
import struct
import sys
from typing import Any, Optional

MAX_TYPE_DEPTH = 64
WALK_BUDGET = 2_000_000          # nodes and text chunks visited per check


class Refused(Exception):
    """The update is refused; the document is as it was before it."""


def _read_exact(stream, n: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def read_frame(stream) -> Optional[tuple[dict, list[bytes]]]:
    head = _read_exact(stream, 4)
    if head is None:
        return None
    (hlen,) = struct.unpack(">I", head)
    raw = _read_exact(stream, hlen)
    if raw is None:
        return None
    header = json.loads(raw.decode("utf-8"))
    blobs = []
    for n in header.get("blobs", []):
        b = _read_exact(stream, int(n))
        if b is None:
            return None
        blobs.append(b)
    return header, blobs


def frame(header: dict, blobs: list[bytes] = ()) -> bytes:
    header = dict(header)
    header["blobs"] = [len(b) for b in blobs]
    raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(raw)) + raw + b"".join(blobs)


# ─── The document ─────────────────────────────────────────────────────


class Holder:
    """One document, rebuilt from ``base`` + ``accepted`` when an update has
    to be taken back."""

    def __init__(self) -> None:
        from pycrdt import Doc

        self.Doc = Doc
        self.doc = Doc()
        self.base: list[bytes] = []
        self.accepted: list[bytes] = []
        self.expected: dict[int, int] = {}   # client -> clock the updates promised
        self.pending = False

    def _state_vector(self) -> dict[int, int]:
        from web.backend.collab.protocol import parse_state_vector

        return parse_state_vector(self.doc.get_state())

    def _rebuild(self) -> None:
        doc = self.Doc()
        for blob in self.base + self.accepted:
            doc.apply_update(blob)
        self.doc = doc
        self.expected = {}
        self.pending = False

    def load(self, blobs: list[bytes]) -> dict:
        for blob in blobs:
            self.doc.apply_update(blob)
        self.base = list(blobs)
        self.accepted = []
        if not depth_ok(self.doc):
            raise Refused("stored document is nested too deep")
        return self.info()

    def info(self) -> dict:
        sv = self.doc.get_state()
        return {"empty": sv in (b"", bytes([0])), "pending": self.pending}

    def apply(self, update: bytes, has_types: bool, ends: dict[int, int]) -> dict:
        was_pending = self.pending
        before_expected = dict(self.expected)
        self.doc.apply_update(update)
        for client, end in ends.items():
            if end > self.expected.get(client, 0):
                self.expected[client] = end
        sv = self._state_vector()
        self.pending = any(end > sv.get(c, 0) for c, end in self.expected.items())
        if not self.pending:
            self.expected = {}
        if has_types or self.pending or was_pending:
            if not depth_ok(self.doc):
                self._rebuild()
                self.expected = before_expected
                self.pending = was_pending
                raise Refused("nested too deep")
        self.accepted.append(update)
        return self.info()

    def compact(self) -> bytes:
        state = self.doc.get_update()
        self.base = [state]
        self.accepted = []
        return state

    def restore(self, version: bytes) -> bytes:
        from web.backend.collab import content

        scratch = content.doc_from_update(self.doc.get_update())
        sv = scratch.get_state()
        content.restore_into(scratch, content.doc_from_update(version))
        update = scratch.get_update(sv)
        self.apply(update, True, {})
        return update


def depth_ok(doc) -> bool:
    """Every shared type within MAX_TYPE_DEPTH of its root, checked without
    recursion and within WALK_BUDGET steps (past the budget: refused)."""
    from pycrdt import Array, Map, Text, XmlElement, XmlFragment, XmlText

    from web.backend.collab import content

    budget = WALK_BUDGET
    stack: list[tuple[Any, int]] = []
    names = set(doc.keys())
    for name, kind in content.ROOTS.items():
        if name in names:
            stack.append((doc.get(name, type=kind), 0))
    if names - set(content.ROOTS):
        return False                      # a root no editor writes (guard refuses these)
    types = (XmlElement, XmlText, XmlFragment, Text, Map, Array)
    while stack:
        node, depth = stack.pop()
        budget -= 1
        if budget < 0:
            return False
        children: list[Any] = []
        if isinstance(node, (XmlFragment, XmlElement)):
            children = list(node.children)
        if isinstance(node, (XmlText, Text)):
            for chunk, _attrs in node.diff():
                budget -= 1
                if isinstance(chunk, types):
                    children.append(chunk)
        elif isinstance(node, Map):
            children = [v for v in node.values() if isinstance(v, types)]
        elif isinstance(node, Array):
            children = [v for v in node if isinstance(v, types)]
        for child in children:
            if depth + 1 > MAX_TYPE_DEPTH:
                return False
            stack.append((child, depth + 1))
    return True


# ─── Stateless jobs (the scratch worker) ──────────────────────────────


def merge_copy(base: bytes, payload: bytes) -> tuple[bytes, bool, bool]:
    """An offline copy: ``payload`` on top of ``base``. (state, changed, empty)."""
    from pycrdt import Doc

    doc = Doc()
    if base:
        doc.apply_update(base)
    before = doc.get_update()
    doc.apply_update(payload)
    if not depth_ok(doc):
        raise Refused("nested too deep")
    after = doc.get_update()
    return after, after != before, doc.get_state() in (b"", bytes([0]))


def preview(state: bytes) -> str:
    from web.backend.collab import content

    doc = content.doc_from_update(state)
    if not depth_ok(doc):
        raise Refused("nested too deep")
    return content.preview_text(doc)


# ─── The loop ─────────────────────────────────────────────────────────


def _limit_memory() -> None:
    limit = int(os.environ.get("DOMOVOI_COLLAB_WORKER_MEM", str(1024 * 1024 * 1024)))
    try:
        import resource  # Linux; Windows has none (timeouts + restarts there)

        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except Exception:  # noqa: BLE001
        pass


def handle(holder: Holder, op: str, args: dict, blobs: list[bytes]) -> tuple[dict, list[bytes]]:
    if op == "ping":
        return {}, []
    if op == "load":
        return holder.load(blobs), []
    if op == "apply":
        ends = {int(k): int(v) for k, v in (args.get("ends") or {}).items()}
        return holder.apply(blobs[0], bool(args.get("has_types")), ends), [holder.doc.get_state()]
    if op == "sv":
        return holder.info(), [holder.doc.get_state()]
    if op == "diff":
        return {}, [holder.doc.get_update(blobs[0])]
    if op == "encode":
        return holder.info(), [holder.doc.get_update()]
    if op == "compact":
        state = holder.compact()
        return holder.info(), [state]
    if op == "restore":
        update = holder.restore(blobs[0])
        return holder.info(), [update]
    if op == "merge_copy":
        state, changed, empty = merge_copy(blobs[0], blobs[1])
        return {"changed": changed, "empty": empty}, [state]
    if op == "merge_blobs":
        from pycrdt import Doc

        doc = Doc()
        for blob in blobs:
            doc.apply_update(blob)
        return {}, [doc.get_update()]
    if op == "preview":
        return {"text": preview(blobs[0])}, []
    if op == "crash":                     # tests only, and only when allowed
        if os.environ.get("DOMOVOI_COLLAB_WORKER_TEST") == "1":
            os._exit(int(args.get("code", 3)))
        raise ValueError("unknown op")
    if op == "sleep":                     # tests only
        if os.environ.get("DOMOVOI_COLLAB_WORKER_TEST") == "1":
            import time

            time.sleep(float(args.get("seconds", 1)))
            return {}, []
        raise ValueError("unknown op")
    raise ValueError(f"unknown op {op}")


def main() -> None:
    _limit_memory()
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    holder = Holder()
    while True:
        got = read_frame(stdin)
        if got is None:
            return
        header, blobs = got
        rid = header.get("id")
        try:
            result, out = handle(holder, header.get("op", ""), header.get("args") or {}, blobs)
            reply = frame({"id": rid, "ok": True, "result": result}, out)
        except Refused as e:
            reply = frame({"id": rid, "ok": False, "refused": True, "error": str(e)})
        except Exception as e:  # noqa: BLE001 — pycrdt refusing the bytes, a bad op
            reply = frame({"id": rid, "ok": False, "refused": True,
                           "error": f"{type(e).__name__}: {e}"[:300]})
        stdout.write(reply)
        stdout.flush()


if __name__ == "__main__":
    main()
