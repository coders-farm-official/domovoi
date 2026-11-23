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
  is **held still** while a turn runs (:meth:`VoiceFirst.hold_while_voice`);
* **Stop is looked at everywhere** (security audit round 6, DOC-9): at the
  top of every wait for quiet, even in a quiet house, during a model step,
  and about once a second while a helper program runs
  (:meth:`VoiceFirst.pass_hold`, which ends the program at once).

The same rules for a single model call made in the background
(:func:`wait_for_voice`, :func:`ahead_of_voice`; S3-7): the house's
Ollama answers one request at a time, so a bedtime story's passage, a
memory pass or a news briefing being written would otherwise hold a
spoken answer up until it finished (a joke waited about 8 s behind a
story). A plugin's model request through ``sdk.http`` gets them by
itself (:mod:`domovoi.sdk.http`).

A job class supplies three hooks: how it marks itself waiting for voice,
how it learns that the person pressed stop, and what to check before each
model step (the household's AI switch). Core-internal: not offered to
plugins.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from domovoi import voice_activity

log = logging.getLogger(__name__)

#: How often a waiting job looks again; how long the house must have been
#: quiet before a job carries on after a voice turn.
POLL_SEC = 0.1
QUIET_SEC = 2.0
#: How many times one step is tried when the model service fails in passing.
STEP_TRIES = 3
#: How often a running helper program looks for the person's stop.
STOP_CHECK_SEC = 1.0
#: A single background call: the quiet it waits for after a turn, and the
#: longest it waits (then it goes ahead and is not stopped again).
CALL_QUIET_SEC = 1.0
CALL_WAIT_MAX_SEC = 60.0
#: How many times one background call is stopped for voice; after that it
#: runs to its end (a false wake or a follow-up window that nobody used
#: counts as voice too, and a long passage on a CPU must finish some time).
CALL_MAX_RESTARTS = 3


def _quiet_for(quiet_sec: float) -> bool:
    snap = voice_activity.snapshot()
    if snap["busy"]:
        return False
    since = snap.get("since_last_ms")
    return since is None or since >= quiet_sec * 1000


async def wait_for_voice(*, quiet_sec: float | None = None, max_sec: float | None = None,
                         poll_sec: float | None = None) -> bool:
    """Before a background model call: wait until no voice turn is under
    way (from the wake word to the end of the reply) and none ended in the
    last ``quiet_sec`` (:data:`CALL_QUIET_SEC`). True once quiet; False
    after ``max_sec`` (:data:`CALL_WAIT_MAX_SEC`) of waiting. A voice
    turn's own code never waits (:func:`voice_activity.in_turn`)."""
    if voice_activity.in_turn():
        return True
    quiet_sec = CALL_QUIET_SEC if quiet_sec is None else quiet_sec
    poll_sec = POLL_SEC if poll_sec is None else poll_sec
    deadline = time.monotonic() + (CALL_WAIT_MAX_SEC if max_sec is None else max_sec)
    while not _quiet_for(quiet_sec):
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(poll_sec)
    return True


async def _close_unread(result: Any) -> None:
    """A response nobody will read (the caller was cancelled as it came):
    closed, so its connection goes back."""
    close = getattr(result, "aclose", None)
    if close is not None:
        try:
            await close()
        except Exception:  # noqa: BLE001 — best effort
            pass


async def ahead_of_voice(make: Callable[[], Awaitable[Any]], *, label: str = "background model call",
                         poll_sec: float | None = None) -> Any:
    """Run the background model call ``make()`` so that voice goes first:
    started once the house is quiet (:func:`wait_for_voice`), and if a voice
    turn starts before it has answered, stopped (closing the request makes
    Ollama stop generating) and started again after the turn, at most
    :data:`CALL_MAX_RESTARTS` times. Only for a call whose partial work
    nobody has seen yet: a non-streamed answer, or a stream until its first
    byte. A voice turn's own call isn't stopped while that turn is open; one
    that outlives its turn (a task the turn started) is background work."""
    poll_sec = POLL_SEC if poll_sec is None else poll_sec
    restarts = 0
    while True:
        quiet = await wait_for_voice(poll_sec=poll_sec)
        task = asyncio.ensure_future(make())
        interrupted = returned = False
        try:
            while True:
                await asyncio.wait({task}, timeout=poll_sec)
                if task.done():
                    returned = True
                    return task.result()
                if (quiet and restarts < CALL_MAX_RESTARTS and not voice_activity.in_turn()
                        and voice_activity.busy()):
                    interrupted = True
                    break
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    # The step's own cancellation is swallowed; the caller's
                    # (this task being cancelled) goes on up.
                    me = asyncio.current_task()
                    if me is not None and me.cancelling():
                        raise
                except Exception:  # noqa: BLE001 — cancelled on purpose
                    pass
            elif not returned and not task.cancelled() and task.exception() is None:
                await _close_unread(task.result())
        if interrupted:
            restarts += 1
            log.info("%s: a voice turn started; it stops and runs again after the turn (%d of %d)",
                     label, restarts, CALL_MAX_RESTARTS)


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
        the job is marked waiting for voice meanwhile. Raises
        :attr:`stopped` when the person pressed stop, a quiet house too."""
        if await self._stop_requested(job):
            raise self.stopped()
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

    def pass_hold(self, job: Any) -> Callable[[Any], Awaitable[None]]:
        """The ``hold`` for a helper program run for ``job``: held still while
        a voice turn runs (:meth:`hold_while_voice`), and about once a second
        a look at stop, which raises :attr:`stopped` (the program is then
        ended: :func:`domovoi.chat_video.media._run`)."""
        last = time.monotonic()

        async def hold(proc: Any) -> None:
            nonlocal last
            await self.hold_while_voice(proc)
            now = time.monotonic()
            if now - last >= STOP_CHECK_SEC:
                last = now
                if await self._stop_requested(job):
                    raise self.stopped()

        return hold

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
