"""The Now Playing visualizer's added presets (MilkDrop, run by Butterchurn
in the browser).

The standard set ships with every install as static files
(``web/static/visualizer/presets/``, listed by its ``index.json``); this
module keeps the presets an admin adds for the whole house, so every
browser gets them. The rules (what a preset may hold, the ``.milk``
reader, the store) are in :mod:`web.backend.visualizer_presets`.

Routes:

* ``GET /api/visualizer/presets`` (household read): ``{presets, limits}``,
  the added presets' summaries (id, name, source, added_at, notes).
* ``GET /api/visualizer/presets/{preset_id}`` (household read): one preset,
  ``{id, name, preset}``.
* ``POST /api/visualizer/presets`` (admin): ``{filename, content}``, the
  file's text; ``.milk`` is converted here, Butterchurn ``.json`` checked.
  201 with the summary; 400 with the reason in words when it is refused,
  409 when it is already there.
* ``DELETE /api/visualizer/presets/{preset_id}`` (admin).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from domovoi.admin_auth import require_admin_mutation, require_device_read
from web.backend import visualizer_presets as vp

router = APIRouter(prefix="/api/visualizer", tags=["visualizer"])

DAILY = [Depends(require_device_read)]
ADMIN = [Depends(require_admin_mutation)]


class PresetUpload(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    # Characters, not bytes: the byte cap is checked after (convert_upload).
    content: str = Field(..., min_length=1, max_length=vp.MAX_UPLOAD_BYTES)


def _bad(e: vp.PresetError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


@router.get("/presets", dependencies=DAILY)
async def list_presets() -> dict[str, Any]:
    presets = await asyncio.to_thread(vp.list_added)
    return {"presets": presets, "limits": vp.LIMITS}


@router.get("/presets/{preset_id}", dependencies=DAILY)
async def get_preset(preset_id: str) -> dict[str, Any]:
    if not vp.valid_id(preset_id):
        raise HTTPException(status_code=404, detail="no such preset")
    doc = await asyncio.to_thread(vp.read_added, preset_id)
    if not doc:
        raise HTTPException(status_code=404, detail="no such preset")
    return {"id": doc["id"], "name": vp.summary(doc)["name"], "preset": doc["preset"]}


@router.post("/presets", dependencies=ADMIN, status_code=201)
async def add_preset(body: PresetUpload) -> dict[str, Any]:
    try:
        name, source, preset, notes = vp.convert_upload(body.filename, body.content)
    except vp.PresetError as e:
        raise _bad(e) from e
    try:
        return await asyncio.to_thread(vp.save_added, name, source, preset, notes)
    except vp.PresetError as e:
        status = 409 if "already" in str(e) else 400
        raise HTTPException(status_code=status, detail=str(e)) from e


@router.delete("/presets/{preset_id}", dependencies=ADMIN, status_code=204)
async def delete_preset(preset_id: str) -> Response:
    if not vp.valid_id(preset_id):
        raise HTTPException(status_code=404, detail="no such preset")
    if not await asyncio.to_thread(vp.delete_added, preset_id):
        raise HTTPException(status_code=404, detail="no such preset")
    return Response(status_code=204)
