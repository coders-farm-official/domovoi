"""Greetings API — the wake-word greeting bank (client_greetings).

These are the short lines a satellite plays the instant the wake word
fires. The Domovoi server renders each *enabled* row to an MP3 in the
configured TTS voice; the satellites sync the clips. On any mutation here
we ping the Domovoi server's ``/v1/admin/sounds/regenerate`` so the change
re-renders and pushes live to connected satellites — best-effort, so a DB
edit still persists (and renders on the next restart) if the
Domovoi server is unreachable.

``{name}`` in a greeting is substituted with the configured bot name at
render time, so name greetings track BOT_NAME.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from domovoi.db.repositories import ClientGreetingsRepository
from web.backend.db import session_scope
from web.backend.domovoi_client import post_admin

router = APIRouter(prefix="/api/greetings", tags=["greetings"])

_CATEGORIES = ("generic", "funny")


class Greeting(BaseModel):
    id: int
    text: str
    category: str
    enabled: bool


class GreetingCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=200)
    category: str = "generic"


class GreetingPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = None
    enabled: bool | None = None


def _check_category(category: str) -> str:
    c = category.strip().lower()
    if c not in _CATEGORIES:
        raise HTTPException(
            status_code=400, detail=f"category must be one of {list(_CATEGORIES)}"
        )
    return c


async def _trigger_rerender() -> None:
    """Best-effort re-render + live push to satellites. Non-fatal: a failure
    leaves the DB change in place to render on the next server boot."""
    await post_admin("/v1/admin/sounds/regenerate")


@router.get("", response_model=list[Greeting])
async def list_greetings() -> list[Greeting]:
    async with session_scope() as s:
        rows = await ClientGreetingsRepository(s).list_all()
    return [Greeting(id=i, text=t, category=c, enabled=e) for i, t, c, e in rows]


@router.post("", response_model=Greeting, status_code=201)
async def create_greeting(payload: GreetingCreate) -> Greeting:
    category = _check_category(payload.category)
    body = payload.text.strip()
    if not body:
        raise HTTPException(status_code=400, detail="text is required")
    try:
        async with session_scope() as s:
            new_id = await ClientGreetingsRepository(s).create(
                greeting_text=body, category=category
            )
    except IntegrityError as e:
        raise HTTPException(
            status_code=409, detail="a greeting with that text already exists"
        ) from e
    await _trigger_rerender()
    return Greeting(id=new_id, text=body, category=category, enabled=True)


@router.patch("/{greeting_id}", response_model=Greeting)
async def patch_greeting(greeting_id: int, payload: GreetingPatch) -> Greeting:
    category = _check_category(payload.category) if payload.category is not None else None
    body = payload.text.strip() if payload.text is not None else None
    if body is not None and not body:
        raise HTTPException(status_code=400, detail="text cannot be empty")
    if body is None and category is None and payload.enabled is None:
        raise HTTPException(status_code=400, detail="no fields to patch")
    try:
        async with session_scope() as s:
            repo = ClientGreetingsRepository(s)
            changed = await repo.update(
                greeting_id,
                greeting_text=body,
                category=category,
                enabled=payload.enabled,
            )
            if not changed:
                raise HTTPException(status_code=404, detail=f"greeting {greeting_id} not found")
            rows = await repo.list_all()
    except IntegrityError as e:
        raise HTTPException(
            status_code=409, detail="a greeting with that text already exists"
        ) from e
    await _trigger_rerender()
    for i, t, c, e in rows:
        if i == greeting_id:
            return Greeting(id=i, text=t, category=c, enabled=e)
    raise HTTPException(status_code=404, detail=f"greeting {greeting_id} not found")


@router.delete("/{greeting_id}", status_code=204)
async def delete_greeting(greeting_id: int) -> None:
    async with session_scope() as s:
        deleted = await ClientGreetingsRepository(s).delete(greeting_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"greeting {greeting_id} not found")
    await _trigger_rerender()
