"""Voice denylist — opt-out registry.

Each row is one anonymous embedding the matcher should suppress. Rows
intentionally have no person FK; a denylisted user has explicitly
declined to be a ``people`` row. The web UI surfaces this as an admin
page where the user can review and remove entries (e.g. after a guest
asked to be re-prompted on a future visit).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from web.backend.db import session_scope
from web.backend.schemas import DenylistEntry

router = APIRouter(prefix="/api/denylist", tags=["denylist"])


@router.get("", response_model=list[DenylistEntry])
async def list_denylist() -> list[DenylistEntry]:
    async with session_scope() as s:
        rows = await s.execute(
            text(
                """
                SELECT id, denylisted_at, notes
                FROM voice_denylist
                ORDER BY denylisted_at DESC
                """
            )
        )
        return [
            DenylistEntry(id=int(r[0]), denylisted_at=r[1], notes=r[2])
            for r in rows.all()
        ]


@router.delete("/{entry_id}", status_code=204)
async def delete_denylist_entry(entry_id: int) -> None:
    async with session_scope() as s:
        result = await s.execute(
            text("DELETE FROM voice_denylist WHERE id = :id"), {"id": entry_id}
        )
    if (result.rowcount or 0) == 0:
        raise HTTPException(
            status_code=404, detail=f"denylist entry {entry_id} not found"
        )
