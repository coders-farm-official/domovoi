"""Wake Words API — the custom wake-word registry (wake_words).

A household trains its own openWakeWord model: record positive clips on a
satellite, hand the set to an (operator-configured, Linux-only) trainer,
and a ``<slug>.onnx`` lands that satellites can pull and switch to. This
page manages that lifecycle: list wake words, create one (name + spoken
phrase), record clips on a chosen room, kick off training once enough
clips are captured, set the default, rename, tune the detection threshold,
push a trained model to a satellite, and delete.

The load-bearing identifier is the ``slug`` (= ``voice_slug(name)``): the
served ``<slug>.onnx`` filename, the Pi's effective wake word, the
openWakeWord prediction-dict key, and the model file stem all equal it.
We derive it once on create so they never disagree — a mismatch and the
Pi silently never wakes.

Recording, training, and pushing each reach into live state the web
process doesn't own (an active Pi session, the trainer queue), so those
routes proxy the Domovoi server's ``/v1/admin/wake/*`` endpoints over HTTP
and pass its status through verbatim (``bridge_response``). Pure-DB
mutations (create / patch / train-enqueue / delete) run locally and fire
``pg_notify('wake_words_changed', ...)`` in their own transaction so the
dashboard's realtime layer refreshes clip-count / status sub-second.

Recorded clips live under ``settings.wake_clips_dir/<slug>/`` and the
trained model under ``settings.wake_models_dir/<slug>.onnx`` — both
server-private dirs the Domovoi core owns; delete cleans them up,
path-guarded.

Note on mic boards: clips should be recorded on the SAME board the
satellite uses at runtime. The XVF3800's on-chip beamforming / AGC
reshapes the signal, so a model trained from HAT-recorded clips may
detect poorly when pushed to an XVF3800 array (and vice versa).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from domovoi import wake_clip_quality as wq
from domovoi.canned_sounds import voice_slug
from domovoi.config import settings
from domovoi.db.repositories import WakeWordsRepository
from web.backend.db import session_scope
from web.backend.domovoi_client import bridge_response, post_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wake-words", tags=["wake-words"])


class WakeWord(BaseModel):
    id: int
    name: str
    slug: str
    phrase: str
    threshold: float
    model_ref: str | None = None
    is_default: bool
    status: str
    source_room_id: str | None = None
    clip_count: int
    error: str | None = None


class WakeWordCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    phrase: str = Field(..., min_length=1, max_length=120)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    source_room_id: str | None = Field(default=None, max_length=120)


class WakeWordPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    set_default: bool | None = None


class RoomBody(BaseModel):
    room_id: str = Field(..., min_length=1, max_length=120)


class ClipMetrics(BaseModel):
    peak_dbfs: float
    rms_dbfs: float
    noise_dbfs: float
    snr_db: float
    clipping_pct: float
    speech_ratio: float
    voiced_ms: int
    leading_silence_ms: int
    trailing_silence_ms: int


class WakeClip(BaseModel):
    """One recorded positive clip, with its computed acoustic quality, the
    auto-trim bounds, an energy sparkline, and whether it's selected for
    training. ``score`` is the offline openWakeWord max-over-clip score if it's
    been run (else null)."""
    name: str
    verdict: str                       # good | fair | poor
    issues: list[str]
    selected: bool
    raw_duration_ms: int
    trimmed_duration_ms: int
    has_trimmed: bool
    metrics: ClipMetrics
    envelope: list[float]
    trim: dict
    score: float | None = None


class WakeClipList(BaseModel):
    slug: str
    count: int
    selected_count: int
    min_clips: int
    clips: list[WakeClip]


class ClipSelectBody(BaseModel):
    selected: bool


class ClipSelectionBody(BaseModel):
    """Bulk selection. Applies ``selected`` to the clips matched by the
    optional filters (both may be combined): ``names`` = an explicit subset,
    ``only_verdict`` = only clips with that verdict. With neither filter it
    applies to ALL clips (select-all / deselect-all)."""
    selected: bool
    names: list[str] | None = None
    only_verdict: str | None = Field(default=None, pattern="^(good|fair|poor)$")


def _clip_to_model(rec: dict) -> WakeClip:
    return WakeClip(
        name=rec["name"],
        verdict=rec["verdict"],
        issues=list(rec.get("issues") or []),
        selected=bool(rec.get("selected", True)),
        raw_duration_ms=int(rec.get("raw_duration_ms", 0)),
        trimmed_duration_ms=int(rec.get("trimmed_duration_ms", 0)),
        has_trimmed=bool(rec.get("has_trimmed", False)),
        metrics=ClipMetrics(**rec["metrics"]),
        envelope=list(rec.get("envelope") or []),
        trim=dict(rec.get("trim") or {}),
        score=rec.get("score"),
    )


def _guarded_clip_dir(slug: str) -> Path | None:
    """Resolve + path-guard a wake word's clip dir. None if a crafted slug
    escaped the clips root."""
    cdir = (_clips_dir() / slug).resolve()
    try:
        cdir.relative_to(_clips_dir().resolve())
    except ValueError:
        return None
    return cdir


def _guarded_raw_clip(cdir: Path, name: str) -> Path:
    """Resolve + path-guard a raw clip path within ``cdir``. Raises 400 on a
    crafted name that escapes the dir."""
    target = (cdir / name).resolve()
    try:
        target.relative_to(cdir)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid clip name") from e
    return target


def _clips_dir() -> Path:
    return Path(settings.wake_clips_dir)


def _models_dir() -> Path:
    return Path(settings.wake_models_dir)


async def _notify(s) -> None:
    """Wake the dashboard's LISTEN task on the ``wake_words_changed``
    channel, in the same transaction as the mutation that called us, so a
    rolled-back commit never delivers a phantom event. The realtime layer
    maps it onto the ``wake_words`` snapshot — the page's clip-count /
    status pills refresh sub-second instead of waiting for the poll tick."""
    await s.execute(text("SELECT pg_notify('wake_words_changed', 'web')"))


def _to_model(row: dict) -> WakeWord:
    return WakeWord(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        phrase=row["phrase"],
        threshold=row["threshold"],
        model_ref=row["model_ref"],
        is_default=row["is_default"],
        status=row["status"],
        source_room_id=row["source_room_id"],
        clip_count=row["clip_count"],
        error=row["error"],
    )


@router.get("", response_model=list[WakeWord])
async def list_wake_words() -> list[WakeWord]:
    async with session_scope() as s:
        rows = await WakeWordsRepository(s).all()
    return [_to_model(r) for r in rows]


@router.post("", response_model=WakeWord, status_code=201)
async def create_wake_word(payload: WakeWordCreate) -> WakeWord:
    """Register a new wake word in the ``recording`` status. The slug is
    derived from the name (``voice_slug``) and is the load-bearing runtime
    identifier; a name OR slug collision raises ``IntegrityError`` → 409."""
    name = payload.name.strip()
    phrase = payload.phrase.strip()
    slug = voice_slug(name)
    threshold = payload.threshold if payload.threshold is not None else 0.5
    try:
        async with session_scope() as s:
            repo = WakeWordsRepository(s)
            new_id = await repo.create(
                name=name,
                slug=slug,
                phrase=phrase,
                threshold=threshold,
                source_room_id=payload.source_room_id,
            )
            row = await repo.get(new_id)
            await _notify(s)
    except IntegrityError as e:
        raise HTTPException(
            status_code=409,
            detail="a wake word with that name (or slug) already exists",
        ) from e
    return _to_model(row or {
        "id": new_id, "name": name, "slug": slug, "phrase": phrase,
        "threshold": threshold, "model_ref": None, "is_default": False,
        "status": "recording", "source_room_id": payload.source_room_id,
        "clip_count": 0, "error": None,
    })


@router.post("/{wake_word_id}/record/start")
async def record_start(wake_word_id: int, body: RoomBody):
    """Tell a connected satellite to start recording positive clips for
    this wake word. Proxies the Domovoi server, which owns the live Pi
    session: pass-through 404 (room not connected / wake word missing),
    502 on a send failure."""
    status, payload = await post_admin(
        "/v1/admin/wake/record/start",
        {"room_id": body.room_id, "wake_word_id": wake_word_id},
    )
    return bridge_response(status, payload)


@router.post("/{wake_word_id}/record/stop")
async def record_stop(wake_word_id: int, body: RoomBody):
    """Stop an in-progress recording on ``room_id`` so the Pi resumes its
    normal wake loop. Pass-through 404 when the room isn't connected."""
    status, payload = await post_admin(
        "/v1/admin/wake/record/stop",
        {"room_id": body.room_id},
    )
    return bridge_response(status, payload)


@router.post("/{wake_word_id}/train", response_model=WakeWord)
async def train_wake_word(wake_word_id: int) -> WakeWord:
    """Promote a ``recording`` wake word to ``training`` — picked up by the
    trainer worker (if enabled). Refuses (409) a row that's missing, past
    ``recording``, holding fewer than ``wake_word_min_clips`` clips total, or
    fewer than that many *selected* for training (the trainer consumes only the
    selected set)."""
    min_clips = settings.wake_word_min_clips
    row0 = await _row_or_404(wake_word_id)
    # Gate on SELECTED clips — the curated set is what actually trains.
    cdir = _guarded_clip_dir(row0["slug"])
    selected = await asyncio.to_thread(wq.selected_count, cdir) if cdir else 0
    if selected < min_clips:
        raise HTTPException(
            status_code=409,
            detail=(
                f"wake word {wake_word_id} can't be trained — needs at least "
                f"{min_clips} SELECTED clips, but {selected} are selected"
            ),
        )
    async with session_scope() as s:
        repo = WakeWordsRepository(s)
        if not await repo.mark_training(wake_word_id, min_clips):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"wake word {wake_word_id} can't be trained — it must be in "
                    f"'recording' with at least {min_clips} clips"
                ),
            )
        row = await repo.get(wake_word_id)
        await _notify(s)
    if row is None:
        raise HTTPException(status_code=404, detail=f"wake word {wake_word_id} not found")
    return _to_model(row)


async def _row_or_404(wake_word_id: int) -> dict:
    async with session_scope() as s:
        row = await WakeWordsRepository(s).get(wake_word_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"wake word {wake_word_id} not found")
    return row


@router.get("/{wake_word_id}/clips", response_model=WakeClipList)
async def list_clips(wake_word_id: int) -> WakeClipList:
    """Recorded clips for a wake word, each with computed quality metrics, the
    auto-trim + a sparkline envelope, and its training-selection state.

    Reads ``settings.wake_clips_dir/<slug>/`` directly and lazily backfills the
    analysis sidecar for any clip that lacks a current one (so pre-existing
    clips get scored on first view). The heavy work runs off the event loop."""
    row = await _row_or_404(wake_word_id)
    cdir = _guarded_clip_dir(row["slug"])
    if cdir is None or not cdir.is_dir():
        return WakeClipList(
            slug=row["slug"], count=0, selected_count=0,
            min_clips=settings.wake_word_min_clips, clips=[],
        )
    records = await asyncio.to_thread(wq.analyze_dir, cdir)
    clips = [_clip_to_model(r) for r in records]
    return WakeClipList(
        slug=row["slug"],
        count=len(clips),
        selected_count=sum(1 for c in clips if c.selected),
        min_clips=settings.wake_word_min_clips,
        clips=clips,
    )


@router.get("/{wake_word_id}/clips/{name}/audio")
async def clip_audio(
    wake_word_id: int,
    name: str,
    variant: str = Query("raw", pattern="^(raw|trimmed)$"),
) -> FileResponse:
    """Stream a clip's WAV for in-browser playback. ``variant=raw`` serves the
    original capture; ``variant=trimmed`` serves the auto-trimmed, end-aligned
    audit copy (lazily generated). Path-guarded."""
    row = await _row_or_404(wake_word_id)
    cdir = _guarded_clip_dir(row["slug"])
    if cdir is None:
        raise HTTPException(status_code=404, detail="clip not found")
    raw = _guarded_raw_clip(cdir, name)
    if not raw.is_file():
        raise HTTPException(status_code=404, detail=f"clip {name!r} not found")
    if variant == "trimmed":
        await asyncio.to_thread(wq.ensure_analysis, raw)
        target = wq.trimmed_path(raw)
        if not target.is_file():
            raise HTTPException(
                status_code=404, detail="no trimmed audio (no speech detected)"
            )
    else:
        target = raw
    return FileResponse(target, media_type="audio/wav", filename=target.name)


@router.patch("/{wake_word_id}/clips/{name}")
async def set_clip_selected(wake_word_id: int, name: str, body: ClipSelectBody) -> dict:
    """Include/exclude a single clip from training (marks it user-set so a
    later re-analyze won't override the choice)."""
    row = await _row_or_404(wake_word_id)
    cdir = _guarded_clip_dir(row["slug"])
    if cdir is None:
        raise HTTPException(status_code=404, detail="clip not found")
    raw = _guarded_raw_clip(cdir, name)
    if not raw.is_file():
        raise HTTPException(status_code=404, detail=f"clip {name!r} not found")
    await asyncio.to_thread(wq.set_selected, raw, body.selected)
    return {"name": name, "selected": body.selected}


@router.post("/{wake_word_id}/clips/selection")
async def bulk_select_clips(wake_word_id: int, body: ClipSelectionBody) -> dict:
    """Bulk include/exclude — all clips, an explicit ``names`` subset, and/or
    only those with a given ``only_verdict``. Returns the new selected count."""
    row = await _row_or_404(wake_word_id)
    cdir = _guarded_clip_dir(row["slug"])
    if cdir is None or not cdir.is_dir():
        return {"changed": 0, "selected_count": 0}

    def _apply() -> tuple[int, int]:
        changed = 0
        for rec in wq.analyze_dir(cdir):
            nm = rec["name"]
            if body.names is not None and nm not in body.names:
                continue
            if body.only_verdict is not None and rec.get("verdict") != body.only_verdict:
                continue
            wq.set_selected(cdir / nm, body.selected)
            changed += 1
        return changed, wq.selected_count(cdir)

    changed, sel = await asyncio.to_thread(_apply)
    return {"changed": changed, "selected_count": sel}


@router.post("/{wake_word_id}/clips/reanalyze")
async def reanalyze_clips(wake_word_id: int) -> dict:
    """Force-recompute quality + trim for every clip (e.g. after tuning). User
    selections are preserved."""
    row = await _row_or_404(wake_word_id)
    cdir = _guarded_clip_dir(row["slug"])
    if cdir is None or not cdir.is_dir():
        return {"count": 0, "selected_count": 0}
    records = await asyncio.to_thread(lambda: wq.analyze_dir(cdir, force=True))
    return {
        "count": len(records),
        "selected_count": sum(1 for r in records if r.get("selected")),
    }


@router.delete("/{wake_word_id}/clips/{name}", status_code=204)
async def delete_clip(wake_word_id: int, name: str) -> None:
    """Delete a single recorded clip (and its analysis artifacts), path-guarded
    to the wake word's clip dir so a crafted ``name`` can't unlink arbitrary
    files."""
    row = await _row_or_404(wake_word_id)
    cdir = _guarded_clip_dir(row["slug"])
    if cdir is None:
        raise HTTPException(status_code=404, detail=f"clip {name!r} not found")
    target = _guarded_raw_clip(cdir, name)
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"clip {name!r} not found")
    try:
        target.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not delete clip: {e}") from e
    # Drop the sidecar + trimmed audit copy alongside it.
    wq.remove_analysis(target)


@router.patch("/{wake_word_id}", response_model=WakeWord)
async def patch_wake_word(wake_word_id: int, payload: WakeWordPatch) -> WakeWord:
    if payload.name is None and payload.threshold is None and not payload.set_default:
        raise HTTPException(status_code=400, detail="no fields to patch")
    try:
        async with session_scope() as s:
            repo = WakeWordsRepository(s)
            if payload.name is not None or payload.threshold is not None:
                updated = await repo.update(
                    wake_word_id,
                    name=payload.name.strip() if payload.name is not None else None,
                    threshold=payload.threshold,
                )
                if not updated:
                    raise HTTPException(
                        status_code=404, detail=f"wake word {wake_word_id} not found"
                    )
            if payload.set_default:
                if not await repo.set_default(wake_word_id):
                    # The repo refuses a missing row OR a row that isn't
                    # 'ready' (you can't default an untrained wake word).
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"wake word {wake_word_id} can't be the default — it "
                            "must be trained ('ready') first"
                        ),
                    )
            row = await repo.get(wake_word_id)
            await _notify(s)
    except IntegrityError as e:
        raise HTTPException(
            status_code=409,
            detail="a wake word with that name already exists",
        ) from e
    if row is None:
        raise HTTPException(status_code=404, detail=f"wake word {wake_word_id} not found")
    return _to_model(row)


@router.post("/{wake_word_id}/score")
async def score_wake_word(wake_word_id: int):
    """Offline-score this word's clips against its trained model (raw + trimmed,
    max-over-clip) — the decisive real-vs-harness check. Proxies the
    Domovoi server (which owns openWakeWord + the model). Pass-through: 404 (no
    such word), 409 (not trained yet), 501 (openWakeWord not installed)."""
    status, payload = await post_admin(
        "/v1/admin/wake/score", {"wake_word_id": wake_word_id}
    )
    return bridge_response(status, payload)


@router.post("/{wake_word_id}/push")
async def push_wake_word(wake_word_id: int, body: RoomBody):
    """Push a trained wake model to a connected satellite. Proxies the
    Domovoi server (which sets the Pi's wake sidecar + tells it to sync and
    restart). Pass-through 404 (room not connected / wake word missing),
    409 (not trained yet), 502 on a send failure."""
    status, payload = await post_admin(
        "/v1/admin/wake/push",
        {"room_id": body.room_id, "wake_word_id": wake_word_id},
    )
    return bridge_response(status, payload)


@router.delete("/{wake_word_id}", status_code=204)
async def delete_wake_word(wake_word_id: int) -> None:
    async with session_scope() as s:
        repo = WakeWordsRepository(s)
        removed = await repo.delete(wake_word_id)
        if removed is None:
            # Either not found, or it's the default (which delete refuses).
            raise HTTPException(
                status_code=409,
                detail="wake word not found, or it's the default — set another default first",
            )
        await _notify(s)
    # Best-effort cleanup of the trained model + recorded clips, path-guarded
    # to the wake dirs so a crafted slug can't delete arbitrary files.
    slug = removed["slug"]
    mdir = _models_dir().resolve()
    for suffix in (".onnx", ".onnx.json"):
        f = (mdir / f"{slug}{suffix}").resolve()
        try:
            f.relative_to(mdir)
        except ValueError:
            continue
        if f.is_file():
            try:
                f.unlink()
            except OSError:
                pass
    cdir = _guarded_clip_dir(slug)
    if cdir is not None and cdir.is_dir():
        # Whole tree — raw clips plus the .analysis / .training subdirs.
        import shutil

        shutil.rmtree(cdir, ignore_errors=True)
