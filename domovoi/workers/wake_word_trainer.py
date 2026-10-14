"""Custom wake-word trainer worker (Feature 5).

Drains the ``training`` queue head of the ``wake_words`` table and turns
each one's recorded positive clips into a trained openWakeWord ``<slug>.onnx``.

WHY THIS WORKER NEVER TRAINS IN-PROCESS
=======================================
openWakeWord's *automatic* training pipeline depends on
``piper-sample-generator``, which is **Linux-only**. So this worker does NOT
import openWakeWord and train inside the event loop. Instead it shells out to
the **configured command** (``settings.wake_word_train_command``), which
Settings → Wake Words → "Turn on wake-word training" sets to the repo's own
wrapper (``scripts/wake_word/train_wake_word.py``): it runs the training in
a Linux container, on a GPU when Docker can reach one. The command
template's placeholders ``{clips_dir} {phrase} {slug} {out}`` are
substituted, the command is split with :func:`shlex.split`, and it runs in
a worker thread so the blocking shell-out never stalls the event loop. On a
clean exit the worker expects the model to have materialized at
``<wake_models_dir>/<slug>.onnx``.

What the command gets beside its argv (environment):

* the trainer's knobs from the settings (``WAKE_TRAINER_IMAGE``, samples,
  steps, CPUs, memory, …; Settings → Configuration → Wake words) and any
  other ``WAKE_TRAINER_*`` line in ``domovoi/.env``, so none of them needs
  a shell or a systemd drop-in any more;
* ``WAKE_TRAINER_DEADLINE_SEC``, a minute under this worker's own time
  limit, so the wrapper removes its container before the worker gives up;
* ``WAKE_PRONUNCIATIONS``: how the phrase's words are said, from Domovoi's
  own phonemizer (the household's "say it like" list first), for the
  words the dictionary lacks.

ONE AT A TIME, AND NOTHING LEFT BEHIND
======================================
Words train one after another (one tick drains the queue serially), never
while Settings is setting training up. When the time limit passes, or the
core stops while a word trains, the command is asked to stop (SIGTERM to
its process group, a kill after a grace period) and, when it is the repo's
wrapper, ``train_wake_word.py --stop`` removes the training container
(``docker run`` alone leaves it running). The first tick after a start
does the same for a container a crashed core left behind. A word cut off
by a shutdown stays queued and trains again after the restart; one that
ran out of time is marked failed with the limit in the message.

GATING (off by default)
=======================
The worker is gated OFF by ``settings.wake_word_trainer_enabled`` (default
``False``): it is not started at boot while that is off, and a tick trains
nothing once it has been switched off. "Turn on wake-word training" starts
it without a restart. Even when started, an EMPTY ``wake_word_train_command``
fails a queued row with a pointer to Settings → Wake Words rather than
leaving it in ``training`` forever.

Lifecycle (start/stop/_run/tick) follows the standard core worker
template (see :class:`domovoi.workers.timer_watcher.TimerWatcher`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from domovoi import egress, lifecycle, wake_clip_quality
from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.models import WAKE_PHRASE_PATTERN
from domovoi.workers.base import Worker

# Compiled once; every queued row is checked against it before its
# phrase becomes an argument to the operator's training command.
_PHRASE_RE = re.compile(WAKE_PHRASE_PATTERN)


def phrase_is_speakable(phrase: str | None) -> bool:
    """Is this a phrase someone could say out loud — letters, digits,
    spaces and the punctuation that appears inside a spoken name?

    The bound exists because the phrase becomes an ARGUMENT to
    ``wake_word_train_command``, which the README suggests writing as a
    ``docker run …`` / ``wsl …`` wrapper.
    """
    return bool(_PHRASE_RE.match(phrase or ""))


def build_train_argv(cmd_template: str, **values: str) -> list[str]:
    """Turn the operator's command TEMPLATE into an argv list.

    The template is split into tokens FIRST and each placeholder is then
    substituted inside a single token, so a value is always exactly one
    element of argv. Formatting the whole string and splitting afterwards
    — which is what this used to do — let a value containing a quote or a
    space decide how many arguments the command received, and where they
    landed relative to, say, an image name.

    posix=False on Windows so the substituted Windows paths (the clips /
    out dirs live under ~/.domovoi) keep their backslashes: POSIX mode
    treats "\\" as an escape and would mangle "C:\\Users\\…" into
    "C:UsersKamron…". In that mode shlex keeps a token's surrounding double
    quotes, so they are taken off here (the template "Turn on" writes
    quotes every path).

    Raises ``ValueError`` for an unparseable template and
    ``KeyError`` / ``IndexError`` / ``ValueError`` for an unknown or
    malformed placeholder — the caller turns those into a failed row.
    """
    tokens = shlex.split(cmd_template, posix=(os.name != "nt"))
    if os.name == "nt":
        tokens = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] == '"' else t for t in tokens]
    return [token.format(**values) for token in tokens]

log = logging.getLogger(__name__)

# How long the command gets to stop after SIGTERM before it is killed (the
# wrapper removes its container in a few seconds).
_STOP_GRACE_SEC = 30.0
# The most output kept for the row's error and the log file's tail.
_OUTPUT_TAIL_LINES = 400

# Stored on a row when training can't proceed because no command is
# configured. Surfaced verbatim on the Wake Words tab.
_NO_COMMAND_MSG = (
    "wake-word training is not turned on: Settings > Wake Words > "
    "\"Turn on wake-word training\" (wake_word_train_command is empty; "
    "see scripts/wake_word/README.md)"
)

# Stored on a queued row when the internet answer is ``never``: the training
# recipe downloads its training data (several GB) the first time it runs, so
# the command is not started at all. User-started, so a visible, retryable
# failure is the right outcome (CONTRACT 5.12); queue it again once online.
_INTERNET_OFF_MSG = (
    "training needs the internet the first time it runs (it downloads its "
    "training data); " + egress.TURNED_OFF_REASON
)

# A training is running in this process (read by the setup job and the
# dashboard's status).
_ACTIVE = threading.Event()
# The orphan sweep runs once per process, at the first tick.
_SWEPT = False


def training_active() -> bool:
    return _ACTIVE.is_set()


@dataclass
class TrainRun:
    """What one run of the command came to."""

    returncode: int | None      # None: it was stopped (time limit / shutdown)
    output: str                 # the tail of its stdout+stderr
    timed_out: bool = False
    cancelled: bool = False


def _popen_kwargs() -> dict:
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def _ask_to_stop(proc: subprocess.Popen) -> None:
    """SIGTERM to the command's process group (the wrapper removes its
    container on it); CTRL_BREAK on Windows."""
    try:
        if os.name == "nt":
            proc.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass


def _kill(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ValueError):
        pass


def _run_stop(stop_argv: list[str], env: dict[str, str]) -> None:
    """The wrapper's ``--stop``: remove whatever training container is left."""
    try:
        subprocess.run(stop_argv, env=env, capture_output=True, text=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("wake-word trainer: %s failed: %s", " ".join(stop_argv), e)


def run_train_command(argv: list[str], *, env: dict[str, str], timeout: float,
                      cancel: threading.Event, log_path: Path | None = None,
                      stop_argv: list[str] | None = None) -> TrainRun:
    """Run the training command to the end, the time limit, or ``cancel``
    (blocking; the worker runs it in a thread). Its output goes to
    ``log_path`` (whole) and the returned tail. When it has to be stopped:
    SIGTERM, ``_STOP_GRACE_SEC``, a kill, then ``stop_argv`` (the wrapper's
    ``--stop``) for anything it left running."""
    tail: deque[str] = deque(maxlen=_OUTPUT_TAIL_LINES)
    logf = None
    if log_path is not None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            logf = open(log_path, "w", encoding="utf-8", errors="replace")
        except OSError as e:
            log.warning("wake-word trainer: no log file %s (%s)", log_path, e)
    proc = subprocess.Popen(
        argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
        **_popen_kwargs(),
    )

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            tail.append(line.rstrip("\r\n")[-2000:])
            if logf is not None:
                try:
                    logf.write(line)
                    logf.flush()
                except OSError:
                    pass

    reader = threading.Thread(target=pump, name="wake-train-output", daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    timed_out = cancelled = False
    try:
        while True:
            try:
                proc.wait(timeout=1.0)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel.is_set():
                cancelled = True
            elif time.monotonic() >= deadline:
                timed_out = True
            if timed_out or cancelled:
                _ask_to_stop(proc)
                if os.name == "nt" and stop_argv:
                    # No signal reaches a console-less child on Windows:
                    # remove the container first, which ends `docker run`.
                    _run_stop(stop_argv, env)
                try:
                    proc.wait(timeout=_STOP_GRACE_SEC)
                except subprocess.TimeoutExpired:
                    _kill(proc)
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                break
        reader.join(timeout=10)
        if (timed_out or cancelled) and stop_argv:
            _run_stop(stop_argv, env)
    finally:
        if logf is not None:
            try:
                logf.close()
            except OSError:
                pass
    return TrainRun(
        returncode=None if (timed_out or cancelled) else proc.returncode,
        output="\n".join(tail),
        timed_out=timed_out,
        cancelled=cancelled,
    )


# Settings → the wrapper's environment (0 / "" = leave the wrapper's default).
_KNOBS = {
    "WAKE_TRAINER_IMAGE": "wake_trainer_image",
    "WAKE_TRAINER_CACHE_VOLUME": "wake_trainer_cache_volume",
    "WAKE_TRAINER_N_SAMPLES": "wake_trainer_n_samples",
    "WAKE_TRAINER_N_SAMPLES_VAL": "wake_trainer_n_samples_val",
    "WAKE_TRAINER_STEPS": "wake_trainer_steps",
    "WAKE_TRAINER_REAL_TARGET_FRACTION": "wake_trainer_real_target_fraction",
    "WAKE_TRAINER_CPUS": "wake_trainer_cpus",
}


def _env_file_trainer_keys() -> dict[str, str]:
    """``WAKE_TRAINER_*`` lines of domovoi/.env that are not settings (the
    wrapper's other knobs: shm size, CPU set, docker binary, …)."""
    try:
        from dotenv import dotenv_values

        from domovoi.config_env_writer import _ENV_FILE

        values = dotenv_values(_ENV_FILE)
    except Exception:  # noqa: BLE001 - no .env, or unreadable: nothing extra
        return {}
    return {k.upper(): str(v) for k, v in values.items()
            if v is not None and k.upper().startswith("WAKE_TRAINER_")}


def trainer_env(timeout: int, pronunciations: str = "") -> dict[str, str]:
    """The command's environment: the core's own, the trainer knobs from
    the settings and ``.env``, the wrapper's deadline and the phrase's
    pronunciations. The process environment wins over ``.env`` (as it does
    for the settings themselves)."""
    env = dict(os.environ)
    for key, value in _env_file_trainer_keys().items():
        env.setdefault(key, value)
    for key, field in _KNOBS.items():
        value = getattr(settings, field, None)
        if isinstance(value, bool) or value in (None, "", 0, 0.0):
            env.pop(key, None)
            continue
        env[key] = f"{value:g}" if isinstance(value, float) else str(value)
    mem = int(getattr(settings, "wake_trainer_memory_gb", 0) or 0)
    if mem > 0:
        env["WAKE_TRAINER_MEMORY"] = f"{mem}g"
    env["WAKE_TRAINER_DEADLINE_SEC"] = str(max(60, int(timeout) - 60))
    if pronunciations:
        env["WAKE_PRONUNCIATIONS"] = pronunciations
    else:
        env.pop("WAKE_PRONUNCIATIONS", None)
    return env


_TOKEN_RE = re.compile(r"^[a-z0-9'.-]+$")


def phrase_pronunciations(phrase: str) -> str:
    """``word=PHONES;…`` for the phrase's words that Domovoi's own
    phonemizer (MIT; ``domovoi/phonemizer/``) can say better than the
    trainer's dictionary: every word on the "say it like" list (the
    household's, and the defaults, "Domovoi = DOH-moe-voy" among them),
    and every word its dictionary lacks (its own model's guess). The
    trainer uses these, not espeak-ng's, to build near-miss phrases.
    "" when the phonemizer can't load."""
    try:
        from domovoi.phonemizer import get_phonemizer, pronunciation_key

        ph = get_phonemizer()
        listed = ph.pronunciations()
    except Exception as e:  # noqa: BLE001 - the trainer falls back to espeak-ng
        log.debug("wake-word trainer: no phonemizer (%s)", e)
        return ""
    out: list[str] = []
    for token in dict.fromkeys(w.lower() for w in (phrase or "").split()):
        if not _TOKEN_RE.match(token):
            continue
        phones: list[str] | None = None
        try:
            entry = listed.get(pronunciation_key(token))
        except Exception:  # noqa: BLE001 - not a word the list could hold
            entry = None
        if entry is not None and entry.phones:
            phones = list(entry.phones)
        else:
            try:
                arpa, source = ph.word_arpa(token)
            except Exception:  # noqa: BLE001
                continue
            if source in ("model", "compound", "hyphen"):
                phones = list(arpa)
        if phones:
            out.append(f"{token}={' '.join(phones)}")
    return ";".join(out)


def _log_dir() -> Path:
    return Path(settings.wake_models_dir).parent / "wake_trainer" / "logs"


# Exit codes of a command that was stopped from outside (SIGHUP, SIGINT,
# SIGTERM, as the wrapper reports them, or as Python's subprocess does).
_STOPPED_CODES = frozenset({129, 130, 143, -1, -2, -15})


def error_tail(output: str, limit: int = 1000) -> str:
    """What a failed run's row says: the wrapper's and the entrypoint's own
    lines and any error line, else the last plain lines. The generator's
    DEBUG chatter and progress bars are left in the log file."""
    lines = [ln.strip() for ln in (output or "").splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith(("DEBUG:", "INFO:"))
             and "it/s]" not in ln and "s/it]" not in ln]
    own = [ln for ln in lines if ln.startswith(("[wake-trainer]", "[entrypoint]"))
           or "FATAL" in ln or "Error" in ln or "error:" in ln]
    picked = own[-6:] if own else lines[-8:]
    return "\n".join(picked)[-limit:]


class WakeWordTrainer(Worker):
    """Background task that trains queued custom wake words.

    ``tick()`` claims the oldest ``training`` wake word and either marks it
    failed (no command / training error) or ready (model produced). It is safe
    to call even when the trainer is disabled — ``main.py`` gates *starting*
    the worker on ``settings.wake_word_trainer_enabled``, and ``tick`` also
    no-ops cleanly when it is off or there's nothing to train."""

    # Declarative registration (design §4.5): gated by
    # wake_word_trainer_enabled, suppressed under stubs so the suite never
    # shells out (tick() is unit-tested with a patched runner).
    name = "wake_word_trainer"
    enabled_setting = "wake_word_trainer_enabled"
    interval_setting = "wake_word_trainer_loop_sec"
    stub_suppressed = True

    async def tick(self) -> int:
        """Drain the training queue: keep claiming jobs until a pass
        processes nothing (drain-immediately
        behavior under the declarative runner). Returns how many wake
        words were processed this tick."""
        if not settings.wake_word_trainer_enabled:
            return 0
        from domovoi import wake_trainer_setup

        if wake_trainer_setup.job_running():
            return 0   # the image may be rebuilding under us
        await self._sweep_orphans()
        processed = 0
        while True:
            n = await self._train_one()
            processed += n
            if not n or not settings.wake_word_trainer_enabled:
                return processed

    async def _sweep_orphans(self) -> None:
        """Once per process: remove a training container a previous core
        left running (killed, crashed) before training anything."""
        global _SWEPT
        if _SWEPT:
            return
        _SWEPT = True
        from domovoi import wake_trainer_setup

        stop = wake_trainer_setup.wrapper_stop_argv(settings.wake_word_train_command or "")
        if not stop:
            return
        try:
            await asyncio.to_thread(
                subprocess.run, stop, env=trainer_env(60), capture_output=True,
                text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("wake-word trainer: leftover-container sweep failed: %s", e)

    async def _train_one(self) -> int:
        """Train (or fail) the oldest queued wake word. Returns 1 if a row was
        processed, 0 if the queue was empty (or the core is stopping)."""
        if lifecycle.shutting_down():
            return 0
        async with session_scope() as s:
            from domovoi.db.repositories import WakeWordsRepository

            repo = WakeWordsRepository(s)
            row = await repo.next_training()
            if row is None:
                return 0

        wake_word_id = int(row["id"])
        slug = row["slug"]
        phrase = row["phrase"]

        # CORE-10. The web route bounds the phrase on the way in; this is
        # the same check where it matters, because a row may predate that
        # bound or have arrived another way, and the next thing that
        # happens to `phrase` is that it becomes an argument to a command
        # the operator configured.
        if not phrase_is_speakable(phrase):
            await self._mark_failed(
                wake_word_id,
                "wake-word phrase must be letters, digits, spaces, "
                "or the punctuation , . ' -",
            )
            return 1

        cmd_template = (settings.wake_word_train_command or "").strip()
        if not cmd_template:
            # Training is gated/unconfigured — fail loudly with the pointer so
            # the row doesn't sit in 'training' forever and the dashboard shows
            # actionable guidance rather than a silent stall.
            await self._mark_failed(wake_word_id, _NO_COMMAND_MSG)
            return 1

        if egress.internet_turned_off():
            # No train command under never: it would fetch its data set.
            await self._mark_failed(wake_word_id, _INTERNET_OFF_MSG)
            log.info("wake-word trainer: slug=%s not trained: internet off", slug)
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
            argv = build_train_argv(
                cmd_template,
                clips_dir=str(clips_dir),
                phrase=phrase,
                slug=slug,
                out=str(out_path),
            )
        except (KeyError, IndexError, ValueError) as e:
            await self._mark_failed(
                wake_word_id, f"bad wake_word_train_command template: {e}"
            )
            return 1
        if not argv:
            await self._mark_failed(
                wake_word_id, "wake_word_train_command is empty after splitting"
            )
            return 1

        from domovoi import wake_trainer_setup

        timeout = wake_trainer_setup.train_timeout_sec()
        pron = await asyncio.to_thread(phrase_pronunciations, phrase)
        env = trainer_env(timeout, pron)
        stop_argv = wake_trainer_setup.wrapper_stop_argv(cmd_template)
        log_path = _log_dir() / f"{slug}.log"
        cancel = threading.Event()

        log.info("wake-word trainer: training slug=%s via %r (limit %d s)", slug, argv, timeout)
        _ACTIVE.set()
        try:
            fut = asyncio.ensure_future(asyncio.to_thread(
                run_train_command, argv, env=env, timeout=timeout, cancel=cancel,
                log_path=log_path, stop_argv=stop_argv,
            ))
            try:
                result = await asyncio.shield(fut)
            except asyncio.CancelledError:
                # The core is stopping: stop the command and its container
                # (the thread does it; give it what the shutdown allows),
                # and leave the row queued so it trains after the restart.
                cancel.set()
                log.warning("wake-word trainer: stopping the training of slug=%s "
                            "(Domovoi is shutting down); it trains again after the "
                            "restart", slug)
                try:
                    await asyncio.wait_for(fut, timeout=_STOP_GRACE_SEC + 5)
                except BaseException:  # noqa: BLE001 - shutting down regardless
                    pass
                raise
        except (OSError, ValueError) as e:
            await self._mark_failed(wake_word_id, f"train command failed to run: {e}")
            return 1
        finally:
            _ACTIVE.clear()

        if result.cancelled or result.returncode in _STOPPED_CODES:
            # systemd's SIGTERM reaches the wrapper too (one control group),
            # and it removes its container and exits a moment before the
            # worker's own cancel: a shutdown, not a failure. The word stays
            # queued and trains again after the restart.
            for _ in range(30):
                if lifecycle.shutting_down():
                    break
                await asyncio.sleep(0.1)
            if lifecycle.shutting_down():
                log.warning("wake-word trainer: slug=%s stopped because Domovoi is "
                            "shutting down; it trains again after the restart", slug)
                return 0

        if result.timed_out:
            await self._mark_failed(
                wake_word_id,
                f"train command timed out after {timeout}s and was stopped "
                "(Settings > Configuration > Wake words > Training time limit)",
            )
            log.warning("wake-word trainer: slug=%s timed out after %d s", slug, timeout)
            return 1

        if result.returncode == 0 and out_path.is_file():
            # model_ref is the slug — the served <slug>.onnx, the Pi's
            # effective wake word, and the openWakeWord prediction-dict key all
            # equal it (the load-bearing slug invariant).
            await self._mark_ready(wake_word_id, slug)
            log.info("wake-word trainer: slug=%s trained → %s", slug, out_path)
        else:
            # Surface the tail of the output so the dashboard shows
            # something diagnosable without exposing the full multi-MB log
            # (that one is in ~/.domovoi/wake_trainer/logs/<slug>.log).
            tail = error_tail(result.output)
            detail = (
                f"train command exited {result.returncode}"
                + (f": {tail}" if tail else "")
            )
            if result.returncode == 0:
                detail = (
                    f"train command exited 0 but {out_path.name} was not "
                    "produced"
                )
            await self._mark_failed(wake_word_id, detail)
            log.warning("wake-word trainer: slug=%s failed: %s", slug, detail[:300])
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
