"""Background jobs that give way to voice: the shared rules.

Two core jobs read things in the background with the house's own models:
chat videos (:mod:`domovoi.chat_video.runner`) and scans
(:mod:`domovoi.scan_reader.runner`). Both follow the same rules, kept
here once so they can't drift:

* before every step the job waits until no voice turn is under way
  (:func:`domovoi.voice_activity.busy`, from the wake word to the end of
  the reply), then for ``quiet_sec`` more (:meth:`VoiceFirst.wait_for_quiet`);
* a step that asks a model the voice turns also need is **cancelled** the
  moment a turn starts and asked again after it
  (:meth:`VoiceFirst.model_step`): a cancelled step is never saved;
* a helper program (ffmpeg, tesseract) runs at the lowest CPU priority and
  is **held still** while a turn runs (:meth:`VoiceFirst.hold_while_voice`).

A job class supplies three hooks: how it marks itself waiting for voice,
how it learns that the person pressed stop, and what to check before each
model step (the household's AI switch). Core-internal: not offered to
plugins.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from domovoi import voice_activity

log = logging.getLogger(__name__)

#: How often a waiting job looks again; how long the house must have been
#: quiet before a job carries on after a voice turn.
POLL_SEC = 0.1
QUIET_SEC = 2.0
#: How many times one step is tried when the model service fails in passing.
STEP_TRIES = 3


class VoiceFirst:
    """The voice-first half of a background job runner. Subclasses set
    :attr:`label` (for the log), :attr:`passthrough` (exceptions a model
    step re-raises at once) and :attr:`stopped` (raised when the person
    pressed stop), and implement :meth:`_mark_waiting`,
    :meth:`_stop_requested` and :meth:`_before_model_step`."""

    label = "background job"
    passthrough: tuple[type[BaseException], ...] = ()
    stopped: type[BaseException] = asyncio.CancelledError

    def __init__(self, *, quiet_sec: float = QUIET_SEC, poll_sec: float = POLL_SEC) -> None:
        self.quiet_sec = quiet_sec
        self.poll_sec = poll_sec
        #: Steps cancelled because a voice turn started (for tests and logs).
        self.voice_cancels = 0
        self.voice_waits = 0

    # ── hooks ───────────────────────────────────────────────────────────
    async def _mark_waiting(self, job: Any, waiting: bool) -> None:
        """Save that ``job`` holds off for a voice turn (or no longer does)."""

    async def _stop_requested(self, job: Any) -> bool:
        """Whether the person pressed stop on ``job`` (checked often)."""
        return False

    def _before_model_step(self) -> None:
        """Raise to refuse a model step (the AI switch turned off)."""

    # ── voice first ─────────────────────────────────────────────────────
    def _quiet(self) -> bool:
        """No voice turn under way, and none ended in the last ``quiet_sec``."""
        snap = voice_activity.snapshot()
        if snap["busy"]:
            return False
        since = snap.get("since_last_ms")
        return since is None or since >= self.quiet_sec * 1000

    async def wait_for_quiet(self, job: Any) -> None:
        """Return once no voice turn has been under way for ``quiet_sec``;
        the job is marked waiting for voice meanwhile."""
        while not self._quiet():
            if voice_activity.busy() and not getattr(job, "waiting", False):
                job.waiting = True
                self.voice_waits += 1
                await self._mark_waiting(job, True)
            if await self._stop_requested(job):
                raise self.stopped()
            await asyncio.sleep(self.poll_sec)
        if getattr(job, "waiting", False):
            job.waiting = False
            await self._mark_waiting(job, False)

    async def hold_while_voice(self, proc: Any) -> None:
        """For a running helper program (anything with ``suspend()`` and
        ``resume()``): held still while a voice turn runs."""
        if not voice_activity.busy():
            return
        proc.suspend()
        try:
            while voice_activity.busy():
                await asyncio.sleep(self.poll_sec)
        finally:
            proc.resume()

    async def model_step(self, job: Any, make: Callable[[], Awaitable[Any]]) -> Any:
        """One model call that a voice turn cancels: started when the house
        is quiet, cancelled the moment a turn starts (or stop is pressed),
        and started again once the turn is over. Passing failures of the
        model service are tried :data:`STEP_TRIES` times."""
        tries = 0
        while True:
            await self.wait_for_quiet(job)
            self._before_model_step()
            task = asyncio.ensure_future(make())
            interrupted = False
            try:
                while not task.done():
                    await asyncio.wait({task}, timeout=self.poll_sec)
                    if task.done():
                        break
                    if voice_activity.busy():
                        interrupted = True
                        break
                    if await self._stop_requested(job):
                        task.cancel()
                        raise self.stopped()
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001 — cancelled on purpose
                        pass
            if interrupted:
                self.voice_cancels += 1
                log.info("%s %s: a voice turn started; the step stops and runs again after it",
                         self.label, str(getattr(job, "id", ""))[:8])
                continue
            try:
                return task.result()
            except self.passthrough:
                raise
            except Exception as e:  # noqa: BLE001
                tries += 1
                if tries >= STEP_TRIES:
                    raise
                log.warning("%s %s: a step failed (%s); trying again", self.label,
                            str(getattr(job, "id", ""))[:8], type(e).__name__)
                await asyncio.sleep(min(10.0, 2.0 * tries))
