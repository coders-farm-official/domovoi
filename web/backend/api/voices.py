"""Voices API — the TTS voice registry (voices).

Each satellite speaks in one registered voice; the Domovoi server
synthesizes that room's responses and renders its greeting/canned clips in
it. This page manages the registry: list voices, upload a local Piper
model (``.onnx`` + ``.onnx.json``), set the default, rename, and delete.
(The Microsoft Edge cloud voices and their registration were dropped on
2026-10-12; the core removes old Edge rows at boot.)

On any mutation we ping the Domovoi server's ``/v1/admin/sounds/regenerate``
so the new/changed voice's clips render and reach satellites — best-effort,
so a DB edit still persists (rendering on the next server boot) if
the Domovoi server is unreachable.

Uploaded Piper models land in ``settings.voice_models_dir`` (the same dir
``tts.py`` resolves voices from), named ``<slug>.onnx`` / ``<slug>.onnx.json``
where ``slug`` is the filesystem-safe form of the voice name; the row's
``model_ref`` is that slug so the synth path finds the model.

Domovoi speaks a Piper voice trained on espeak's English phonemes, British
``"en"`` or American ``"en-us"`` (what most voices people train use). A
voice trained on any other set is still kept, but can't speak here: rooms
set to it fall back to the system voice. Each row says so (``problem``,
in words for the owner, also in the upload's answer), and its play button
is hidden (``sample`` is null): a button plays the voice it names, or a
shipped voice's built-in clip before it is downloaded, or is not shown.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from domovoi.admin_auth import require_admin_mutation
from domovoi.canned_sounds import voice_slug
from domovoi.config import settings
from domovoi.db.repositories import VoicesRepository
from domovoi.voice_sample import piper_sample_source
from web.backend.api.voice import clip_response
from web.backend.db import session_scope
from web.backend.domovoi_client import (
    auth_forward_headers,
    core_audio_refusal,
    domovoi_url,
    post_admin,
    post_admin_bytes,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voices", tags=["voices"])

# Cap an uploaded Piper model at a sane size. Piper voices are ~20–80 MB
# (medium quality ~60 MB); 200 MB leaves headroom for high quality without
# letting an accidental wrong-file upload fill the disk.
_MAX_ONNX_BYTES = 200 * 1024 * 1024

# The sidecar config is a small JSON document (phoneme map, sample rate,
# speaker ids) — a few KB in practice. 4 MB is generous and keeps a file
# picked by mistake from being read into memory whole.
_MAX_CONFIG_BYTES = 4 * 1024 * 1024

# Both uploads are streamed to disk in blocks of this size, so the process
# holds one block at a time rather than the whole model.
_UPLOAD_CHUNK_BYTES = 1024 * 1024


async def _save_under_budget(upload: UploadFile, dest: Path, budget: int, what: str) -> int:
    """Stream ``upload`` into ``dest``, stopping with 413 the moment it
    passes ``budget``. Returns the byte count.

    The partial file is removed on refusal (and on any write error), so a
    rejected upload leaves nothing behind. The caller is also fronted by
    ``BodyLimitMiddleware``, which refuses a declared oversize before the
    request body is read at all; this is the check that holds when the
    size is not declared up front.
    """
    written = 0
    try:
        with dest.open("wb") as fh:
            while True:
                chunk = await upload.read(_UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > budget:
                    raise HTTPException(
                        status_code=413,
                        detail=f"{what} too large (limit {budget} bytes)",
                    )
                fh.write(chunk)
    except OSError as e:
        # Disk full, permission denied, path gone: the operator needs to
        # read which one, not a bare 500.
        dest.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500, detail=f"could not save {what}: {e}"
        ) from e
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return written


class Voice(BaseModel):
    id: int
    name: str
    engine: str
    model_ref: str
    is_default: bool
    # What the play button plays: "voice" (synthesized live), "clip" (a
    # shipped voice's built-in clip, before it is downloaded) or None (no
    # button: nothing that is really this voice).
    sample: str | None = None
    # Why a Piper voice can't speak here (rooms set to it use the system
    # voice), in words for the owner; None when it can.
    problem: str | None = None


class VoicePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    set_default: bool | None = None


def _voices_dir() -> Path:
    return Path(settings.voice_models_dir)


async def _trigger_rerender(request: Request) -> None:
    """Best-effort: ask the Domovoi server to (background-) re-render clips and
    push them to satellites. Non-fatal — but surface an unreachable
    domovoi in the log so a missing render isn't silent (the DB change
    persists and renders on the Domovoi server's next restart regardless).

    The core route is admin-gated, so this carries the credentials of the
    admin whose edit triggered the re-render."""
    status, _ = await post_admin(
        "/v1/admin/sounds/regenerate", headers=auth_forward_headers(request)
    )
    if status == 0:
        log.warning(
            "clip re-render skipped: domovoi unreachable at %s — new clips "
            "will render on its next restart",
            domovoi_url(),
        )


def _speakability(engine: str, model_ref: str) -> tuple[str | None, str | None]:
    """(sample, problem) for a registry row; reads a Piper voice's config
    from disk, never its model. A Microsoft Edge row left from an older
    release (the core removes them at its next start) plays nothing."""
    if engine == "edge":
        return None, "a Microsoft Edge voice: Domovoi no longer speaks those"
    if engine != "piper":
        return "voice", None
    from domovoi.clients.tts import piper_voice_problem

    return piper_sample_source(model_ref), piper_voice_problem(model_ref)


def _to_model(row: dict) -> Voice:
    sample, problem = _speakability(row["engine"], row["model_ref"])
    return Voice(
        id=row["id"], name=row["name"], engine=row["engine"],
        model_ref=row["model_ref"], is_default=row["is_default"],
        sample=sample, problem=problem,
    )


@router.get("", response_model=list[Voice])
async def list_voices() -> list[Voice]:
    async with session_scope() as s:
        rows = await VoicesRepository(s).all()
    return [_to_model(r) for r in rows]


@router.get("/{voice_id}/sample")
async def sample_voice(voice_id: int, request: Request) -> Response:
    """Stream a freshly-synthesized sample (intro + random fun fact) for a
    voice, for the play button. Proxies the Domovoi server's TTS — the web
    process has none of its own. 502 if the Domovoi server is unreachable;
    a refusal from it (409: the voice can't speak right now, and why)
    passes through with its own sentence.
    A shipped voice not downloaded yet plays its built-in clip instead; a
    Piper voice that can't speak here is 409 with the reason (its button
    is hidden: the system voice would answer, not this one)."""
    async with session_scope() as s:
        rows = await VoicesRepository(s).all()
    voice = next((v for v in rows if v["id"] == voice_id), None)
    if voice is None:
        raise HTTPException(status_code=404, detail=f"voice {voice_id} not found")
    if voice["engine"] == "piper":
        sample, problem = _speakability("piper", voice["model_ref"])
        if sample == "clip":
            return clip_response(voice["model_ref"])
        if sample is None:
            raise HTTPException(
                status_code=409,
                detail=f"{voice['name']} can't speak here: {problem}" if problem
                else f"{voice['name']} is not on this server",
            )
    elif voice["engine"] == "edge":
        raise HTTPException(status_code=409, detail=f"{voice['name']} is a Microsoft Edge voice: "
                            "Domovoi no longer speaks those")

    status, audio, headers = await post_admin_bytes(
        "/v1/admin/voices/sample",
        {"name": voice["name"]},
        headers=auth_forward_headers(request),
        # A catalogue voice not on the box yet downloads on its first
        # sample (Cori is 114 MB): the shipped voices' route waits as long.
        timeout=120.0,
    )
    if status != 200 or not audio:
        # The core's own sentence ("Cori can't speak right now: ..."), not
        # a bare "502 Bad Gateway".
        raise core_audio_refusal(status, audio, f"{voice['name']}'s sample could not be made")
    # Forward the spoken text so the UI can show what it's saying.
    out_headers = {}
    if headers.get("x-sample-text"):
        out_headers["X-Sample-Text"] = headers["x-sample-text"]
    return Response(
        content=audio,
        media_type=headers.get("content-type", "audio/wav"),
        headers=out_headers,
    )


@router.post(
    "/piper", response_model=Voice, status_code=201,
    dependencies=[Depends(require_admin_mutation)],
)
async def upload_piper_voice(
    request: Request,
    name: str = Form(..., min_length=1, max_length=80),
    set_default: bool = Form(False),
    onnx: UploadFile = File(...),
    config: UploadFile = File(...),
) -> Voice:
    """Upload a Piper voice: the ``.onnx`` model and its ``.onnx.json``
    config. Saved under the voice-models dir as ``<slug>.onnx`` /
    ``<slug>.onnx.json``; the row's ``model_ref`` is the slug.

    Admin-gated, and both files are streamed to disk under a byte budget
    (model ``_MAX_ONNX_BYTES``, config ``_MAX_CONFIG_BYTES``) rather than
    read into memory — an oversized upload is answered 413 while it is
    still arriving."""
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
    # Staged names, so a refused upload (or a name collision) never leaves a
    # half-written model where the synth path would find it.
    onnx_part = vdir / f"{slug}.onnx.part"
    json_part = vdir / f"{slug}.onnx.json.part"

    await _save_under_budget(onnx, onnx_part, _MAX_ONNX_BYTES, "model file")
    try:
        await _save_under_budget(config, json_part, _MAX_CONFIG_BYTES, "config file")
    except BaseException:
        onnx_part.unlink(missing_ok=True)
        raise

    # Then the DB row (so a name collision fails before anything lands under
    # its final name), and only after that do the staged files take it.
    try:
        async with session_scope() as s:
            repo = VoicesRepository(s)
            new_id = await repo.create(
                name=name, engine="piper", model_ref=slug, is_default=set_default,
            )
    except IntegrityError as e:
        onnx_part.unlink(missing_ok=True)
        json_part.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="a voice with that name already exists") from e
    except BaseException:
        onnx_part.unlink(missing_ok=True)
        json_part.unlink(missing_ok=True)
        raise

    try:
        onnx_part.replace(onnx_path)
        json_part.replace(json_path)
    except OSError as e:
        onnx_part.unlink(missing_ok=True)
        json_part.unlink(missing_ok=True)
        # Roll back the row we just created so the registry doesn't point at
        # a model that isn't on disk.
        async with session_scope() as s:
            await VoicesRepository(s).delete(new_id)
        raise HTTPException(status_code=500, detail=f"could not save model: {e}") from e

    await _trigger_rerender(request)
    sample, problem = _speakability("piper", slug)
    if problem:
        log.info("uploaded voice %r can't speak here: %s", name, problem)
    return Voice(id=new_id, name=name, engine="piper", model_ref=slug, is_default=set_default,
                 sample=sample, problem=problem)


@router.patch("/{voice_id}", response_model=Voice, dependencies=[Depends(require_admin_mutation)])
async def patch_voice(voice_id: int, payload: VoicePatch, request: Request) -> Voice:
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
            await _trigger_rerender(request)
            return _to_model(r)
    raise HTTPException(status_code=404, detail=f"voice {voice_id} not found")


@router.delete("/{voice_id}", status_code=204, dependencies=[Depends(require_admin_mutation)])
async def delete_voice(voice_id: int, request: Request) -> None:
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
    await _trigger_rerender(request)
