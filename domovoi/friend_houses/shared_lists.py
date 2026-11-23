"""Shared lists between friend houses, on the shared-state layer.

Friend Houses ("serverless messaging and friend system between households"),
phase F4. A household list (domovoi/lists.py) can be shared with one friend
house. Each house keeps its own copy in its own Lists, under its own name,
and the two send each other signed changes
(:mod:`domovoi.friend_houses.spaces`) that both apply the same way.

**Sharing.** An admin shares a list with a friend (:func:`share`); its items
go across as changes. At the friend's house the share waits for an admin to
**accept** it (it then becomes a new list there) or **decline** it (nothing
is kept and nobody is told). Either side can **stop sharing** (:func:`leave`);
both copies then stay as ordinary lists.

**What travels.** Items, their text and whether they are done. Not the
list's name (each house names its own copy), not the order, not who in the
house added what.

**Local edits.** Anything that changes a shared list here (the dashboard,
the apps, voice in a room, from the core or the web process) is noticed by
comparing the list with the shared copy kept beside it (:func:`sync_local`,
every few seconds while something is shared), and the differences go out
as changes. A friend's changes are applied through the same lists functions
in the same transaction that records them, so they never echo back.

**A friend's text in this house** (threat-model findings 6 and 33):
* changes from a friend only ever touch the one list its share was linked
  to here, through ``friend_houses_spaces.local_list_id``, never a list id
  the friend names;
* item text is cleaned (no control or bidi characters), capped at 200
  characters, and every wake phrase the house's speakers answer to is
  taken out before it is stored, so an item read aloud by the Lists voice
  handler can't wake and command a speaker;
* each change is checked again here (kind, size, clock), and a friend can
  change only the spaces shared with it.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any

from sqlalchemy import text

from domovoi import lists as core_lists
from domovoi.friend_houses import envelope as fe
from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import spaces as sp

log = logging.getLogger(__name__)

_LOCK = asyncio.Lock()
SYNC_EVERY_SEC = 5.0


class SharedListError(Exception):
    """Something a person can fix; the message says how."""


def _db():
    from domovoi.db.session import session_scope

    return session_scope()


def _service():
    from domovoi.friend_houses import service

    return service


async def _safe_text(value: str, phrases: list[str] | None = None, *,
                     cap: int = core_lists.TEXT_MAX) -> str:
    """A friend's item text as this house keeps it (see the module note).
    Cut to ``cap`` BEFORE the wake-phrase filter: a cut after it could end
    the text on a phrase the filter had seen as part of a longer word."""
    from domovoi.friend_houses import messages

    phrases = phrases if phrases is not None else await messages.wake_phrases()
    value = " ".join(str(value).split())[:cap]
    cleaned = messages.safe_to_say(value, phrases, limit=10_000)
    return " ".join(cleaned.split())[:cap].strip()


# ─── storage helpers ──────────────────────────────────────────────────────


async def _space(s, space_id: bytes) -> dict[str, Any] | None:
    row = (await s.execute(text(
        "SELECT id, kind, house_key, friend_id, state, origin, title, local_list_id, clock, "
        "synced_at FROM friend_houses_spaces WHERE id = :i"), {"i": space_id})).first()
    if row is None:
        return None
    keys = ("id", "kind", "house_key", "friend_id", "state", "origin", "title", "local_list_id",
            "clock", "synced_at")
    out = dict(zip(keys, row))
    out["id"], out["house_key"] = bytes(out["id"]), bytes(out["house_key"])
    return out



MAX_OFFERS_PER_FRIEND = 5     # lists a friend has offered that wait for an admin (R6 FH-4)
MAX_WAITING_OPS = 2_000       # changes an offered list may hold before it's accepted
MAX_ITEMS_KEPT = 5_000        # items a shared list remembers, removed ones included
OPS_KEPT = 2_000              # applied changes from a friend kept for spotting repeats

async def _items(s, space_id: bytes) -> tuple[dict[bytes, sp.Item], dict[bytes, int | None]]:
    rows = await s.execute(text(
        "SELECT uid, local_item_id, text, done, removed, text_at, text_by, done_at, done_by, "
        "removed_at FROM friend_houses_space_items WHERE space_id = :s"), {"s": space_id})
    items, local = {}, {}
    for r in rows:
        uid = bytes(r[0])
        items[uid] = sp.Item(uid, str(r[2]), (int(r[5]), bytes(r[6])), bool(r[3]),
                             (int(r[7]), bytes(r[8])),
                             (int(r[9]), b"") if r[4] and r[9] is not None else None)
        local[uid] = r[1]
    return items, local


async def _save_item(s, space_id: bytes, item: sp.Item, local_id: int | None) -> None:
    await s.execute(text(
        "INSERT INTO friend_houses_space_items (space_id, uid, local_item_id, text, done, removed, "
        "text_at, text_by, done_at, done_by, removed_at) VALUES (:s, :u, :l, :t, :d, :r, :ta, :tb, "
        ":da, :db, :ra) ON CONFLICT (space_id, uid) DO UPDATE SET local_item_id = EXCLUDED.local_item_id, "
        "text = EXCLUDED.text, done = EXCLUDED.done, removed = EXCLUDED.removed, "
        "text_at = EXCLUDED.text_at, text_by = EXCLUDED.text_by, done_at = EXCLUDED.done_at, "
        "done_by = EXCLUDED.done_by, removed_at = EXCLUDED.removed_at"),
        {"s": space_id, "u": item.uid, "l": local_id, "t": item.text, "d": item.done,
         "r": item.removed, "ta": item.text_at[0], "tb": item.text_at[1], "da": item.done_at[0],
         "db": item.done_at[1], "ra": item.removed_at[0] if item.removed_at else None})


async def _queue(s, space: dict[str, Any], ops: list[sp.Op], keys: fid.HouseKeys) -> None:
    """Record our own changes and queue them, up to 200 per signed envelope."""
    from domovoi.friend_houses import messages

    for start in range(0, len(ops), sp.MAX_OPS_PER_BATCH):
        chunk = ops[start: start + sp.MAX_OPS_PER_BATCH]
        body = sp.encode_batch(sp.Batch(space["id"], sp.KIND_LIST, tuple(chunk)))
        epoch, seq = await messages._next_seq(s)
        env = fe.signed(keys, kind=fe.KIND_SPACE, to=space["house_key"], body=body, epoch=epoch,
                        seq=seq, sent_at=int(time.time()))
        for i, op in enumerate(chunk):
            await s.execute(text(
                "INSERT INTO friend_houses_space_ops (space_id, direction, author, op_id, lamport, op, "
                "item_uid, value, envelope, msg_id, state) VALUES (:s, 'out', :a, :o, :l, :op, :u, :v, "
                ":env, :m, 'queued')"),
                {"s": space["id"], "a": keys.house_key, "o": op.op_id, "l": op.lamport, "op": op.op,
                 "u": None if op.item_uid == sp.NO_ITEM else op.item_uid, "v": op.value,
                 "env": env.to_bytes() if i == 0 else None, "m": env.msg_id})


async def _set_space(s, space_id: bytes, **fields: Any) -> None:
    allowed = {"state", "local_list_id", "clock", "synced_at", "friend_id"}
    assert set(fields) <= allowed
    sets = ", ".join(f"{k} = :{k}" for k in sorted(fields))
    await s.execute(text(f"UPDATE friend_houses_spaces SET {sets} WHERE id = :id"),
                    {**fields, "id": space_id})


async def _list_updated_at(s, list_id: int):
    row = (await s.execute(text("SELECT updated_at FROM lists WHERE id = :i"), {"i": list_id})).first()
    return row[0] if row else None


# ─── reflecting the shared items into the linked list ─────────────────────


async def _reflect(s, space: dict[str, Any], items: dict[bytes, sp.Item],
                   local: dict[bytes, int | None]) -> None:
    """Make the linked list show the shared items, then remember the list's
    state so the local comparison doesn't send it back."""
    list_id = space["local_list_id"]
    if list_id is None:
        return
    existing = {i.id: i for i in await core_lists.items_of(s, list_id)}
    visible_locals = {local.get(u) for u, it in items.items() if not it.removed and it.text}
    for uid, item in items.items():
        lid = local.get(uid)
        row = existing.get(lid) if lid is not None else None
        visible = not item.removed and bool(item.text)
        try:
            if visible and row is None:
                added, _outcome = await core_lists.add_item(s, list_id, item.text, via="friend")
                lid = added.id
                if added.done != item.done:
                    await core_lists.update_item(s, list_id, lid, done=item.done)
                existing[lid] = added
            elif visible and row is not None:
                if row.text != item.text or row.done != item.done:
                    # A friend's wording is shown as the friend's, never
                    # as the household member who first added the item.
                    await core_lists.update_item(
                        s, list_id, row.id,
                        item_text=item.text if row.text != item.text else None,
                        done=item.done if row.done != item.done else None,
                        friend_text=item.text_at[1] == space["house_key"])
            elif not visible and row is not None and row.id not in visible_locals:
                await core_lists.delete_item(s, list_id, row.id)
                lid = None
        except core_lists.ListError as e:
            log.info("Friend Houses: a shared item couldn't be placed (%s)", e.code)
        await _save_item(s, space["id"], item, lid)
    await _set_space(s, space["id"], synced_at=await _list_updated_at(s, list_id))


# ─── sharing (this house) ─────────────────────────────────────────────────


async def share(list_id: int, friend_id: str) -> dict[str, Any]:
    from domovoi.friend_houses import store

    keys = fid.load()
    if keys is None:
        raise SharedListError("Friend Houses isn't set up on this house yet.")
    async with _LOCK, _db() as s:
        friend = await store.friend(s, friend_id)
        if friend is None or friend.state != "active":
            raise SharedListError("That house isn't a friend any more.")
        the_list = await core_lists.get_list(s, list_id)
        if the_list is None:
            raise SharedListError("That list doesn't exist any more.")
        taken = (await s.execute(text(
            "SELECT 1 FROM friend_houses_spaces WHERE local_list_id = :l AND state = 'active'"),
            {"l": list_id})).first()
        if taken:
            raise SharedListError("That list is already shared. Stop sharing it first.")
        space_id = secrets.token_bytes(16)
        await s.execute(text(
            "INSERT INTO friend_houses_spaces (id, kind, house_key, friend_id, state, origin, title, "
            "local_list_id, clock) VALUES (:i, 'list', :h, :f, 'active', 'us', :t, :l, 1)"),
            {"i": space_id, "h": friend.house_key, "f": friend.id, "t": the_list.name, "l": list_id})
        space = await _space(s, space_id)
        assert space is not None
        clock = 1
        ops = [sp.Op("share", clock, value=the_list.name)]
        for row in await core_lists.items_of(s, list_id):
            uid = secrets.token_bytes(16)
            clock += 1
            ops.append(sp.Op("add", clock, uid, row.text))
            item = sp.Item(uid, row.text, (clock, keys.house_key))
            if row.done:
                clock += 1
                ops.append(sp.Op("done", clock, uid, "true"))
                item.done, item.done_at = True, (clock, keys.house_key)
            await _save_item(s, space_id, item, row.id)
        await _queue(s, space, ops, keys)
        await _set_space(s, space_id, clock=clock + 1, synced_at=the_list.updated_at)
    log.warning("Friend Houses: a list was shared with %s", friend.local_name)
    # Said aloud like a new friend, and on the status's notice for a week:
    # a list shared without the household knowing mustn't go unnoticed
    # (audit R6 FH-8).
    from domovoi.friend_houses import friending, messages

    phrases = await messages.wake_phrases()
    await friending.announce(
        f"{the_list.name} is now shared with {messages.safe_name(friend.local_name, phrases)}.")
    _service().spawn(_flush(friend.house_key), "friend-houses-flush")
    return {"id": space_id.hex(), "state": "active"}


async def leave(space_hex: str) -> bool:
    """Stop sharing. Both copies stay as ordinary lists; the friend is told."""
    keys = fid.load()
    try:
        space_id = bytes.fromhex(space_hex)
    except ValueError:
        return False
    async with _LOCK, _db() as s:
        space = await _space(s, space_id)
        if space is None or space["state"] == "left":
            return False
        if space["state"] == "active" and keys is not None:
            await _queue(s, space, [sp.Op("leave", space["clock"])], keys)
        await _set_space(s, space_id, state="left", local_list_id=None,
                         clock=space["clock"] + 1)
        house = space["house_key"]
    _service().spawn(_flush(house), "friend-houses-flush")
    return True


# ─── receiving (from a friend) ────────────────────────────────────────────


async def on_envelope(friend: Any, env: fe.Envelope) -> bool:
    """A batch of changes from ``friend`` (already authenticated and signed).
    Returns True to acknowledge it: also for a batch that is dropped as
    unreadable or not this friend's to change, so it isn't sent forever."""
    try:
        batch, skipped = sp.parse_batch(env.body)
    except sp.BadBatch as e:
        log.warning("Friend Houses: an unreadable shared-list change was dropped (%s)", e)
        return True
    if skipped:
        log.info("Friend Houses: %d malformed shared-list change(s) skipped", skipped)
    from domovoi.friend_houses import messages

    phrases = await messages.wake_phrases()
    async with _LOCK, _db() as s:
        space = await _space(s, batch.space_id)
        if space is None:
            share = next((op for op in batch.ops if op.op == "share"), None)
            if share is None:
                return True  # changes to something never shared with us
            if not sp.first_clock_allowed(share.lamport):
                log.info("Friend Houses: a shared list offered with a runaway clock was dropped")
                return True
            offers = (await s.execute(text(
                "SELECT count(*) FROM friend_houses_spaces WHERE house_key = :h AND state = 'invited'"),
                {"h": friend.house_key})).scalar_one()
            if offers >= MAX_OFFERS_PER_FRIEND:
                # Decline some first: one friend can't fill the page (R6 FH-4).
                log.info("Friend Houses: %s offered more lists than can wait; dropped", friend.local_name)
                return True
            title = await _safe_text(share.value, phrases, cap=60) or "A shared list"
            await s.execute(text(
                "INSERT INTO friend_houses_spaces (id, kind, house_key, friend_id, state, origin, "
                "title, clock) VALUES (:i, 'list', :h, :f, 'invited', 'them', :t, :c)"),
                {"i": batch.space_id, "h": friend.house_key, "f": friend.id, "t": title[:60],
                 "c": share.lamport + 1})
            space = await _space(s, batch.space_id)
            assert space is not None
            log.info("Friend Houses: %s shared a list with this house", friend.local_name)
        if space["house_key"] != friend.house_key:
            log.warning("Friend Houses: a friend tried to change a space it isn't part of")
            return True
        if space["state"] == "left":
            return True
        items, local = await _items(s, space["id"])
        clock, state = space["clock"], space["state"]
        waiting = 0
        if state == "invited":
            waiting = (await s.execute(text(
                "SELECT count(*) FROM friend_houses_space_ops WHERE space_id = :s AND state = 'waiting'"),
                {"s": space["id"]})).scalar_one()
        for op in batch.ops:
            if state == "invited" and waiting >= MAX_WAITING_OPS:
                break  # an offer can't grow without bound before anyone accepts (R6 FH-4)
            if op.item_uid != sp.NO_ITEM and op.item_uid not in items and len(items) >= MAX_ITEMS_KEPT:
                continue  # removed items count here too, so a list can't grow for ever
            if not sp.clock_allowed(clock, op.lamport):
                log.info("Friend Houses: a shared-list change with a runaway clock was skipped")
                continue
            dup = (await s.execute(text(
                "SELECT 1 FROM friend_houses_space_ops WHERE space_id = :s AND author = :a AND op_id = :o"),
                {"s": space["id"], "a": friend.house_key, "o": op.op_id})).first()
            if dup:
                continue
            value = op.value
            if op.op in ("add", "text", "share"):
                value = await _safe_text(value, phrases)
                if not value:
                    continue
            clean = sp.Op(op.op, op.lamport, op.item_uid, value, op.op_id)
            await s.execute(text(
                "INSERT INTO friend_houses_space_ops (space_id, direction, author, op_id, lamport, op, "
                "item_uid, value, state) VALUES (:s, 'in', :a, :o, :l, :op, :u, :v, :st)"),
                {"s": space["id"], "a": friend.house_key, "o": op.op_id, "l": op.lamport, "op": op.op,
                 "u": None if op.item_uid == sp.NO_ITEM else op.item_uid, "v": value,
                 "st": "applied" if state == "active" else "waiting"})
            waiting += state == "invited"
            clock = sp.next_clock(clock, op.lamport)
            if op.op == "leave":
                state = "left"
            elif state == "active" and (
                    op.item_uid in items or _visible(items) < core_lists.MAX_ITEMS_PER_LIST + 100):
                # Only items still on the list count: removed ones used to,
                # so 600 removes froze a list for good (R6 FH-15).
                sp.merge(items, clean, friend.house_key)
        if state == "left":
            if space["state"] == "invited":
                # Withdrawn before anyone here accepted it: nothing local to
                # keep, so the offer and what it held are forgotten, as a
                # decline would.
                await s.execute(text("DELETE FROM friend_houses_spaces WHERE id = :i"),
                                {"i": space["id"]})
            else:
                await _set_space(s, space["id"], state="left", local_list_id=None, clock=clock)
            log.info("Friend Houses: %s stopped sharing a list", friend.local_name)
            return True
        await _set_space(s, space["id"], clock=clock)
        if state == "active":
            space["clock"] = clock
            await _reflect(s, space, items, local)
            await _prune_ops(s, space["id"])
    return True


def _visible(items: dict[bytes, sp.Item]) -> int:
    return sum(1 for i in items.values() if not i.removed)


async def _prune_ops(s, space_id: bytes) -> None:
    """Keep the newest :data:`OPS_KEPT` applied changes from a friend (for
    spotting a batch sent twice); older ones are already in the items.
    Counted by rows, so changes sharing one clock value are pruned too."""
    await s.execute(text(
        "DELETE FROM friend_houses_space_ops WHERE space_id = :s AND direction = 'in' "
        "AND state = 'applied' AND id NOT IN (SELECT id FROM friend_houses_space_ops "
        "WHERE space_id = :s AND direction = 'in' AND state = 'applied' "
        "ORDER BY lamport DESC, id DESC LIMIT :k)"), {"s": space_id, "k": OPS_KEPT})


async def accept(space_hex: str, *, name: str | None = None) -> dict[str, Any]:
    """Accept a list a friend shared: it becomes a new list here (named as
    the friend named it unless the admin chooses another name)."""
    try:
        space_id = bytes.fromhex(space_hex)
    except ValueError as exc:
        raise SharedListError("No such shared list.") from exc
    async with _LOCK, _db() as s:
        space = await _space(s, space_id)
        if space is None or space["state"] != "invited":
            raise SharedListError("That shared list isn't waiting any more.")
        from domovoi.friend_houses import blocking, store

        friend = await store.friend_by_house(s, space["house_key"])
        if (friend is None or friend.state != "active"
                or await blocking.is_blocked(space["house_key"])):
            raise SharedListError("The house that shared it isn't a friend any more.")
        try:
            created = await core_lists.create_list(s, name or space["title"])
        except core_lists.ListError as e:
            raise SharedListError(
                f"{e.message[:1].upper()}{e.message[1:]}. Choose another name for it.") from e
        await _set_space(s, space_id, state="active", local_list_id=created.id)
        space["local_list_id"], space["state"] = created.id, "active"
        items, local = await _items(s, space_id)
        rows = await s.execute(text(
            "SELECT op, lamport, item_uid, value, op_id, author FROM friend_houses_space_ops "
            "WHERE space_id = :s AND state = 'waiting' ORDER BY lamport, author"), {"s": space_id})
        for r in rows.all():
            sp.merge(items, sp.Op(r[0], int(r[1]), bytes(r[2]) if r[2] else sp.NO_ITEM, r[3] or "",
                                  bytes(r[4])), bytes(r[5]))
        await s.execute(text(
            "UPDATE friend_houses_space_ops SET state = 'applied' WHERE space_id = :s AND state = 'waiting'"),
            {"s": space_id})
        await _reflect(s, space, items, local)
    return {"list_id": created.id, "name": created.name}


async def decline(space_hex: str) -> bool:
    """Forget a list a friend offered. The friend isn't told."""
    try:
        space_id = bytes.fromhex(space_hex)
    except ValueError:
        return False
    async with _LOCK, _db() as s:
        res = await s.execute(text(
            "DELETE FROM friend_houses_spaces WHERE id = :i AND state = 'invited'"), {"i": space_id})
    return bool(res.rowcount)


# ─── local edits become changes ───────────────────────────────────────────


async def sync_local() -> int:
    """Compare every shared list with its shared copy and send what changed
    here. Returns how many changes were queued."""
    keys = fid.load()
    if keys is None:
        return 0
    queued, houses = 0, set()
    async with _LOCK, _db() as s:
        spaces = (await s.execute(text(
            "SELECT id FROM friend_houses_spaces WHERE state = 'active'"))).all()
        for (raw_id,) in spaces:
            # Each list on its own: one that fails (whatever a friend sent)
            # never stops the others from syncing (audit R6 FH-5).
            try:
                async with s.begin_nested():
                    q, h = await _sync_one(s, bytes(raw_id), keys)
            except Exception:  # noqa: BLE001
                log.exception("Friend Houses: syncing one shared list failed; the others go on")
                continue
            queued += q
            houses |= h
    for house in houses:
        _service().spawn(_flush(house), "friend-houses-flush")
    return queued


async def _sync_one(s, space_id: bytes, keys: fid.HouseKeys) -> tuple[int, set[bytes]]:
    """One shared list: what changed here since the last sync, queued."""
    space = await _space(s, space_id)
    if space is None:
        return 0, set()
    if space["friend_id"] is None or space["local_list_id"] is None:
        # The friend was removed, or the list was deleted here: stop.
        houses = set()
        if space["friend_id"] is not None:
            await _queue(s, space, [sp.Op("leave", space["clock"])], keys)
            houses.add(space["house_key"])
        await _set_space(s, space["id"], state="left", local_list_id=None)
        return 0, houses
    updated = await _list_updated_at(s, space["local_list_id"])
    if updated is None or (space["synced_at"] and updated <= space["synced_at"]):
        return 0, set()
    ops = await _diff(s, space, keys)
    if ops:
        await _queue(s, space, ops, keys)
    await _set_space(s, space["id"], synced_at=updated, clock=space["clock"])
    return len(ops), ({space["house_key"]} if ops else set())


async def _diff(s, space: dict[str, Any], keys: fid.HouseKeys) -> list[sp.Op]:
    items, local = await _items(s, space["id"])
    rows = {r.id: r for r in await core_lists.items_of(s, space["local_list_id"])}
    by_local: dict[int, list[bytes]] = {}
    for uid, lid in local.items():
        if lid is not None:
            by_local.setdefault(lid, []).append(uid)
    ops: list[sp.Op] = []

    def stamp() -> int:
        space["clock"] += 1
        return space["clock"] - 1

    me = keys.house_key
    for lid, row in rows.items():
        uids = [u for u in by_local.get(lid, []) if not items[u].removed]
        if not uids:
            uid = secrets.token_bytes(16)
            op = sp.Op("add", stamp(), uid, row.text)
            ops.append(op)
            sp.merge(items, op, me)
            local[uid] = lid
            if row.done:
                op = sp.Op("done", stamp(), uid, "true")
                ops.append(op)
                sp.merge(items, op, me)
            continue
        for uid in uids:
            item = items[uid]
            if item.text != row.text:
                op = sp.Op("text", stamp(), uid, row.text)
                ops.append(op)
                sp.merge(items, op, me)
            if item.done != row.done:
                op = sp.Op("done", stamp(), uid, "true" if row.done else "false")
                ops.append(op)
                sp.merge(items, op, me)
    for uid, item in items.items():
        lid = local.get(uid)
        if not item.removed and item.text and (lid is None or lid not in rows):
            op = sp.Op("remove", stamp(), uid)
            ops.append(op)
            sp.merge(items, op, me)
            local[uid] = None
    for uid, item in items.items():
        await _save_item(s, space["id"], item, local.get(uid))
    return ops


# ─── delivery (rides the message outbox) ──────────────────────────────────


async def queued_envelopes(house_key: bytes) -> list[tuple[bytes, bytes]]:
    """``(msg_id, envelope)`` for every queued batch to one friend, oldest first."""
    async with _db() as s:
        rows = await s.execute(text(
            "SELECT o.msg_id, o.envelope FROM friend_houses_space_ops o "
            "JOIN friend_houses_spaces sp ON sp.id = o.space_id "
            "WHERE sp.house_key = :h AND o.direction = 'out' AND o.state = 'queued' "
            "AND o.envelope IS NOT NULL ORDER BY o.id"), {"h": house_key})
        return [(bytes(r[0]), bytes(r[1])) for r in rows]


async def mark_sent(msg_id: bytes) -> None:
    async with _db() as s:
        await s.execute(text(
            "UPDATE friend_houses_space_ops SET state = 'sent' WHERE msg_id = :m AND direction = 'out'"),
            {"m": msg_id})


async def _flush(house: bytes) -> None:
    from domovoi.friend_houses import messages

    await messages.flush(house)


# ─── for the dashboard ────────────────────────────────────────────────────


async def overview() -> list[dict[str, Any]]:
    async with _db() as s:
        rows = await s.execute(text(
            "SELECT sp.id, sp.state, sp.origin, sp.title, sp.local_list_id, l.name, "
            "f.local_name, sp.friend_id, sp.created_at, "
            "(SELECT count(*) FROM friend_houses_space_ops o WHERE o.space_id = sp.id "
            " AND o.direction = 'out' AND o.state = 'queued') "
            "FROM friend_houses_spaces sp LEFT JOIN lists l ON l.id = sp.local_list_id "
            "LEFT JOIN friend_houses_friends f ON f.id = sp.friend_id "
            "WHERE sp.state <> 'left' ORDER BY sp.created_at LIMIT 200"))
        return [{"id": bytes(r[0]).hex(), "state": r[1], "origin": r[2], "title": r[3],
                 "list_id": r[4], "list_name": r[5], "friend": r[6], "friend_id": r[7],
                 "created_at": r[8].isoformat(), "waiting_to_send": int(r[9])} for r in rows]


async def any_active() -> bool:
    async with _db() as s:
        return bool((await s.execute(text(
            "SELECT 1 FROM friend_houses_spaces WHERE state = 'active' LIMIT 1"))).first())
