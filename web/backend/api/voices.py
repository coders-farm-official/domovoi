"""Voices API — the TTS voice registry (voices).

Each satellite speaks in one registered voice; the Domovoi server
synthesizes that room's responses and renders its greeting/canned clips in
it. This page manages the registry: list voices, register a cloud (Edge)
voice by id, upload a local Piper model (``.onnx`` + ``.onnx.json``), set
the default, rename, and delete.

On any mutation we ping the Domovoi server's ``/v1/admin/sounds/regenerate``
so the new/changed voice's clips render and reach satellites — best-effort,
so a DB edit still persists (rendering on the next server boot) if
the Domovoi server is unreachable.

Uploaded Piper models land in ``settings.voice_models_dir`` (the same dir
``tts.py`` resolves voices from), named ``<slug>.onnx`` / ``<slug>.onnx.json``
where ``slug`` is the filesystem-safe form of the voice name; the row's
``model_ref`` is that slug so the synth path finds the model.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from domovoi.canned_sounds import voice_slug
from domovoi.config import settings
from domovoi.db.repositories import VoicesRepository
from web.backend.db import session_scope
from web.backend.domovoi_client import domovoi_url, post_admin, post_admin_bytes

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voices", tags=["voices"])

# Cap an uploaded Piper model at a sane size. Piper voices are ~20–80 MB
# (medium quality ~60 MB); 200 MB leaves headroom for high quality without
# letting an accidental wrong-file upload fill the disk.
_MAX_ONNX_BYTES = 200 * 1024 * 1024


class Voice(BaseModel):
    id: int
    name: str
    engine: str
    model_ref: str
    is_default: bool


class EdgeVoiceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    voice_id: str = Field(..., min_length=1, max_length=120)
    set_default: bool = False


class VoicePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    set_default: bool | None = None


def _voices_dir() -> Path:
    return Path(settings.voice_models_dir)


async def _trigger_rerender() -> None:
    """Best-effort: ask the Domovoi server to (background-) re-render clips and
    push them to satellites. Non-fatal — but surface an unreachable
    domovoi in the log so a missing render isn't silent (the DB change
    persists and renders on the Domovoi server's next restart regardless)."""
    status, _ = await post_admin("/v1/admin/sounds/regenerate")
    if status == 0:
        log.warning(
            "clip re-render skipped: domovoi unreachable at %s — new clips "
            "will render on its next restart",
            domovoi_url(),
        )


def _to_model(row: dict) -> Voice:
    return Voice(
        id=row["id"], name=row["name"], engine=row["engine"],
        model_ref=row["model_ref"], is_default=row["is_default"],
    )


@router.get("", response_model=list[Voice])
async def list_voices() -> list[Voice]:
    async with session_scope() as s:
        rows = await VoicesRepository(s).all()
    return [_to_model(r) for r in rows]


@router.get("/{voice_id}/sample")
async def sample_voice(voice_id: int) -> Response:
    """Stream a freshly-synthesized sample (intro + random fun fact) for a
    voice, for the play button. Proxies the Domovoi server's TTS — the web
    process has none of its own. 502 if the Domovoi server is unreachable."""
    async with session_scope() as s:
        rows = await VoicesRepository(s).all()
    voice = next((v for v in rows if v["id"] == voice_id), None)
    if voice is None:
        raise HTTPException(status_code=404, detail=f"voice {voice_id} not found")

    status, audio, headers = await post_admin_bytes(
        "/v1/admin/voices/sample", {"name": voice["name"]}
    )
    if status == 0:
        raise HTTPException(status_code=502, detail="domovoi unreachable")
    if status != 200 or not audio:
        raise HTTPException(status_code=502, detail="voice sample synthesis failed")
    # Forward the spoken text so the UI can show what it's saying.
    out_headers = {}
    if headers.get("x-sample-text"):
        out_headers["X-Sample-Text"] = headers["x-sample-text"]
    return Response(
        content=audio,
        media_type=headers.get("content-type", "audio/wav"),
        headers=out_headers,
    )


@router.post("/edge", response_model=Voice, status_code=201)
async def register_edge_voice(payload: EdgeVoiceCreate) -> Voice:
    """Register a Microsoft Edge cloud voice by its voice id (e.g.
    ``en-US-AriaNeural``). No file — the engine downloads on demand."""
    name = payload.name.strip()
    voice_id = payload.voice_id.strip()
    try:
        async with session_scope() as s:
            repo = VoicesRepository(s)
            new_id = await repo.create(
                name=name, engine="edge", model_ref=voice_id,
                is_default=payload.set_default,
            )
            row = await repo.get_by_name(name)
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="a voice with that name already exists") from e
    await _trigger_rerender()
    return _to_model(row or {"id": new_id, "name": name, "engine": "edge",
                             "model_ref": voice_id, "is_default": payload.set_default})


@router.post("/piper", response_model=Voice, status_code=201)
async def upload_piper_voice(
    name: str = Form(..., min_length=1, max_length=80),
    set_default: bool = Form(False),
    onnx: UploadFile = File(...),
    config: UploadFile = File(...),
) -> Voice:
    """Upload a Piper voice: the ``.onnx`` model and its ``.onnx.json``
    config. Saved under the voice-models dir as ``<slug>.onnx`` /
    ``<slug>.onnx.json``; the row's ``model_ref`` is the slug."""
    name = name.strip()
    slug = voice_slug(name)
    if not (onnx.filename or "").endswith(".onnx"):
        raise HTTPException(status_code=400, detail="model file must be a .onnx")
    if not (config.filename or "").endswith(".json"):
        raise HTTPException(status_code=400, detail="config file must be a .onnx.json")

    vdir = _voices_dir()
    vdir.mkdir(parents=True, exist_ok=True)
    onnx_path = vdir / f"{slug}.onnx"
    json_path = vdir / f"{slug}.onnx.json"

    onnx_bytes = await onnx.read()
    if len(onnx_bytes) > _MAX_ONNX_BYTES:
        raise HTTPException(status_code=413, detail="model file too large")
    json_bytes = await config.read()

    # Write to a clean DB row first (so a name collision fails BEFORE we
    # litter the disk), then drop the files. Reverse cleanup on file error.
    try:
        async with session_scope() as s:
            repo = VoicesRepository(s)
            new_id = await repo.create(
                name=name, engine="piper", model_ref=slug, is_default=set_default,
            )
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="a voice with that name already exists") from e

    try:
        onnx_path.write_bytes(onnx_bytes)
        json_path.write_bytes(json_bytes)
    except OSError as e:
        # Roll back the row we just created so the registry doesn't point at
        # a model that isn't on disk.
        async with session_scope() as s:
            await VoicesRepository(s).delete(new_id)
        raise HTTPException(status_code=500, detail=f"could not save model: {e}") from e

    await _trigger_rerender()
    return Voice(id=new_id, name=name, engine="piper", model_ref=slug, is_default=set_default)


@router.patch("/{voice_id}", response_model=Voice)
async def patch_voice(voice_id: int, payload: VoicePatch) -> Voice:
    if payload.name is None and not payload.set_default:
        raise HTTPException(status_code=400, detail="no fields to patch")
    try:
        async with session_scope() as s:
            repo = VoicesRepository(s)
            if payload.name is not None:
                if not await repo.update(voice_id, name=payload.name.strip()):
                    raise HTTPException(status_code=404, detail=f"voice {voice_id} not found")
            if payload.set_default:
                if not await repo.set_default(voice_id):
                    raise HTTPException(status_code=404, detail=f"voice {voice_id} not found")
            rows = await repo.all()
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="a voice with that name already exists") from e
    for r in rows:
        if r["id"] == voice_id:
            await _trigger_rerender()
            return _to_model(r)
    raise HTTPException(status_code=404, detail=f"voice {voice_id} not found")


@router.delete("/{voice_id}", status_code=204)
async def delete_voice(voice_id: int) -> None:
    async with session_scope() as s:
        removed = await VoicesRepository(s).delete(voice_id)
    if removed is None:
        # Either not found, or it's the default (which delete refuses).
        raise HTTPException(
            status_code=409,
            detail="voice not found, or it's the default — set another default first",
        )
    # Best-effort cleanup of an uploaded Piper model, path-guarded to the
    # voice-models dir so a crafted model_ref can't delete arbitrary files.
    if removed["engine"] == "piper":
        vdir = _voices_dir().resolve()
        for suffix in (".onnx", ".onnx.json"):
            f = (vdir / f"{removed['model_ref']}{suffix}").resolve()
            try:
                f.relative_to(vdir)
            except ValueError:
                continue
            if f.is_file():
                try:
                    f.unlink()
                except OSError:
                    pass
    await _trigger_rerender()
