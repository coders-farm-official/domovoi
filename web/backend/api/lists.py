"""Lists API — the household's lists (V048) for the dashboard and the apps.

The same lists voice keeps (``domovoi/handlers/lists.py``): every read and
write goes through ``domovoi/lists.py``, so a list behaves the same however
it is changed, and every write queues ``pg_notify('lists_changed')`` in its
own transaction. The web's LISTEN task turns that into a ``lists.changed``
event on ``/ws/state`` (``web/backend/realtime.py``), so an item said to a
room shows on an open Lists page within a moment.

Auth tiers, like the calendar (household data, owner decision 2026-10-08):
reading takes the READ half of the device tier (``require_device_read``:
the household token, an admin Bearer, the dashboard cookie, with the
pre-setup grace); every write takes the device tier (``require_device``:
the household token or an admin Bearer). Deleting a whole list is a
household action too, like deleting a playlist; the dashboard and the apps
ask before they send it.

Routes (all under ``/api/lists``):

* ``GET    /api/lists``                               every list, with counts
* ``POST   /api/lists``                               make one ``{name}``
* ``GET    /api/lists/{list_id}``                     one list and its items
* ``PATCH  /api/lists/{list_id}``                     rename ``{name}``
* ``DELETE /api/lists/{list_id}``                     delete it and its items
* ``POST   /api/lists/{list_id}/items``               add ``{text}``
* ``PATCH  /api/lists/{list_id}/items/{item_id}``     ``{text?, done?}``
* ``DELETE /api/lists/{list_id}/items/{item_id}``     delete one item
* ``PUT    /api/lists/{list_id}/items/order``         ``{item_ids: [...]}``
* ``POST   /api/lists/{list_id}/clear-done``          delete the crossed-off items

Refusals: 404 for a list or item that isn't there (``detail`` a sentence);
422 for a name or text that is empty, too long or holds control characters;
409 with ``detail: {code, message}`` for ``list_name_taken`` (another list
already answers to that name — "Groceries" beside "Shopping"),
``too_many_lists`` and ``list_full``; 400 for an order naming an item that
isn't on the list.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from domovoi import lists as L
from domovoi.admin_auth import require_device, require_device_read
from web.backend.db import session_scope
from web.backend.schemas import (
    HouseholdList,
    HouseholdListDetail,
    HouseholdListItem,
    ListCreate,
    ListItemAdded,
    ListItemCreate,
    ListItemOrder,
    ListItemPatch,
    ListPatch,
    ListsCleared,
)

router = APIRouter(prefix="/api/lists", tags=["lists"])

DEVICE = [Depends(require_device)]
READ = [Depends(require_device_read)]


def _refuse(e: L.ListError) -> HTTPException:
    """The answer to a refused write. The three conflicts a client can act
    on carry a code (each has a help entry: domovoi/help_desk/error_codes.py)."""
    if e.code == "name_taken":
        return HTTPException(status_code=409, detail={"code": "list_name_taken", "message": e.message})
    if e.code == "too_many_lists":
        return HTTPException(status_code=409, detail={"code": "too_many_lists", "message": e.message})
    if e.code == "list_full":
        return HTTPException(status_code=409, detail={"code": "list_full", "message": e.message})
    if e.code == "unknown_item":
        return HTTPException(status_code=400, detail=e.message)
    return HTTPException(status_code=422, detail=e.message)


def _list(row: L.ListRow) -> HouseholdList:
    return HouseholdList(**row.to_dict())


def _item(row: L.ItemRow) -> HouseholdListItem:
    return HouseholdListItem(**row.to_dict())


async def _need_list(s: Any, list_id: int) -> L.ListRow:
    row = await L.get_list(s, list_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"list {list_id} not found")
    return row


@router.get("", response_model=list[HouseholdList], dependencies=READ)
async def all_lists() -> list[HouseholdList]:
    """Every list, in the household's order, with its open and done counts."""
    async with session_scope() as s:
        return [_list(r) for r in await L.all_lists(s)]


@router.post("", response_model=HouseholdList, status_code=201, dependencies=DEVICE)
async def create_list(payload: ListCreate) -> HouseholdList:
    async with session_scope() as s:
        try:
            return _list(await L.create_list(s, payload.name))
        except L.ListError as e:
            raise _refuse(e) from None


@router.get("/{list_id}", response_model=HouseholdListDetail, dependencies=READ)
async def get_list(list_id: int) -> HouseholdListDetail:
    """One list and all its items (open and crossed off), in list order."""
    async with session_scope() as s:
        row = await _need_list(s, list_id)
        items = await L.items_of(s, list_id)
    return HouseholdListDetail(list=_list(row), items=[_item(i) for i in items])


@router.patch("/{list_id}", response_model=HouseholdList, dependencies=DEVICE)
async def rename_list(list_id: int, payload: ListPatch) -> HouseholdList:
    async with session_scope() as s:
        try:
            row = await L.rename_list(s, list_id, payload.name)
        except L.ListError as e:
            raise _refuse(e) from None
    if row is None:
        raise HTTPException(status_code=404, detail=f"list {list_id} not found")
    return _list(row)


@router.delete("/{list_id}", status_code=204, dependencies=DEVICE)
async def delete_list(list_id: int) -> None:
    async with session_scope() as s:
        gone = await L.delete_list(s, list_id)
    if not gone:
        raise HTTPException(status_code=404, detail=f"list {list_id} not found")


@router.post("/{list_id}/items", response_model=ListItemAdded, status_code=201, dependencies=DEVICE)
async def add_item(list_id: int, payload: ListItemCreate) -> ListItemAdded:
    """Add an item at the end. ``outcome`` says what happened: ``added``,
    ``reopened`` (the same item was crossed off; it is open again, at the
    end) or ``already`` (it is already open on the list; nothing changed)."""
    async with session_scope() as s:
        await _need_list(s, list_id)
        try:
            item, outcome = await L.add_item(s, list_id, payload.text, via="screen")
        except L.ListError as e:
            raise _refuse(e) from None
    return ListItemAdded(item=_item(item), outcome=outcome)


@router.patch("/{list_id}/items/{item_id}", response_model=HouseholdListItem, dependencies=DEVICE)
async def patch_item(list_id: int, item_id: int, payload: ListItemPatch) -> HouseholdListItem:
    """Change an item's text, or cross it off (``done: true``) or back on."""
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="no fields provided")
    async with session_scope() as s:
        try:
            row = await L.update_item(s, list_id, item_id, item_text=fields.get("text"), done=fields.get("done"))
        except L.ListError as e:
            raise _refuse(e) from None
    if row is None:
        raise HTTPException(status_code=404, detail=f"item {item_id} not found on list {list_id}")
    return _item(row)


@router.delete("/{list_id}/items/{item_id}", status_code=204, dependencies=DEVICE)
async def delete_item(list_id: int, item_id: int) -> None:
    async with session_scope() as s:
        gone = await L.delete_item(s, list_id, item_id)
    if not gone:
        raise HTTPException(status_code=404, detail=f"item {item_id} not found on list {list_id}")


@router.put("/{list_id}/items/order", response_model=list[HouseholdListItem], dependencies=DEVICE)
async def order_items(list_id: int, payload: ListItemOrder) -> list[HouseholdListItem]:
    """Put ``item_ids`` first, in that order; any item not named (one added
    meanwhile, by voice say) keeps its place after them. Answers the whole
    list in its new order."""
    async with session_scope() as s:
        await _need_list(s, list_id)
        try:
            items = await L.reorder_items(s, list_id, payload.item_ids)
        except L.ListError as e:
            raise _refuse(e) from None
    return [_item(i) for i in items]


@router.post("/{list_id}/clear-done", response_model=ListsCleared, dependencies=DEVICE)
async def clear_done(list_id: int) -> ListsCleared:
    """Delete the list's crossed-off items; how many went."""
    async with session_scope() as s:
        await _need_list(s, list_id)
        removed = await L.clear_items(s, list_id, done_only=True)
    return ListsCleared(removed=removed)


async def snapshot() -> dict[str, Any]:
    """The ``lists`` realtime channel's value: a digest that moves on every
    list or item write (counts, id sums, the newest ``updated_at``), never a
    name or an item. An open Lists page reads again on ``lists.changed``."""
    from sqlalchemy import text

    async with session_scope() as s:
        row = (await s.execute(text(
            """
            SELECT (SELECT COUNT(*) FROM lists), (SELECT COALESCE(SUM(id), 0) FROM lists),
                   (SELECT MAX(updated_at) FROM lists),
                   (SELECT COUNT(*) FROM list_items), (SELECT COALESCE(SUM(id), 0) FROM list_items),
                   (SELECT COUNT(*) FROM list_items WHERE done),
                   (SELECT MAX(updated_at) FROM list_items)
            """
        ))).one()
    return {
        "lists": int(row[0]), "list_ids": int(row[1]),
        "lists_at": row[2].isoformat() if row[2] else None,
        "items": int(row[3]), "item_ids": int(row[4]), "done": int(row[5]),
        "items_at": row[6].isoformat() if row[6] else None,
    }
