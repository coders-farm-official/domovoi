"""Household lists (V048): to-do, shopping, and any list the house names.

One module for everything that reads or writes ``lists`` / ``list_items``:
the voice handler (``domovoi/handlers/lists.py``) and the web API
(``web/backend/api/lists.py``, which the dashboard and the apps use) both
call these functions, so a list behaves the same whichever way it is
changed. Every write queues a ``lists_changed`` NOTIFY on the caller's
transaction (delivered on COMMIT only); the web turns it into a
``lists.changed`` event on ``/ws/state`` so an item said to a room shows up
on an open Lists page at once.

Names. A list is found by its *key* (:func:`name_key`): the name folded to
lowercase letters and digits, a trailing "list" dropped, and the words
people use for the two built-in lists folded onto them ("todo", "to do",
"to-do", "to-dos" are the To-do list; "grocery", "groceries" the Shopping
list). The key is unique, so the dashboard can't make "Groceries" beside
"Shopping", and voice finds "my grocery list" without a second list.

Items. Adding an item that is already open on the list adds nothing
(``outcome == "already"``); adding one that is ticked off opens it again at
the end (``"reopened"``), so "add milk" every week doesn't pile up old
milks. Items are matched by :func:`item_key` (lowercase words, a leading
"a"/"an"/"the"/"some" dropped); finding one to cross off or remove also
accepts a close spelling (:func:`find_items`), adding never does.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

LISTS_CHANGED_CHANNEL = "lists_changed"

NAME_MAX = 60
TEXT_MAX = 200
MAX_LISTS = 50
MAX_ITEMS_PER_LIST = 500

# Keys the two built-in lists answer to, besides their own.
_KEY_SYNONYMS = {
    "todo": "todo", "todos": "todo", "thingstodo": "todo", "todolist": "todo", "todoes": "todo",
    "grocery": "shopping", "groceries": "shopping", "groceryshopping": "shopping",
    "foodshopping": "shopping", "shop": "shopping",
}
_TRAILING_LIST_RE = re.compile(r"(?:\s|-|_)+lists?\s*$", re.IGNORECASE)
_LEADING_ARTICLE_RE = re.compile(r"^(?:a|an|the|some|my|our)\s+")


class ListError(ValueError):
    """A request the lists can't take; ``code`` is machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def clean_name(name: str) -> str:
    """The name as kept: whitespace collapsed, a trailing "list" dropped
    ("Camping list" is the Camping list, said "my camping list")."""
    n = " ".join(str(name or "").split())
    stripped = _TRAILING_LIST_RE.sub("", n).strip()
    return stripped or n


def name_key(name: str) -> str:
    """The key a list is found by (see the module note)."""
    k = re.sub(r"[^a-z0-9]", "", _fold(clean_name(name)))
    return _KEY_SYNONYMS.get(k, k)[:NAME_MAX]


def clean_text(s: str) -> str:
    return " ".join(str(s or "").split())


def item_key(s: str) -> str:
    """An item folded for matching: lowercase words, punctuation dropped,
    a leading article dropped ("the milk" is "milk")."""
    t = re.sub(r"[^a-z0-9 ]", " ", _fold(s))
    t = " ".join(t.split())
    return _LEADING_ARTICLE_RE.sub("", t)


def spoken_name(name: str) -> str:
    """How a list's name sits in a sentence: "your to-do list", "your
    camping list" — lowercased unless it's written like a proper noun or
    an acronym inside ("BBQ", "McKenzie")."""
    if any(c.isupper() for c in name[1:]):
        return name
    return name[:1].lower() + name[1:]


# ─── rows ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ListRow:
    id: int
    name: str
    position: int
    open_count: int
    done_count: int
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "position": self.position,
            "open_count": self.open_count, "done_count": self.done_count,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ItemRow:
    id: int
    list_id: int
    text: str
    done: bool
    done_at: datetime | None
    position: int
    added_by_person_id: int | None
    added_by_name: str | None
    added_by_room: str | None
    added_via: str
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "list_id": self.list_id, "text": self.text,
            "done": self.done, "done_at": self.done_at, "position": self.position,
            "added_by_person_id": self.added_by_person_id,
            "added_by_name": self.added_by_name, "added_by_room": self.added_by_room,
            "added_via": self.added_via,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }


_LIST_SELECT = """
    SELECT l.id, l.name, l.position,
           COUNT(i.id) FILTER (WHERE NOT i.done) AS open_count,
           COUNT(i.id) FILTER (WHERE i.done) AS done_count,
           l.created_at, l.updated_at
    FROM lists l LEFT JOIN list_items i ON i.list_id = l.id
"""
_LIST_GROUP = " GROUP BY l.id ORDER BY l.position ASC, l.id ASC"

_ITEM_SELECT = """
    SELECT i.id, i.list_id, i.text, i.done, i.done_at, i.position,
           i.added_by_person_id, p.name, i.added_by_room, i.added_via,
           i.created_at, i.updated_at
    FROM list_items i LEFT JOIN people p ON p.id = i.added_by_person_id
"""


def _list_row(r: Any) -> ListRow:
    return ListRow(int(r[0]), str(r[1]), int(r[2]), int(r[3] or 0), int(r[4] or 0), r[5], r[6])


def _item_row(r: Any) -> ItemRow:
    return ItemRow(
        id=int(r[0]), list_id=int(r[1]), text=str(r[2]), done=bool(r[3]), done_at=r[4],
        position=int(r[5]), added_by_person_id=r[6], added_by_name=r[7],
        added_by_room=r[8], added_via=str(r[9]), created_at=r[10], updated_at=r[11],
    )


async def notify(session: AsyncSession, reason: str) -> None:
    """Queue a ``lists_changed`` NOTIFY on ``session``'s transaction."""
    await session.execute(
        text("SELECT pg_notify(:channel, :reason)"),
        {"channel": LISTS_CHANGED_CHANNEL, "reason": reason},
    )


async def _touch_list(session: AsyncSession, list_id: int) -> None:
    await session.execute(text("UPDATE lists SET updated_at = now() WHERE id = :id"), {"id": list_id})


# ─── lists ───────────────────────────────────────────────────────────────


async def all_lists(session: AsyncSession) -> list[ListRow]:
    rows = await session.execute(text(_LIST_SELECT + _LIST_GROUP))
    return [_list_row(r) for r in rows.all()]


async def get_list(session: AsyncSession, list_id: int) -> ListRow | None:
    rows = await session.execute(text(_LIST_SELECT + " WHERE l.id = :id" + _LIST_GROUP), {"id": list_id})
    r = rows.first()
    return _list_row(r) if r is not None else None


async def find_list(session: AsyncSession, spoken: str) -> ListRow | None:
    """The list whose key matches ``spoken`` ("my to do", "Groceries")."""
    key = name_key(spoken)
    if not key:
        return None
    rows = await session.execute(
        text(_LIST_SELECT + " WHERE l.name_key = :k" + _LIST_GROUP), {"k": key}
    )
    r = rows.first()
    return _list_row(r) if r is not None else None


def valid_name(name: str) -> tuple[str, str]:
    n = clean_name(name)
    if not n:
        raise ListError("name_required", "a list needs a name")
    if len(n) > NAME_MAX:
        raise ListError("name_too_long", f"a list name is at most {NAME_MAX} characters")
    if any(unicodedata.category(c) == "Cc" for c in n):
        raise ListError("name_invalid", "a list name can't hold control characters")
    key = name_key(n)
    if not key:
        raise ListError("name_invalid", "a list name needs a letter or a digit")
    return n, key


async def _key_taken(session: AsyncSession, key: str, but: int | None = None) -> str | None:
    row = (await session.execute(
        text("SELECT name FROM lists WHERE name_key = :k AND (CAST(:but AS INT) IS NULL OR id <> CAST(:but AS INT))"),
        {"k": key, "but": but},
    )).first()
    return str(row[0]) if row is not None else None


async def create_list(session: AsyncSession, name: str) -> ListRow:
    n, key = valid_name(name)
    taken = await _key_taken(session, key)
    if taken is not None:
        raise ListError("name_taken", f"there's already a list called {taken}")
    count = (await session.execute(text("SELECT COUNT(*) FROM lists"))).scalar_one()
    if int(count) >= MAX_LISTS:
        raise ListError("too_many_lists", f"a household can keep up to {MAX_LISTS} lists")
    row = await session.execute(
        text(
            """
            INSERT INTO lists (name, name_key, position)
            VALUES (:n, :k, COALESCE((SELECT MAX(position) FROM lists), 0) + 1)
            RETURNING id
            """
        ),
        {"n": n, "k": key},
    )
    list_id = int(row.scalar_one())
    await notify(session, "list_created")
    created = await get_list(session, list_id)
    assert created is not None
    return created


async def rename_list(session: AsyncSession, list_id: int, name: str) -> ListRow | None:
    n, key = valid_name(name)
    taken = await _key_taken(session, key, but=list_id)
    if taken is not None:
        raise ListError("name_taken", f"there's already a list called {taken}")
    res = await session.execute(
        text("UPDATE lists SET name = :n, name_key = :k, updated_at = now() WHERE id = :id"),
        {"n": n, "k": key, "id": list_id},
    )
    if (res.rowcount or 0) == 0:
        return None
    await notify(session, "list_renamed")
    return await get_list(session, list_id)


async def delete_list(session: AsyncSession, list_id: int) -> bool:
    res = await session.execute(text("DELETE FROM lists WHERE id = :id"), {"id": list_id})
    if (res.rowcount or 0) == 0:
        return False
    await notify(session, "list_deleted")
    return True


# ─── items ───────────────────────────────────────────────────────────────


async def items_of(session: AsyncSession, list_id: int, *, open_only: bool = False) -> list[ItemRow]:
    where = " WHERE i.list_id = :id" + (" AND NOT i.done" if open_only else "")
    rows = await session.execute(
        text(_ITEM_SELECT + where + " ORDER BY i.position ASC, i.id ASC"), {"id": list_id}
    )
    return [_item_row(r) for r in rows.all()]


async def get_item(session: AsyncSession, list_id: int, item_id: int) -> ItemRow | None:
    rows = await session.execute(
        text(_ITEM_SELECT + " WHERE i.list_id = :l AND i.id = :id"), {"l": list_id, "id": item_id}
    )
    r = rows.first()
    return _item_row(r) if r is not None else None


def _valid_text(s: str) -> str:
    t = clean_text(s)
    if not t:
        raise ListError("text_required", "an item needs some text")
    if len(t) > TEXT_MAX:
        raise ListError("text_too_long", f"an item is at most {TEXT_MAX} characters")
    if any(unicodedata.category(c) == "Cc" for c in t):
        raise ListError("text_invalid", "an item can't hold control characters")
    return t


AddOutcome = Literal["added", "reopened", "already"]


async def add_item(
    session: AsyncSession,
    list_id: int,
    item_text: str,
    *,
    person_id: int | None = None,
    room: str | None = None,
    via: Literal["voice", "screen"] = "screen",
) -> tuple[ItemRow, AddOutcome]:
    """Add ``item_text`` to the end of the list (see the module note for
    "already" and "reopened")."""
    t = _valid_text(item_text)
    key = item_key(t)
    same = [i for i in await items_of(session, list_id) if item_key(i.text) == key] if key else []
    open_same = [i for i in same if not i.done]
    if open_same:
        return open_same[0], "already"
    next_pos = "COALESCE((SELECT MAX(position) FROM list_items WHERE list_id = :l), 0) + 1"
    if same:
        item = same[0]
        await session.execute(
            text(
                f"UPDATE list_items SET done = FALSE, done_at = NULL, position = {next_pos}, "
                "updated_at = now() WHERE id = :id"
            ),
            {"l": list_id, "id": item.id},
        )
        await _touch_list(session, list_id)
        await notify(session, "item_reopened")
        again = await get_item(session, list_id, item.id)
        assert again is not None
        return again, "reopened"
    count = (await session.execute(
        text("SELECT COUNT(*) FROM list_items WHERE list_id = :l"), {"l": list_id}
    )).scalar_one()
    if int(count) >= MAX_ITEMS_PER_LIST:
        raise ListError("list_full", f"a list holds up to {MAX_ITEMS_PER_LIST} items")
    row = await session.execute(
        text(
            f"""
            INSERT INTO list_items (list_id, text, position, added_by_person_id,
                                    added_by_room, added_via)
            VALUES (:l, :t, {next_pos}, :p, :r, :v)
            RETURNING id
            """
        ),
        {"l": list_id, "t": t, "p": person_id, "r": room, "v": via},
    )
    item_id = int(row.scalar_one())
    await _touch_list(session, list_id)
    await notify(session, "item_added")
    added = await get_item(session, list_id, item_id)
    assert added is not None
    return added, "added"


async def update_item(
    session: AsyncSession,
    list_id: int,
    item_id: int,
    *,
    item_text: str | None = None,
    done: bool | None = None,
) -> ItemRow | None:
    sets: list[str] = []
    params: dict[str, Any] = {"l": list_id, "id": item_id}
    if item_text is not None:
        sets.append("text = :t")
        params["t"] = _valid_text(item_text)
    if done is not None:
        sets.append("done = CAST(:d AS BOOLEAN)")
        sets.append("done_at = CASE WHEN CAST(:d AS BOOLEAN) THEN COALESCE(done_at, now()) ELSE NULL END")
        params["d"] = bool(done)
    if not sets:
        return await get_item(session, list_id, item_id)
    res = await session.execute(
        text(f"UPDATE list_items SET {', '.join(sets)}, updated_at = now() "
             "WHERE list_id = :l AND id = :id"),
        params,
    )
    if (res.rowcount or 0) == 0:
        return None
    await _touch_list(session, list_id)
    await notify(session, "item_updated")
    return await get_item(session, list_id, item_id)


async def delete_item(session: AsyncSession, list_id: int, item_id: int) -> bool:
    res = await session.execute(
        text("DELETE FROM list_items WHERE list_id = :l AND id = :id"), {"l": list_id, "id": item_id}
    )
    if (res.rowcount or 0) == 0:
        return False
    await _touch_list(session, list_id)
    await notify(session, "item_deleted")
    return True


async def reorder_items(session: AsyncSession, list_id: int, item_ids: list[int]) -> list[ItemRow]:
    """Put ``item_ids`` first, in that order; the list's other items follow
    in the order they had (an item added meanwhile is not lost). An id that
    isn't on this list is refused."""
    current = await items_of(session, list_id)
    known = {i.id for i in current}
    seen: set[int] = set()
    ordered: list[int] = []
    for i in item_ids:
        if i not in known:
            raise ListError("unknown_item", f"item {i} is not on this list")
        if i not in seen:
            seen.add(i)
            ordered.append(i)
    ordered += [i.id for i in current if i.id not in seen]
    for pos, i in enumerate(ordered, start=1):
        await session.execute(
            text("UPDATE list_items SET position = :p WHERE id = :id AND position <> :p"),
            {"p": pos, "id": i},
        )
    await _touch_list(session, list_id)
    await notify(session, "items_reordered")
    return await items_of(session, list_id)


async def clear_items(session: AsyncSession, list_id: int, *, done_only: bool) -> int:
    """Delete the list's ticked-off items (``done_only``) or all of them;
    how many went."""
    where = "list_id = :l" + (" AND done" if done_only else "")
    res = await session.execute(text(f"DELETE FROM list_items WHERE {where}"), {"l": list_id})
    n = int(res.rowcount or 0)
    if n:
        await _touch_list(session, list_id)
        await notify(session, "items_cleared")
    return n


def _singular(key: str) -> str:
    """Each word of an item key in a rough singular ("batteries" →
    "battery", "boxes" → "box", "eggs" → "egg"): for matching only."""
    out = []
    for w in key.split():
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith(("ches", "shes", "sses", "xes", "zes")):
            w = w[:-2]
        elif len(w) > 2 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return " ".join(out)


def find_items(items: list[ItemRow], spoken: str) -> list[ItemRow]:
    """The items ``spoken`` names: the same words first; then the same
    words in the singular ("battery" for "batteries"); else a close
    spelling (a mis-heard letter). Best first; open items before ticked-off
    ones on a tie."""
    key = item_key(spoken)
    if not key:
        return []
    exact = [i for i in items if item_key(i.text) == key]
    if exact:
        return sorted(exact, key=lambda i: i.done)
    one = _singular(key)
    same = [i for i in items if _singular(item_key(i.text)) == one]
    if same:
        return sorted(same, key=lambda i: i.done)
    scored: list[tuple[float, ItemRow]] = []
    for i in items:
        k = item_key(i.text)
        ratio = difflib.SequenceMatcher(None, key, k).ratio()
        if ratio >= 0.85:
            scored.append((ratio, i))
    scored.sort(key=lambda p: (-p[0], p[1].done))
    return [i for _, i in scored]


__all__ = [
    "AddOutcome", "ItemRow", "LISTS_CHANGED_CHANNEL", "ListError", "ListRow",
    "MAX_ITEMS_PER_LIST", "MAX_LISTS", "NAME_MAX", "TEXT_MAX",
    "add_item", "all_lists", "clean_name", "clear_items", "create_list", "delete_item",
    "delete_list", "find_items", "find_list", "get_item", "get_list", "item_key",
    "items_of", "name_key", "notify", "rename_list", "reorder_items", "spoken_name",
    "update_item", "valid_name",
]
