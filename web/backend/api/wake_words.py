"""Wake Words API — the custom wake-word registry (wake_words).

A household trains its own openWakeWord model: record positive clips on a
satellite, hand the set to an (operator-configured, Linux-only) trainer,
and a ``<slug>.onnx`` lands that satellites can pull and switch to. This
page manages that lifecycle: list wake words, create one (name + spoken
phrase), record clips on a chosen room, kick off training once enough
clips are captured, set the default, rename, tune the detection threshold,
push a trained model to a satellite, and delete.

Takes append (2026-11): each Record adds clips after the ones already
there, from any room, until the household trains; a trained word can keep
recording and train again. "Delete all clips" is the explicit way to start
over.

The load-bearing identifier is the ``slug`` (= ``wake_slug(name)``): the
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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from domovoi.admin_auth import (
    require_admin_mutation,
    require_admin_read,
    require_admin_security,
    require_device_read,
)
from domovoi import wake_clip_quality as wq
from domovoi.canned_sounds import wake_slug
from domovoi.config import settings
from domovoi.db.repositories import WakeWordsRepository
from domovoi.models import MAX_WAKE_PHRASE_CHARS, WAKE_PHRASE_PATTERN
from web.backend.db import session_scope
from web.backend.domovoi_client import (
    auth_forward_headers,
    bridge_response,
    get_admin,
    post_admin,
)

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
    # Clips recorded since the last successful training (0 for a word never
    # trained): the row says "N new clips since the last training".
    new_clips: int = 0
    # Trains on the synthetic voices alone: no recordings, no clip minimum.
    synthetic_only: bool = False
    # How many of the household's recordings the CURRENT model was trained
    # on: 0 = synthetic voices only. None = no model, or a model trained
    # before Domovoi kept count that the clips folder can't account for
    # either (the page then says it may include recordings).
    # ``trained_clips_from``: "model" (recorded when it was trained) or
    # "marker" (the clips folder's note of its last training, for a model
    # from before V047).
    trained_clips: int | None = None
    trained_clips_from: str | None = None


class WakeWordCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    # CORE-10: a phrase is words someone says out loud, so it is bounded
    # to letters, digits, spaces and spoken punctuation. It ends up as an
    # argument to the operator's configured training command, and a value
    # carrying quotes or shell metacharacters is not a phrase. 422 here;
    # the trainer re-checks before it runs anything (defence in depth,
    # and for rows created before this bound existed).
    phrase: str = Field(
        ...,
        min_length=1,
        max_length=MAX_WAKE_PHRASE_CHARS,
        pattern=WAKE_PHRASE_PATTERN,
    )
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    source_room_id: str | None = Field(default=None, max_length=120)
    # Train on the synthetic voices only: no recordings, ever, for this word.
    synthetic_only: bool = False


class WakeWordPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    set_default: bool | None = None
    # How the training voices say it (a respelling), bounded like create's.
    # It changes the next training's synthetic clips, never the slug the
    # rooms know the word by.
    phrase: str | None = Field(
        default=None, min_length=1, max_length=MAX_WAKE_PHRASE_CHARS,
        pattern=WAKE_PHRASE_PATTERN,
    )


class PreviewBody(BaseModel):
    # The phrase as typed, saved or not: the voices say what is in the box.
    phrase: str = Field(
        ..., min_length=1, max_length=MAX_WAKE_PHRASE_CHARS, pattern=WAKE_PHRASE_PATTERN,
    )


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
    # Where it came from (null for a clip recorded before 2026-11): the room
    # that recorded it, when its take started and when it was recorded
    # (UTC, ISO 8601). Takes append, so one word's clips can span rooms.
    room: str | None = None
    take_started_at: str | None = None
    recorded_at: str | None = None


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
        room=rec.get("room"),
        take_started_at=rec.get("take_started_at"),
        recorded_at=rec.get("recorded_at"),
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


def _new_clips(row: dict) -> int:
    """Clips recorded since the word's last successful training (0 for a
    word that has no model). A directory listing, not a sidecar read."""
    if not row.get("model_ref"):
        return 0
    cdir = _guarded_clip_dir(row["slug"])
    if cdir is None:
        return 0
    try:
        return wq.new_since_training(cdir, _models_dir() / f"{row['slug']}.onnx")
    except OSError:
        return 0


def _trained_with(row: dict) -> tuple[int | None, str | None]:
    """What the current model was trained on: the row's count (V047 on);
    for an older model, the clips folder's training marker when it names a
    count (its 0 can't be trusted: older trainings fell back to every clip
    when none was selected); else unknown."""
    if not row.get("model_ref"):
        return None, None
    if row.get("trained_clips") is not None:
        return int(row["trained_clips"]), "model"
    cdir = _guarded_clip_dir(row["slug"])
    marker = wq.read_trained(cdir) if cdir is not None else None
    try:
        n = int((marker or {}).get("clips") or 0)
    except (TypeError, ValueError):
        n = 0
    return (n, "marker") if n > 0 else (None, None)


async def _with_new_clips(rows: list[dict]) -> list[WakeWord]:
    def _read() -> list[tuple[int, tuple[int | None, str | None]]]:
        return [(_new_clips(r), _trained_with(r)) for r in rows]

    info = await asyncio.to_thread(_read)
    return [_to_model(r, new_clips=n, trained=t) for r, (n, t) in zip(rows, info)]


async def _resync_clip_count(wake_word_id: int, cdir: Path) -> int:
    """Set the row's clip_count to the clips on disk (after a delete) and
    tell the dashboard."""
    n = len(await asyncio.to_thread(wq.iter_raw_clips, cdir))
    async with session_scope() as s:
        await WakeWordsRepository(s).set_clip_count(wake_word_id, n)
        await _notify(s)
    return n


def _to_model(row: dict, *, new_clips: int = 0,
              trained: tuple[int | None, str | None] | None = None) -> WakeWord:
    if trained is None:
        trained = ((int(row["trained_clips"]), "model")
                   if row.get("model_ref") and row.get("trained_clips") is not None
                   else (None, None))
    return WakeWord(
        new_clips=new_clips,
        synthetic_only=bool(row.get("synthetic_only")),
        trained_clips=trained[0],
        trained_clips_from=trained[1],
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
    return await _with_new_clips(rows)


@router.post(
    "", response_model=WakeWord, status_code=201,
    dependencies=[Depends(require_admin_mutation)],
)
async def create_wake_word(payload: WakeWordCreate) -> WakeWord:
    """Register a new wake word in the ``recording`` status. The slug is
    derived from the name (``wake_slug``: ``voice_slug``, never a Windows
    device name, which no satellite would sync) and is the load-bearing
    runtime identifier; a name OR slug collision raises ``IntegrityError`` →
    409."""
    name = payload.name.strip()
    phrase = payload.phrase.strip()
    slug = wake_slug(name)
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
                synthetic_only=payload.synthetic_only,
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
        "clip_count": 0, "error": None, "synthetic_only": payload.synthetic_only,
    })


# ─── Training on/off: "Turn on wake-word training" ─────────────────────────
# The core owns it (it builds and runs a Docker image as the service user
# and writes two settings; domovoi/wake_trainer_setup.py), so these proxy
# /v1/admin/wake/trainer* with the caller's credentials. Declared before
# the /{wake_word_id} routes.


class TrainerEnableBody(BaseModel):
    # Yes to training on the CPU (over an hour a word; voice slower meanwhile).
    cpu_ok: bool = False
    # The Windows install can't see C:'s free space: the admin confirms it.
    disk_ok: bool = False


@router.get("/trainer", dependencies=[Depends(require_admin_read)])
async def trainer_status(request: Request):
    """Whether training is on, the setup job's progress or its error, and
    what training needs against what the server has, with any refusal."""
    status, payload = await get_admin(
        "/v1/admin/wake/trainer", timeout=30.0, headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


@router.post("/trainer/enable", dependencies=[Depends(require_admin_security)])
async def trainer_enable(body: TrainerEnableBody, request: Request):
    """Start "Turn on wake-word training" on the core: 200 with the status
    (the job runs in the background), 409 with the reason it can't."""
    status, payload = await post_admin(
        "/v1/admin/wake/trainer/enable", {"cpu_ok": body.cpu_ok, "disk_ok": body.disk_ok},
        timeout=60.0, headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


@router.get("/trainer/footprint", dependencies=[Depends(require_admin_read)])
async def trainer_footprint(request: Request):
    """What "Turn off and free the space" would remove, as Docker sizes it."""
    status, payload = await get_admin(
        "/v1/admin/wake/trainer/footprint", timeout=150.0, headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


@router.post("/trainer/disable", dependencies=[Depends(require_admin_security)])
async def trainer_disable(request: Request):
    """Turn training off and free the space (the core removes the trainer
    image, its build cache and the cache volume); 409 while it is busy."""
    status, payload = await post_admin(
        "/v1/admin/wake/trainer/disable", {}, timeout=600.0,
        headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


@router.post("/{wake_word_id}/train/stop", dependencies=[Depends(require_admin_mutation)])
async def train_stop(wake_word_id: int, request: Request):
    """Stop this word's training (running or queued): it goes back to
    "recording" with its clips. Proxies the core, which owns the run."""
    status, payload = await post_admin(
        "/v1/admin/wake/train/stop", {"wake_word_id": wake_word_id},
        timeout=90.0, headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


@router.post("/{wake_word_id}/record/start", dependencies=[Depends(require_admin_mutation)])
async def record_start(wake_word_id: int, body: RoomBody, request: Request):
    """Tell a connected satellite to start recording positive clips for
    this wake word. Proxies the Domovoi server, which owns the live Pi
    session: pass-through 404 (room not connected / wake word missing),
    502 on a send failure."""
    status, payload = await post_admin(
        "/v1/admin/wake/record/start",
        {"room_id": body.room_id, "wake_word_id": wake_word_id},
        headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


@router.post("/{wake_word_id}/record/stop", dependencies=[Depends(require_admin_mutation)])
async def record_stop(wake_word_id: int, body: RoomBody, request: Request):
    """Stop an in-progress recording on ``room_id`` so the Pi resumes its
    normal wake loop. Pass-through 404 when the room isn't connected."""
    status, payload = await post_admin(
        "/v1/admin/wake/record/stop",
        # The word too: the core stops whichever room records it, not only
        # the room the picker shows (see the core endpoint).
        {"room_id": body.room_id, "wake_word_id": wake_word_id},
        headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


@router.post(
    "/{wake_word_id}/train", response_model=WakeWord,
    dependencies=[Depends(require_admin_mutation)],
)
async def train_wake_word(wake_word_id: int) -> WakeWord:
    """Queue a wake word for training — picked up by the trainer worker (if
    enabled). A ``recording`` or ``failed`` word trains for the first time
    (or again); a ``ready`` one trains again on every selected clip, the ones
    recorded since its last training included, and keeps its current model
    in use until the new one lands. Refuses (409) a row that's missing,
    already training, holding fewer than ``wake_word_min_clips`` clips total,
    or fewer than that many *selected* for training (the trainer consumes
    only the selected set)."""
    min_clips = settings.wake_word_min_clips
    row0 = await _row_or_404(wake_word_id)
    # Gate on SELECTED clips — the curated set is what actually trains. A
    # synthetic-voices-only word uses no clips, so it has no minimum.
    cdir = _guarded_clip_dir(row0["slug"])
    selected = await asyncio.to_thread(wq.selected_count, cdir) if cdir else 0
    if not row0.get("synthetic_only") and selected < min_clips:
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
                    f"wake word {wake_word_id} can't be trained — it is already "
                    f"training, or has fewer than {min_clips} clips"
                ),
            )
        row = await repo.get(wake_word_id)
        await _notify(s)
    if row is None:
        raise HTTPException(status_code=404, detail=f"wake word {wake_word_id} not found")
    return _to_model(row)


SYNTHETIC_SUFFIX = "_synthetic"


def synthetic_copy_slug(slug: str) -> str:
    """The slug of a word's synthetic-voices-only copy: its own model file
    (``<slug>_synthetic.onnx``), so it sits beside the recorded word's model
    and either can be pushed to a room. At most 80 characters, like every
    slug the trainer takes."""
    return slug[: 80 - len(SYNTHETIC_SUFFIX)] + SYNTHETIC_SUFFIX


@router.post(
    "/{wake_word_id}/synthetic-copy", response_model=WakeWord, status_code=201,
    dependencies=[Depends(require_admin_mutation)],
)
async def synthetic_copy(wake_word_id: int) -> WakeWord:
    """"Train on synthetic voices only" on a word that uses recordings: a
    new word with the same phrase that trains on the trainer's synthetic
    voices alone, queued for training at once. It is its own word, with its
    own model file, so the recorded word's model is never replaced and the
    two can be compared room by room. 409 when the copy exists already
    (train it from its own row) or the word is itself synthetic-only."""
    src = await _row_or_404(wake_word_id)
    if src.get("synthetic_only"):
        raise HTTPException(
            status_code=409,
            detail=f"“{src['name']}” already trains on synthetic voices only",
        )
    slug = synthetic_copy_slug(src["slug"])
    name = f"{src['name']} (synthetic voices)"[:80]
    try:
        async with session_scope() as s:
            repo = WakeWordsRepository(s)
            new_id = await repo.create(
                name=name, slug=slug, phrase=src["phrase"], threshold=src["threshold"],
                synthetic_only=True,
            )
            await repo.mark_training(new_id, settings.wake_word_min_clips)
            row = await repo.get(new_id)
            await _notify(s)
    except IntegrityError as e:
        raise HTTPException(
            status_code=409,
            detail=(f"a synthetic-voices copy of “{src['name']}” already exists "
                    f"(“{name}”): train it from its own row"),
        ) from e
    if row is None:
        raise HTTPException(status_code=404, detail="the copy was not created")
    return _to_model(row)


# ─── "Hear the training voices" ────────────────────────────────────────────
# The core owns Docker and runs the trainer image's preview mode
# (domovoi/wake_preview.py); these proxy it, and the clips it wrote are
# served from its folder like recorded clips are.

_PREVIEW_JOB_RE = r"^[0-9a-f]{12}$"
_PREVIEW_CLIP_RE = r"^preview_[1-8]\.wav$"


@router.get("/preview", dependencies=[Depends(require_admin_read)])
async def preview_status(request: Request):
    """The current or last preview: state, phrase, elapsed seconds, the
    trainer's last line, clip names; ``available`` false with the reason
    when the training voices can't speak here (training not turned on)."""
    status, payload = await get_admin(
        "/v1/admin/wake/preview", timeout=15.0, headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


@router.post("/preview", dependencies=[Depends(require_admin_mutation)])
async def preview_start(body: PreviewBody, request: Request):
    """Speak the phrase with the trainer's own synthetic voices (a few
    clips). 409 with the reason when it can't run."""
    status, payload = await post_admin(
        "/v1/admin/wake/preview", {"phrase": body.phrase.strip()}, timeout=30.0,
        headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


def _preview_root() -> Path:
    # wake_trainer_setup.state_dir()/preview, from the same setting the core
    # uses (the two processes share the server's disk, as for clips).
    return _models_dir().parent / "wake_trainer" / "preview"


@router.get(
    "/preview/{job}/{name}/audio",
    # Device READ, like a recorded clip's audio: the <audio> element can't
    # set a header. These are synthetic voices, nobody's recording.
    dependencies=[Depends(require_device_read)],
)
async def preview_audio(
    job: str = PathParam(..., pattern=_PREVIEW_JOB_RE),
    name: str = PathParam(..., pattern=_PREVIEW_CLIP_RE),
) -> FileResponse:
    root = _preview_root().resolve()
    target = (root / job / name).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise HTTPException(status_code=404, detail="no such clip") from e
    if not target.is_file():
        raise HTTPException(status_code=404, detail="no such clip (a newer preview replaced it)")
    return FileResponse(target, media_type="audio/wav", filename=name)


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


@router.get(
    "/{wake_word_id}/clips/{name}/audio",
    # Device READ tier (owner decision 2026-09-26): a clip is a recording
    # of somebody in the house saying the wake phrase — household speech,
    # for paired devices only. The <audio> element that plays it cannot set
    # a header, so the dashboard cookie and ?device_token= pass as they do
    # on every media read. The clip LIST beside it (names, quality metrics,
    # an amplitude envelope) carries no audio and stays open.
    dependencies=[Depends(require_device_read)],
)
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


@router.patch("/{wake_word_id}/clips/{name}", dependencies=[Depends(require_admin_mutation)])
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


@router.post("/{wake_word_id}/clips/selection", dependencies=[Depends(require_admin_mutation)])
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


@router.post("/{wake_word_id}/clips/reanalyze", dependencies=[Depends(require_admin_mutation)])
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


@router.delete(
    "/{wake_word_id}/clips/{name}", status_code=204,
    dependencies=[Depends(require_admin_mutation)],
)
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
    # The row's count follows the files (it gates Train).
    await _resync_clip_count(wake_word_id, cdir)


@router.delete(
    "/{wake_word_id}/clips",
    # Same tier as deleting one clip; the dashboard confirms first, naming
    # the count.
    dependencies=[Depends(require_admin_mutation)],
)
async def delete_all_clips(wake_word_id: int) -> dict:
    """Delete every recorded clip of a wake word (with their analysis and the
    training copy). The word, its trained model and the rooms using it are
    untouched; the next take starts numbering after the last trained clip, so
    the row still counts what is new since that training. 409 while the word
    trains (the run reads the training copy)."""
    row = await _row_or_404(wake_word_id)
    if row["status"] == "training":
        raise HTTPException(
            status_code=409,
            detail=f"“{row['name']}” is training; delete its clips after it finishes",
        )
    cdir = _guarded_clip_dir(row["slug"])
    if cdir is None:
        raise HTTPException(status_code=404, detail="clips not found")
    removed = await asyncio.to_thread(wq.remove_all_clips, cdir) if cdir.is_dir() else 0
    left = await _resync_clip_count(wake_word_id, cdir)
    return {"deleted": removed, "clip_count": left}


@router.patch(
    "/{wake_word_id}", response_model=WakeWord,
    dependencies=[Depends(require_admin_mutation)],
)
async def patch_wake_word(wake_word_id: int, payload: WakeWordPatch) -> WakeWord:
    if (payload.name is None and payload.threshold is None and payload.phrase is None
            and not payload.set_default):
        raise HTTPException(status_code=400, detail="no fields to patch")
    phrase = payload.phrase.strip() if payload.phrase is not None else None
    if phrase == "":
        raise HTTPException(status_code=422, detail="the phrase can't be empty")
    try:
        async with session_scope() as s:
            repo = WakeWordsRepository(s)
            if phrase is not None:
                # A training reads the phrase when it starts and its clips
                # are spoken from it: not mid-run.
                current = await repo.get(wake_word_id)
                if current is None:
                    raise HTTPException(
                        status_code=404, detail=f"wake word {wake_word_id} not found"
                    )
                if current["status"] == "training" and current["phrase"] != phrase:
                    raise HTTPException(
                        status_code=409,
                        detail=f"“{current['name']}” is training; change how it's said "
                               "after it finishes, or stop the training first",
                    )
            if payload.name is not None or payload.threshold is not None or phrase is not None:
                updated = await repo.update(
                    wake_word_id,
                    name=payload.name.strip() if payload.name is not None else None,
                    threshold=payload.threshold,
                    phrase=phrase,
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


@router.post("/{wake_word_id}/score", dependencies=[Depends(require_admin_mutation)])
async def score_wake_word(wake_word_id: int, request: Request):
    """Offline-score this word's clips against its trained model (raw + trimmed,
    max-over-clip) — the decisive real-vs-harness check. Proxies the
    Domovoi server (which owns openWakeWord + the model). Pass-through: 404 (no
    such word), 409 (not trained yet), 501 (openWakeWord not installed)."""
    status, payload = await post_admin(
        "/v1/admin/wake/score",
        {"wake_word_id": wake_word_id},
        headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


@router.post("/{wake_word_id}/push", dependencies=[Depends(require_admin_mutation)])
async def push_wake_word(wake_word_id: int, body: RoomBody, request: Request):
    """Push a trained wake model to a connected satellite. Proxies the
    Domovoi server (which sets the Pi's wake sidecar + tells it to sync and
    restart). Pass-through 404 (room not connected / wake word missing),
    409 (not trained yet), 502 on a send failure."""
    status, payload = await post_admin(
        "/v1/admin/wake/push",
        {"room_id": body.room_id, "wake_word_id": wake_word_id},
        headers=auth_forward_headers(request),
    )
    return bridge_response(status, payload)


@router.delete("/{wake_word_id}", status_code=204, dependencies=[Depends(require_admin_mutation)])
async def delete_wake_word(wake_word_id: int, request: Request) -> None:
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
    # Its training files in the trainer's cache volume (features, synthetic
    # clips, any copies of the recordings) go too; the core owns Docker.
    # Best effort: the worker's sweep after its next start catches a miss.
    await post_admin("/v1/admin/wake/forget", {"slug": slug}, timeout=10.0,
                     headers=auth_forward_headers(request))
