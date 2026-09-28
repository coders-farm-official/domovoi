"""Load the voice pipeline's Ollama models before anyone asks a question.

On an all-CPU host a model that isn't loaded costs the first question after
it a cold start: the load plus the router's ~3k-token tool-schema prefill,
measured at ~54 s against ~4 s warm. ``keep_alive`` keeps the models loaded
once they are, but nothing loaded them in the first place — the first
question after every core restart, model switch or Ollama restart paid the
whole cold start while the room sat silent.

So the core loads them itself, in the background: at boot (``lifespan``
schedules :func:`schedule_warm_up`) and after a change to any setting the
loaded models depend on (a reapply hook runs :func:`schedule_llm_warmup`).
The work is ``RealOllamaClient.warm_up`` — both models, with the options
their real calls send, the router's prompt prefix included. Never blocks
boot or a settings write, and never raises. ``ollama_warmup`` turns it off.
"""

from __future__ import annotations

import asyncio
import logging
import time

from domovoi.config import settings

log = logging.getLogger(__name__)

# Ollama may still be starting when the core boots (both are services on
# the same host): ask again this many times, this far apart, before giving
# up and letting the first question load the models.
_ATTEMPTS = 3
_RETRY_DELAY_SEC = 15.0

_task: asyncio.Task | None = None


async def warm_up_models(reason: str) -> list[str]:
    """Load whichever voice models Ollama doesn't have ready. Returns the
    models loaded (empty when there was nothing to do or it failed)."""
    if settings.use_stubs or not settings.ollama_warmup:
        return []
    from domovoi.clients.ollama import get_ollama_client
    from domovoi.router import offered_tool_schemas

    for attempt in range(1, _ATTEMPTS + 1):
        client = get_ollama_client()
        warm = getattr(client, "warm_up", None)
        if warm is None:
            return []
        t0 = time.monotonic()
        try:
            # The schemas a plain question is offered: the ungated tools, in
            # the order that forms the prompt prefix Ollama caches.
            loaded = await warm(offered_tool_schemas(""))
        except Exception as e:  # noqa: BLE001 — a warm-up must never fail anything
            log.warning("LLM warm-up (%s) failed: %s", reason, e)
            return []
        if loaded is not None:
            if loaded:
                log.info(
                    "LLM warm-up (%s): loaded %s in %.1f s",
                    reason, ", ".join(loaded), time.monotonic() - t0,
                )
            else:
                log.info("LLM warm-up (%s): models already loaded", reason)
            return loaded
        if attempt < _ATTEMPTS:
            log.info(
                "LLM warm-up (%s): Ollama not answering yet; retrying in %.0f s",
                reason, _RETRY_DELAY_SEC,
            )
            await asyncio.sleep(_RETRY_DELAY_SEC)
    log.warning(
        "LLM warm-up (%s): Ollama never answered; the first question will "
        "load the models", reason,
    )
    return []


def schedule_warm_up(reason: str) -> asyncio.Task | None:
    """Start :func:`warm_up_models` in the background, replacing a warm-up
    still in flight (it was loading what the settings no longer ask for).
    None when warm-up is off, under stubs, or with no running loop."""
    global _task
    if settings.use_stubs or not settings.ollama_warmup:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    if _task is not None and not _task.done():
        _task.cancel()
    _task = loop.create_task(warm_up_models(reason), name="llm-warmup")
    return _task


def schedule_llm_warmup() -> None:
    """The reapply hook: the client was just rebuilt for new model
    settings (reset_ollama_client runs first), so load what it now uses."""
    schedule_warm_up("model settings changed")


async def cancel_warm_up() -> None:
    """Stop a warm-up in flight (core shutdown)."""
    global _task
    task, _task = _task, None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
