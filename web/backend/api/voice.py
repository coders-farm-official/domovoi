"""Voice API — Settings → Voice, and what the phone reads to sound the same.

Domovoi speaks with one of three public-domain Piper voices, fed by its own
text-to-phoneme converter, and an owner can teach that converter how to say
a word ("Nguyen" = "win"). The phone runs the same converter and voices, so
it reads the server's choice and list here.

  GET    /api/voice                         · the voices, the server's voice, the list (device tier)
  GET    /api/voice/samples                 · what each voice's play button can play (device tier)
  GET    /api/voice/sample/{voice_id}       · a sample in one of the voices (device tier)
  PUT    /api/voice/server                  · {voice}: the server's voice (admin)
  POST   /api/voice/pronunciations          · {word, say_like}: add or change an entry (admin)
  DELETE /api/voice/pronunciations/{word}   · take an entry off the list (admin)
  POST   /api/voice/pronunciations/defaults · put the shipped entries back (admin)
  POST   /api/voice/pronunciations/preview  · {word, say_like, voice?}: hear an entry, unsaved (device tier)

A play button plays the voice it names or is not shown (owner, 2026-10-12):
a voice on this server is synthesized live; one not downloaded yet plays
its built-in clip (``domovoi/voice_clips/``, Ogg Opus, the same files the
phone ships); ``/api/voice/samples`` says which, so the page can hide a
button with neither.

Every write answers with the same body as ``GET /api/voice``, then asks the
core to reload the list (``/v1/admin/pronunciations/reload``) or re-render
the clips (``/v1/admin/sounds/regenerate``). Best-effort: a change saved
while the core is down takes effect at its next start. The data model is
in domovoi/voice_settings.py; the "say it like" format in
domovoi/phonemizer/README.md.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from domovoi.admin_auth import require_admin_mutation, require_device
from domovoi.clients.tts import PIPER_PINS, SHIPPED_VOICES
from domovoi.db.repositories import PronunciationsRepository, VoicesRepository
from domovoi.phonemizer import Phonemizer, PronunciationError, pronunciation_key
from domovoi.voice_sample import CLIP_MEDIA_TYPE, clip_line, piper_sample_source, shipped_clip
from domovoi.voice_settings import (
    MAX_PRONUNCIATIONS,
    default_keys,
    effective_pronunciations,
    owner_words,
    voice_payload,
)
from web.backend.db import session_scope
from web.backend.domovoi_client import (
    auth_forward_headers,
    core_audio_refusal,
    domovoi_url,
    post_admin,
    post_admin_bytes,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])
DEVICE = [Depends(require_device)]
ADMIN = [Depends(require_admin_mutation)]


class VoiceInfo(BaseModel):
    id: str
    name: str
    sex: str
    sample_rate: int
    size_bytes: int
    sha256: str


class PronunciationEntry(BaseModel):
    word: str
    say_like: str


class VoiceSettings(BaseModel):
    voices: list[VoiceInfo]
    server_voice: str
    pronunciations: list[PronunciationEntry]
    updated_at: str


class ServerVoiceBody(BaseModel):
    voice: str = Field(..., min_length=1, max_length=80)


class PronunciationBody(BaseModel):
    word: str = Field(..., min_length=1, max_length=48)
    say_like: str = Field(..., min_length=1, max_length=100)


class PreviewBody(PronunciationBody):
    voice: str | None = Field(default=None, max_length=80)


# The web process checks an entry the way the core will take it, with its
# own converter: the dictionary, no model (a respelling's syllables that are
# in no dictionary get spelled instead of guessed, which can't change
# whether the entry is usable). Built on first use, about half a second.
_checker: Phonemizer | None = None
_checker_lock = threading.Lock()


def _check_entry(word: str, say_like: str) -> None:
    global _checker
    with _checker_lock:
        if _checker is None:
            _checker = Phonemizer(model=False, pronunciations=[])
    _checker.resolve_pronunciation(word, say_like)


async def _payload() -> dict:
    async with session_scope() as s:
        return await voice_payload(s)


async def _reload_core(request: Request) -> None:
    status, _ = await post_admin(
        "/v1/admin/pronunciations/reload", headers=auth_forward_headers(request)
    )
    if status == 0:
        log.warning(
            "pronunciations saved; the core at %s is unreachable and takes them at its next start",
            domovoi_url(),
        )


@router.get("", response_model=VoiceSettings, dependencies=DEVICE)
async def get_voice() -> dict:
    """The three voices, the one the server speaks with, and the effective
    "say it like" list (the shipped entries with the owner's changes).
    ``updated_at`` changes whenever the list or the server's voice does."""
    return await _payload()


class SampleSources(BaseModel):
    samples: dict[str, str | None]


@router.get("/samples", response_model=SampleSources, dependencies=DEVICE)
async def sample_sources() -> dict:
    """What each voice's play button plays: ``"voice"`` (synthesized live,
    the voice is on this server), ``"clip"`` (its built-in clip: not
    downloaded yet) or null (nothing that is really that voice: no
    button)."""
    return {"samples": {v.model_ref: await asyncio.to_thread(piper_sample_source, v.model_ref)
                        for v in SHIPPED_VOICES}}


def clip_response(model_ref: str) -> Response:
    """A shipped voice's built-in clip (Ogg Opus), with what it says."""
    from urllib.parse import quote

    path = shipped_clip(model_ref)
    if path is None:
        raise HTTPException(status_code=409, detail=f"{model_ref} has no sample to play")
    name = next(v.name for v in SHIPPED_VOICES if v.model_ref == model_ref)
    return Response(content=path.read_bytes(), media_type=CLIP_MEDIA_TYPE,
                    headers={"X-Sample-Text": quote(clip_line(name), safe=""), "X-Sample-Source": "clip"})


@router.get("/sample/{voice_id}", dependencies=DEVICE)
async def sample_voice(voice_id: str, request: Request) -> Response:
    """A sample in one of the three voices, for the play buttons beside
    them: synthesized fresh (intro + a fun fact) when the voice is on this
    server, else its built-in clip (``X-Sample-Source`` says which)."""
    if voice_id not in PIPER_PINS:
        raise HTTPException(status_code=404, detail=f"{voice_id!r} is not one of the voices")
    source = await asyncio.to_thread(piper_sample_source, voice_id)
    if source != "voice":
        return clip_response(voice_id)
    async with session_scope() as s:
        rows = await VoicesRepository(s).all()
    row = next((r for r in rows if r["engine"] == "piper" and r["model_ref"] == voice_id), None)
    if row is None:
        raise HTTPException(status_code=409, detail="the voice is not registered yet: restart the core")
    status, audio, headers = await post_admin_bytes(
        "/v1/admin/voices/sample", {"name": row["name"]}, headers=auth_forward_headers(request),
        timeout=120.0,
    )
    if status != 200 or not audio:
        raise core_audio_refusal(status, audio, f"{row['name']}'s sample could not be made")
    out = {"X-Sample-Text": headers["x-sample-text"]} if headers.get("x-sample-text") else {}
    out["X-Sample-Source"] = "voice"
    return Response(content=audio, media_type="audio/wav", headers=out)


@router.put("/server", response_model=VoiceSettings, dependencies=ADMIN)
async def set_server_voice(body: ServerVoiceBody, request: Request) -> dict:
    """Make one of the three voices the server's: the voice registry's
    default, so every room without a voice of its own speaks in it. The
    clips re-render in it in the background."""
    shipped = {v.model_ref: v for v in SHIPPED_VOICES}
    if body.voice not in shipped:
        raise HTTPException(status_code=400, detail=f"{body.voice!r} is not one of the voices")
    async with session_scope() as s:
        repo = VoicesRepository(s)
        rows = await repo.all()
        row = next((r for r in rows if r["engine"] == "piper" and r["model_ref"] == body.voice), None)
        if row is None:
            vid = await repo.create(name=shipped[body.voice].name, engine="piper", model_ref=body.voice)
        else:
            vid = row["id"]
        await repo.set_default(vid)
    status, _ = await post_admin("/v1/admin/sounds/regenerate", headers=auth_forward_headers(request))
    if status == 0:
        log.warning("server voice saved; the core is unreachable and re-renders at its next start")
    return await _payload()


@router.post("/pronunciations", response_model=VoiceSettings, dependencies=ADMIN)
async def put_pronunciation(body: PronunciationBody, request: Request) -> dict:
    """Add a word to the list, or change how a listed word is said. 400 with
    the reason when the converter can't use the entry."""
    try:
        key = pronunciation_key(body.word)
        await asyncio.to_thread(_check_entry, body.word, body.say_like)
    except PronunciationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    async with session_scope() as s:
        repo = PronunciationsRepository(s)
        rows = await repo.all()
        new_own_word = key not in {r["key"] for r in rows} and key not in default_keys()
        if new_own_word and owner_words(rows) >= MAX_PRONUNCIATIONS:
            raise HTTPException(status_code=409, detail=f"the list holds at most {MAX_PRONUNCIATIONS} words")
        await repo.put(key=key, word=body.word.strip(), say_like=body.say_like.strip())
        await repo.touch()
    await _reload_core(request)
    return await _payload()


@router.delete("/pronunciations/{word}", response_model=VoiceSettings, dependencies=ADMIN)
async def delete_pronunciation(word: str, request: Request) -> dict:
    """Take a word off the list: the converter decides how to say it again.
    The owner's own word's row is deleted; a shipped entry is marked
    removed, and stays off until it is added again or the defaults are put
    back."""
    try:
        key = pronunciation_key(word)
    except PronunciationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    async with session_scope() as s:
        repo = PronunciationsRepository(s)
        rows = await repo.all()
        listed = {pronunciation_key(w): w for w, _ in effective_pronunciations(rows)}
        if key not in listed:
            raise HTTPException(status_code=404, detail=f"{word!r} is not on the list")
        if key in default_keys():
            await repo.put(key=key, word=listed[key], say_like=None)
        else:
            await repo.delete([key])
        await repo.touch()
    await _reload_core(request)
    return await _payload()


@router.post("/pronunciations/defaults", response_model=VoiceSettings, dependencies=ADMIN)
async def restore_default_pronunciations(request: Request) -> dict:
    """Put every shipped entry back as it shipped: the owner's rows for the
    default words (changes and removals) are deleted, so the defaults of
    the release the box runs apply, now and after later updates. The
    owner's own words stay."""
    async with session_scope() as s:
        repo = PronunciationsRepository(s)
        await repo.delete(default_keys())
        await repo.touch()
    await _reload_core(request)
    return await _payload()


@router.post("/pronunciations/preview", dependencies=DEVICE)
async def preview_pronunciation(body: PreviewBody, request: Request) -> Response:
    """Hear a word said as ``say_like`` asks, without saving it, in
    ``voice`` (one of the three) or the server's voice. The phonemes come
    back in ``X-Phonemes``."""
    try:
        pronunciation_key(body.word)
        await asyncio.to_thread(_check_entry, body.word, body.say_like)
    except PronunciationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    if body.voice is not None and body.voice not in PIPER_PINS:
        raise HTTPException(status_code=400, detail=f"{body.voice!r} is not one of the voices")
    status, audio, headers = await post_admin_bytes(
        "/v1/admin/voice/say-like",
        {"word": body.word, "say_like": body.say_like, "voice": body.voice},
        headers=auth_forward_headers(request), timeout=120.0,
    )
    if status != 200 or not audio:
        raise core_audio_refusal(status, audio, "the preview could not be said")
    out = {"X-Phonemes": headers["x-phonemes"]} if headers.get("x-phonemes") else {}
    return Response(content=audio, media_type="audio/wav", headers=out)
