"""Load the speech models a voice turn needs before anyone speaks.

Whisper loads at boot (``domovoi/clients/whisper.py``) and the language
models are warmed in the background (``domovoi/llm_warmup.py``), but two
more loads used to land inside the first voice turn after every restart:

* the Resemblyzer voice encoder, loaded on the first embed — and loaded ON
  THE EVENT LOOP, so for its ~600 ms every room's socket stood still
  (``identify_ms`` 633 on the first turn after a restart, ~35 warm);
* the room's Piper voice, loaded (and run for the first time) on the first
  reply in it (``tts_first_ms`` 638, against 44-99 warm).

So the core loads them itself, in worker threads, off the event loop: the
encoder and the registry's default voice at boot (``lifespan`` schedules
:func:`schedule_speech_warm_up`), and each room's own voice when the room
reports it (``voice_status``, on every satellite connect —
:func:`schedule_room_voice_warm_up`). A voice already loaded is skipped,
and a load already running is joined, not repeated. Never blocks boot or
a socket, never raises; nothing happens under stubs. A turn that races a
load waits for that same load (the loaders hold a lock) instead of
starting a second one.
"""

from __future__ import annotations

import asyncio
import logging
import time

from domovoi.config import settings

log = logging.getLogger(__name__)

_boot_task: asyncio.Task | None = None
# model_ref → the preload running for it, so a burst of satellites
# reporting the same voice at boot loads it once.
_voice_tasks: dict[str, asyncio.Task] = {}
_room_tasks: set[asyncio.Task] = set()


async def warm_voice_encoder() -> bool:
    """Load the voice encoder and run it once (clients/voice_embedder.py).
    False when there is nothing to load or the load failed."""
    from domovoi.clients.voice_embedder import get_voice_embedder

    preload = getattr(get_voice_embedder(), "preload", None)
    if preload is None:
        return False
    t0 = time.monotonic()
    try:
        ok = bool(await preload())
    except Exception as e:  # noqa: BLE001 — a warm-up must never fail anything
        log.warning(
            "speech warm-up: the voice encoder didn't load (%s: %s); the first "
            "turn will try again", type(e).__name__, e,
        )
        return False
    log.info("speech warm-up: voice encoder ready in %.1f s", time.monotonic() - t0)
    return ok


async def _voice_ref(voice_name: str | None) -> str | None:
    """The Piper model_ref a room speaking ``voice_name`` synthesizes with
    — exactly as the turn resolves it (streaming.resolve_voice, then the
    TTS client's own default) — or None when that isn't Piper."""
    from domovoi.streaming import resolve_voice

    engine, ref = await resolve_voice(voice_name)
    if engine is None:
        engine, ref = settings.tts_engine, settings.tts_piper_voice
    if engine != "piper" or not ref:
        return None
    return str(ref)


async def warm_piper_voice(ref: str) -> bool:
    """Load one Piper voice (by model_ref) in a worker thread, once."""
    from domovoi.clients import tts

    if tts.piper_voice_loaded(ref):
        return True
    running = _voice_tasks.get(ref)
    if running is None or running.done():
        running = asyncio.ensure_future(_load_piper(ref))
        _voice_tasks[ref] = running
    return await asyncio.shield(running)


async def _load_piper(ref: str) -> bool:
    from domovoi.clients import tts

    t0 = time.monotonic()
    try:
        ok = await asyncio.to_thread(tts.preload_piper_voice_sync, ref)
    except Exception as e:  # noqa: BLE001
        log.warning(
            "speech warm-up: Piper voice %s didn't load (%s: %s); the first reply "
            "in it will try again", ref, type(e).__name__, e,
        )
        return False
    finally:
        _voice_tasks.pop(ref, None)
    if ok:
        log.info("speech warm-up: Piper voice %s ready in %.1f s", ref, time.monotonic() - t0)
    return ok


async def warm_room_voice(voice_name: str | None) -> bool:
    """Load the Piper voice a room speaking ``voice_name`` (None: the
    registry default) replies in. False when it isn't a Piper voice or
    didn't load."""
    try:
        ref = await _voice_ref(voice_name)
    except Exception as e:  # noqa: BLE001
        log.debug("speech warm-up: can't resolve voice %r: %s", voice_name, e)
        return False
    if ref is None:
        return False
    return await warm_piper_voice(ref)


async def warm_speech(reason: str) -> None:
    """Everything above, for boot: the encoder, then the default voice."""
    if settings.use_stubs:
        return
    log.info("speech warm-up (%s): loading the voice encoder and the default voice", reason)
    await warm_voice_encoder()
    await warm_room_voice(None)


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def schedule_speech_warm_up(reason: str = "boot") -> asyncio.Task | None:
    """Start :func:`warm_speech` in the background. None under stubs or
    with no running loop."""
    global _boot_task
    loop = _running_loop()
    if settings.use_stubs or loop is None:
        return None
    if _boot_task is not None and not _boot_task.done():
        return _boot_task
    _boot_task = loop.create_task(warm_speech(reason), name="speech-warmup")
    return _boot_task


def schedule_room_voice_warm_up(voice_name: str | None) -> asyncio.Task | None:
    """A room just said which voice it speaks in: load that voice in the
    background, before its first reply. None under stubs or with no
    running loop."""
    loop = _running_loop()
    if settings.use_stubs or loop is None:
        return None
    task = loop.create_task(warm_room_voice(voice_name), name="speech-warmup-voice")
    # The loop keeps only a weak reference to a task: hold it until done.
    _room_tasks.add(task)
    task.add_done_callback(_room_tasks.discard)
    return task


async def cancel_speech_warm_up() -> None:
    """Stop a boot warm-up in flight (core shutdown). A load already in
    its worker thread finishes there; nothing waits for it."""
    global _boot_task
    task, _boot_task = _boot_task, None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
