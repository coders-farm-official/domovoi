"""The shared-state layer: signed, ordered changes two houses both apply.

Friend Houses ("serverless messaging and friend system between households"),
phase F4. Owner decision 9: "one small shared-state layer of signed, ordered
changes, which turn-based shared activities can reuse later". A shared
list is the first user of it.

**A space** is one thing two houses share (a list today). Its id is 16
random bytes chosen by the house that shared it. Each house keeps its own
copy and sends the other its **changes**, a batch at a time, in one signed
envelope (:data:`domovoi.friend_houses.envelope.KIND_SPACE`). There is no
server and no leader: both houses apply every change themselves, and both
end with the same result whatever order the changes arrive in.

**Ordering.** Every change carries a Lamport clock. A house sends one past
the highest it has seen or sent, and orders changes by (clock, author's
house key), a total order both houses agree on.

**Merging** (per item, per field, last writer wins by that order):

* ``add`` creates an item (a random 16-byte uid, so two houses never
  collide) with its text;
* ``text`` and ``done`` set a field when their stamp is newer than the
  field's;
* ``remove`` is final: a removed item stays removed (no resurrection by a
  late edit), so a delete is never undone by a slow friend;
* ``share`` names the space for the house it's shared with; ``leave``
  ends it.

**Each house checks every change itself** (threat-model findings 20 and
33): sizes and kinds are fixed, text is cleaned (and, for lists, the
caller takes wake phrases out), and a clock may run at most
:data:`MAX_CLOCK_JUMP` ahead of what this house has seen, so a hostile
friend can't jump the clock to make its edits win forever.

This module is pure (no database), so the merge rules are tested on their
own; :mod:`domovoi.friend_houses.shared_lists` stores and applies them.
"""

from __future__ import annotations

import secrets
import struct
from dataclasses import dataclass, field

from domovoi.friend_houses import envelope as fe

KIND_LIST = 1
OPS = {"share": 1, "add": 2, "text": 3, "done": 4, "remove": 5, "leave": 6}
OP_NAMES = {v: k for k, v in OPS.items()}
MAX_OPS_PER_BATCH = 200
MAX_VALUE_BYTES = 800          # 200 characters of UTF-8 at worst
MAX_CLOCK_JUMP = 100_000
# No clock ever reaches this: far below BIGINT's top, so +1 can't overflow
# the column (audit R6 FH-5: a first share at 2^63-2 broke every sync).
CLOCK_MAX = 1 << 50
NO_ITEM = bytes(16)

_HEAD = struct.Struct(">16sBH")          # space id, kind, count
_OP = struct.Struct(">16sQB16sH")       # op id, lamport, op, item uid, value length


class BadBatch(ValueError):
    """A batch that can't be read at all. (Single bad changes inside a
    readable batch are skipped, not fatal: see :func:`parse_batch`.)"""


@dataclass(frozen=True)
class Op:
    op: str
    lamport: int
    item_uid: bytes = NO_ITEM
    value: str = ""
    op_id: bytes = field(default_factory=lambda: secrets.token_bytes(16))


@dataclass(frozen=True)
class Batch:
    space_id: bytes
    kind: int
    ops: tuple[Op, ...]


def encode_batch(batch: Batch) -> bytes:
    if not 1 <= len(batch.ops) <= MAX_OPS_PER_BATCH:
        raise ValueError("a batch holds 1 to 200 changes")
    out = bytearray(_HEAD.pack(batch.space_id, batch.kind, len(batch.ops)))
    for op in batch.ops:
        value = op.value.encode("utf-8")
        if len(value) > MAX_VALUE_BYTES:
            raise ValueError("value too long")
        out += _OP.pack(op.op_id, op.lamport, OPS[op.op], op.item_uid, len(value)) + value
    if len(out) > fe.ENVELOPE_MAX - 300:
        raise ValueError("batch too large for one envelope")
    return bytes(out)


def parse_batch(body: bytes) -> tuple[Batch, int]:
    """A batch and how many of its changes were skipped as unreadable.
    Raises :class:`BadBatch` only when the batch as a whole can't be read."""
    if len(body) < _HEAD.size:
        raise BadBatch("short")
    space_id, kind, count = _HEAD.unpack(body[: _HEAD.size])
    if kind != KIND_LIST or not 1 <= count <= MAX_OPS_PER_BATCH:
        raise BadBatch("kind or count")
    pos, ops, skipped = _HEAD.size, [], 0
    for _ in range(count):
        if len(body) - pos < _OP.size:
            raise BadBatch("truncated")
        op_id, lamport, code, uid, vlen = _OP.unpack(body[pos: pos + _OP.size])
        pos += _OP.size
        if vlen > MAX_VALUE_BYTES or len(body) - pos < vlen:
            raise BadBatch("value length")
        raw = body[pos: pos + vlen]
        pos += vlen
        name = OP_NAMES.get(code)
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError:
            name = None
        if name is None or lamport == 0 or not _shape_ok(name, uid, value):
            skipped += 1
            continue
        ops.append(Op(name, lamport, uid, value, op_id))
    if pos != len(body):
        raise BadBatch("trailing bytes")
    return Batch(space_id, kind, tuple(ops)), skipped


def _shape_ok(name: str, uid: bytes, value: str) -> bool:
    item_op = name in ("add", "text", "done", "remove")
    if item_op == (uid == NO_ITEM):
        return False
    if name == "done":
        return value in ("true", "false")
    if name in ("add", "text", "share"):
        return 0 < len(fe.clean_text(value)) <= 200
    return value == ""


# ─── merging ──────────────────────────────────────────────────────────────

Stamp = tuple[int, bytes]  # (lamport, author house key)


@dataclass
class Item:
    uid: bytes
    text: str
    text_at: Stamp
    done: bool = False
    done_at: Stamp = (0, b"")
    removed_at: Stamp | None = None

    @property
    def removed(self) -> bool:
        return self.removed_at is not None


def merge(items: dict[bytes, Item], op: Op, author: bytes) -> bool:
    """Apply one change to the shared items. True when anything changed.
    Commutative per field: applying the same set of changes in any order
    gives the same items."""
    stamp: Stamp = (op.lamport, author)
    item = items.get(op.item_uid)
    if op.op == "add":
        if item is None:
            items[op.item_uid] = Item(op.item_uid, op.value, stamp)
            return True
        if stamp > item.text_at:  # the same uid twice: the newer text wins
            item.text, item.text_at = op.value, stamp
            return True
        return False
    if item is None:
        # A change to an item this house hasn't seen added yet: keep a
        # placeholder so the add, when it comes, merges into it.
        if op.op == "remove":
            items[op.item_uid] = Item(op.item_uid, "", (0, b""), removed_at=stamp)
            return True
        if op.op in ("text", "done"):
            placeholder = Item(op.item_uid, "", (0, b""))
            items[op.item_uid] = placeholder
            item = placeholder
        else:
            return False
    if op.op == "remove":
        if item.removed_at is None or stamp > item.removed_at:
            item.removed_at = stamp
            return True
        return False
    # A removed item still takes field changes (invisibly), so the merge
    # stays the same in any order; it stays removed whatever they say.
    if op.op == "text" and stamp > item.text_at:
        item.text, item.text_at = op.value, stamp
        return True
    if op.op == "done" and stamp > item.done_at:
        item.done, item.done_at = op.value == "true", stamp
        return True
    return False


def next_clock(current: int, seen: int) -> int:
    """The space's clock after seeing a change with clock ``seen``."""
    return max(current, seen + 1)


def clock_allowed(current: int, seen: int) -> bool:
    return 0 < seen <= current + MAX_CLOCK_JUMP and seen < CLOCK_MAX


def first_clock_allowed(seen: int) -> bool:
    """A space's first change (a friend's share) sets its clock, so it is
    held to the same jump as every later one: from zero (R6 FH-5)."""
    return clock_allowed(0, seen)
