"""Custom wake-word trainer worker (Feature 5).

Drains the ``training`` queue head of the ``wake_words`` table and turns
each one's recorded positive clips into a trained openWakeWord ``<slug>.onnx``.

WHY THIS WORKER NEVER TRAINS IN-PROCESS
=======================================
openWakeWord's *automatic* training pipeline depends on
``piper-sample-generator``, which is **Linux-only** — it does not run on native
Windows. The Domovoi server host is Windows 11. So this worker does NOT
import openWakeWord and train inside the event loop. Instead it shells out to an
**operator-configured external command** (``settings.wake_word_train_command``)
that points at a WSL2 / docker-Linux / Colab pipeline. The command template's
placeholders ``{clips_dir} {phrase} {slug} {out}`` are substituted, the command
is split with :func:`shlex.split`, and it runs via
``asyncio.to_thread(subprocess.run, ...)`` so the blocking shell-out never
stalls the event loop. On a clean exit the worker expects the model to have
materialized at ``<wake_models_dir>/<slug>.onnx``.

GATING (off by default)
=======================
The whole worker is gated OFF by ``settings.wake_word_trainer_enabled`` (default
``False``) — ``main.py`` doesn't even start it unless the flag is on (and stubs
are off). Even when started, an EMPTY ``wake_word_train_command`` (the default)
disables actual training: a queued row is :meth:`mark_failed` with a runbook
pointer rather than silently hanging in ``training`` forever. This mirrors the
``radio_sdr_enabled`` "off until the external toolchain is present" pattern.

See ``scripts/wake_word/README.md`` for the operator runbook (the canonical
openWakeWord automatic-training recipe and an example WSL2 invocation).

Lifecycle (start/stop/_run/tick) follows the standard core worker
template (see :class:`domovoi.workers.timer_watcher.TimerWatcher`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import subprocess
from pathlib import Path

from sqlalchemy import text

from domovoi import wake_clip_quality
from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

# Hard ceiling on a single train shell-out. openWakeWord automatic training
# runs ~30–60 min on CPU (the README's own estimate), so we allow a generous
# hour before declaring the external command wedged. Bounded so a hung WSL2 /
# docker invocation can't leak a subprocess forever.
_TRAIN_TIMEOUT_SEC = 3600

# Sentinel runbook message stored on a row when training can't proceed because
# no external command is configured. Surfaced verbatim on the Wake Words tab.
_NO_COMMAND_MSG = (
    "wake_word_train_command not configured; openWakeWord training is "
    "Linux-only, see scripts/wake_word/README.md"
)


class WakeWordTrainer(Worker):
    """Background task that trains queued custom wake words.

    ``tick()`` claims the oldest ``training`` wake word and either marks it
    failed (no command / training error) or ready (model produced). It is safe
    to call even when the trainer is disabled — ``main.py`` gates *starting*
    the worker on ``settings.wake_word_trainer_enabled``, but ``tick`` also
    no-ops cleanly when there's nothing to train."""

    # Declarative registration (design §4.5): gated by
    # wake_word_trainer_enabled, suppressed under stubs so the suite never
    # shells out (tick() is unit-tested with a patched subprocess).
    name = "wake_word_trainer"
    enabled_setting = "wake_word_trainer_enabled"
    interval_setting = "wake_word_trainer_loop_sec"
    stub_suppressed = True

    async def tick(self) -> int:
        """Drain the training queue: keep claiming jobs until a pass
        processes nothing (drain-immediately
        behavior under the declarative runner). Returns how many wake
        words were processed this tick."""
        processed = 0
        while True:
            n = await self._train_one()
            processed += n
            if not n:
                return processed

    async def _train_one(self) -> int:
        """Train (or fail) the oldest queued wake word. Returns 1 if a row was
        processed, 0 if the queue was empty."""
        async with session_scope() as s:
            from domovoi.db.repositories import WakeWordsRepository

            repo = WakeWordsRepository(s)
            row = await repo.next_training()
            if row is None:
                return 0

        wake_word_id = int(row["id"])
        slug = row["slug"]
        phrase = row["phrase"]

        cmd_template = (settings.wake_word_train_command or "").strip()
        if not cmd_template:
            # Training is gated/unconfigured — fail loudly with the runbook so
            # the row doesn't sit in 'training' forever and the dashboard shows
            # actionable guidance rather than a silent stall.
            await self._mark_failed(wake_word_id, _NO_COMMAND_MSG)
            return 1

        # Hand the trainer ONLY the clips the operator selected. stage_selected
        # rebuilds <slug>/.training/ with the selected raw clips (re-indexed);
        # if nothing is selected / sidecars predate this feature (count 0), fall
        # back to the raw slug dir so a queued word still trains on what's there.
        raw_dir = Path(settings.wake_clips_dir) / slug
        try:
            staged, n_selected = await asyncio.to_thread(
                wake_clip_quality.stage_selected_clips, raw_dir
            )
            clips_dir = staged if n_selected > 0 else raw_dir
            log.info(
                "wake-word trainer: slug=%s staged %d selected clip(s) → %s",
                slug, n_selected, clips_dir,
            )
        except Exception as e:
            log.warning(
                "wake-word trainer: clip staging failed for slug=%s (%s); "
                "training on the raw clip dir", slug, e,
            )
            clips_dir = raw_dir
        out_path = Path(settings.wake_models_dir) / f"{slug}.onnx"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            cmd = cmd_template.format(
                clips_dir=str(clips_dir),
                phrase=phrase,
                slug=slug,
                out=str(out_path),
            )
            # posix=False on Windows so the substituted Windows paths (the
            # clips/out dirs live under ~/.domovoi) keep their backslashes —
            # POSIX mode treats "\" as an escape and would mangle "C:\Users\…"
            # into "C:UsersKamron…". Domovoi is the Windows host that shells out
            # (e.g. to a `wsl …` / `docker …` wrapper), so Windows-style
            # tokenizing is correct here.
            argv = shlex.split(cmd, posix=(os.name != "nt"))
        except (KeyError, IndexError, ValueError) as e:
            await self._mark_failed(
                wake_word_id, f"bad wake_word_train_command template: {e}"
            )
            return 1

        log.info("wake-word trainer: training slug=%s via %r", slug, argv)
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                argv,
                capture_output=True,
                text=True,
                timeout=_TRAIN_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            await self._mark_failed(
                wake_word_id,
                f"train command timed out after {_TRAIN_TIMEOUT_SEC}s",
            )
            return 1
        except (OSError, ValueError) as e:
            await self._mark_failed(wake_word_id, f"train command failed to run: {e}")
            return 1

        if proc.returncode == 0 and out_path.is_file():
            # model_ref is the slug — the served <slug>.onnx, the Pi's
            # effective wake word, and the openWakeWord prediction-dict key all
            # equal it (the load-bearing slug invariant).
            await self._mark_ready(wake_word_id, slug)
            log.info("wake-word trainer: slug=%s trained → %s", slug, out_path)
        else:
            # Surface the tail of stderr (or stdout) so the dashboard shows
            # something diagnosable without exposing the full multi-MB log.
            tail = (proc.stderr or proc.stdout or "").strip()[-1000:]
            detail = (
                f"train command exited {proc.returncode}"
                + (f": {tail}" if tail else "")
            )
            if proc.returncode == 0:
                detail = (
                    f"train command exited 0 but {out_path.name} was not "
                    "produced"
                )
            await self._mark_failed(wake_word_id, detail)
            log.warning("wake-word trainer: slug=%s failed: %s", slug, detail)
        return 1

    async def _mark_ready(self, wake_word_id: int, model_ref: str) -> None:
        async with session_scope() as s:
            from domovoi.db.repositories import WakeWordsRepository

            await WakeWordsRepository(s).mark_ready(wake_word_id, model_ref)
            # Same session as the status flip so the dashboard's LISTEN task
            # sees 'ready' on the post-COMMIT delivery.
            await s.execute(
                text("SELECT pg_notify('wake_words_changed', :p)"),
                {"p": str(wake_word_id)},
            )

    async def _mark_failed(self, wake_word_id: int, error: str) -> None:
        async with session_scope() as s:
            from domovoi.db.repositories import WakeWordsRepository

            await WakeWordsRepository(s).mark_failed(wake_word_id, error)
            await s.execute(
                text("SELECT pg_notify('wake_words_changed', :p)"),
                {"p": str(wake_word_id)},
            )
