"""Settings > Wake words > "Hear the training voices".

A few clips of a phrase spoken by the SAME synthetic voices the wake-word
trainer makes its positive examples with: the trainer image's own
piper-sample-generator (its multi-speaker LibriTTS model), run through the
repo's wrapper (``scripts/wake_word/train_wake_word.py --preview``) and the
image's ``entrypoint.py --preview``. The household types a name, hears how
those voices say it, and respells it ("alex sander") until they say it
right, before a training spends twenty minutes or more on it.

Never another voice: no Domovoi Piper voice, no fallback. When training
isn't turned on (no trainer image) or the configured command isn't the
repo's wrapper, a preview is refused with what it needs, and the dashboard
hides the control behind that sentence.

One preview at a time, in the background; the page polls :func:`status`.
The clips land in ``~/.domovoi/wake_trainer/preview/<job>/`` (the web
process serves them from there, as it serves recorded clips); an older
job's folder is removed when the next one starts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from domovoi.config import settings

log = logging.getLogger(__name__)

COUNT = 4
# The wrapper's own limit (a cold container start, the model load and four
# clips take well under a minute on four CPU cores; a busy box gets more).
TIMEOUT_SEC = 300
_JOB_RE = re.compile(r"^[0-9a-f]{12}$")
_CLIP_RE = re.compile(r"^preview_[1-8]\.wav$")

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {}
_TASK: asyncio.Task | None = None
_CANCEL = threading.Event()


class Refused(Exception):
    """The preview can't start; ``str(e)`` says why (an HTTP 409)."""


def preview_root() -> Path:
    from domovoi import wake_trainer_setup

    return wake_trainer_setup.state_dir() / "preview"


def job_dir(job: str) -> Path | None:
    """A job's folder, or None for an id that isn't one."""
    if not _JOB_RE.match(job or ""):
        return None
    return preview_root() / job


def clip_path(job: str, name: str) -> Path | None:
    """A preview clip's file, path-guarded (None for anything else)."""
    d = job_dir(job)
    if d is None or not _CLIP_RE.match(name or ""):
        return None
    return d / name


def _blank() -> dict[str, Any]:
    return {"state": "idle", "job": None, "phrase": None, "started_at": None,
            "elapsed_sec": 0, "clips": [], "spoken_as": None, "error": None,
            "last_line": None}


def reset_for_tests() -> None:
    global _TASK
    with _LOCK:
        _STATE.clear()
        _STATE.update(_blank())
    _TASK = None
    _CANCEL.clear()


reset_for_tests()


def running() -> bool:
    return _TASK is not None and not _TASK.done()


async def status() -> dict[str, Any]:
    """What the page shows: the current (or last) preview."""
    with _LOCK:
        st = dict(_STATE)
        st["clips"] = list(st.get("clips") or [])
    if st.get("state") == "running" and st.get("_t0"):
        st["elapsed_sec"] = int(time.monotonic() - st["_t0"])
    st.pop("_t0", None)
    st["available"], st["unavailable_reason"] = await available()
    return st


async def available() -> tuple[bool, str | None]:
    """Can the training voices speak here? (False, why) when not: training
    off, a hand-written training command, or a trainer image built from an
    older trainer (one without the preview mode; an update rebuilds it)."""
    from domovoi import wake_trainer_setup

    if not wake_trainer_setup.enabled():
        return False, ("Turn on wake-word training above to hear the training voices: "
                       "they live in the trainer.")
    if preview_argv("x", Path("x")) is None:
        return False, ("The training command isn't Domovoi's own trainer, so its voices "
                       "can't be played here.")
    try:
        # Cached by the setup module (and kept while a training runs).
        image = (await wake_trainer_setup.requirements()).get("image") or {}
        src = await asyncio.to_thread(wake_trainer_setup.source_tree_id)
    except Exception as e:  # noqa: BLE001 - can't tell: let the run say
        log.debug("wake preview: no image check (%s)", e)
        return True, None
    if src != "unknown" and image and not image.get("current", True):
        if not image.get("present"):
            return False, ("The trainer image is missing. Turn wake-word training off and "
                           "on again to build it.")
        return False, ("The trainer image is from an older version of Domovoi and can't play "
                       "its voices yet. The next update rebuilds it (so does turning training "
                       "off and on again).")
    return True, None


def preview_argv(phrase: str, out: Path) -> list[str] | None:
    """``[python, …/train_wake_word.py, --preview, …]`` from the configured
    command, or None when it isn't the repo's wrapper."""
    from domovoi import wake_trainer_setup

    stop = wake_trainer_setup.wrapper_stop_argv(settings.wake_word_train_command or "")
    if not stop:
        return None
    return [*stop[:-1], "--preview", "--phrase", phrase, "--out", str(out),
            "--count", str(COUNT)]


def _update(**kw: Any) -> None:
    with _LOCK:
        _STATE.update(kw)


def _clean_old(keep: str | None) -> None:
    root = preview_root()
    if not root.is_dir():
        return
    for d in root.iterdir():
        if d.is_dir() and d.name != keep and _JOB_RE.match(d.name):
            shutil.rmtree(d, ignore_errors=True)


async def start(phrase: str) -> dict[str, Any]:
    """Start a preview of ``phrase`` (already bound to a speakable phrase by
    the caller). Refused when the voices aren't available here or a preview
    is already running."""
    global _TASK
    from domovoi import wake_trainer_setup
    from domovoi.workers import wake_word_trainer as wwt

    ok, why = await available()
    if not ok:
        raise Refused(why)
    if not wwt.phrase_is_speakable(phrase):
        raise Refused("a phrase is letters, digits, spaces and , . ' - only")
    if wake_trainer_setup.job_running():
        raise Refused("Wake-word training is being set up; try again when it finishes.")
    with _LOCK:
        starting = _STATE.get("state") == "running"   # set below, before any await
    if running() or starting:
        raise Refused("The training voices are already speaking a phrase; one at a time.")
    job = secrets.token_hex(6)
    out = preview_root() / job
    argv = preview_argv(phrase, out)
    if argv is None:   # the command changed between the two reads
        raise Refused("The training command isn't Domovoi's own trainer.")
    _CANCEL.clear()
    fresh = _blank()
    fresh.update(state="running", job=job, phrase=phrase,
                 started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 _t0=time.monotonic(), last_line="starting the trainer's container")
    with _LOCK:
        _STATE.clear()
        _STATE.update(fresh)
    try:
        # The household's pronunciations, as a training of this phrase
        # would get them ("Domovoi" as DOH-moe-voy).
        synth = await asyncio.to_thread(wwt.synthesis_ipa, phrase)
        env = wwt.trainer_env(TIMEOUT_SEC + 60, "", synth)
        env.pop("WAKE_TRAINER_REQUIRE_GPU", None)
        _TASK = asyncio.get_running_loop().create_task(
            asyncio.to_thread(_run, job, out, argv, env), name=f"wake-preview-{job}")
    except BaseException as e:
        _update(state="failed", error=f"the preview didn't start: {e}")
        raise
    return await status()


def _run(job: str, out: Path, argv: list[str], env: dict[str, str]) -> None:
    """The preview, in a worker thread: run the wrapper, then read what it
    wrote."""
    from domovoi.workers import wake_word_trainer as wwt

    def on_line(line: str) -> None:
        # The wrapper's and the entrypoint's own lines are the progress.
        if line.startswith(("[entrypoint]", "[wake-trainer]")):
            _update(last_line=line.split("] ", 1)[-1][:200])

    try:
        _clean_old(keep=None)
        out.mkdir(parents=True, exist_ok=True)
        # The wrapper's --stop-preview removes the container (on Windows no
        # signal reaches a console-less docker client).
        stop = [*argv[:argv.index("--preview")], "--stop-preview"]
        run = wwt.run_train_command(argv, env=env, timeout=TIMEOUT_SEC + 60, cancel=_CANCEL,
                                    stop_argv=stop, on_line=on_line)
    except Exception as e:  # noqa: BLE001 - the page shows it
        log.warning("wake preview: %s", e)
        _update(state="failed", error=f"the preview didn't run: {e}")
        return
    if run.timed_out:
        _update(state="failed", error=f"the training voices took over {TIMEOUT_SEC + 60} s "
                                      "and were stopped")
        return
    if run.cancelled:
        _update(state="failed", error="stopped: Domovoi is shutting down")
        return
    if run.returncode != 0:
        _update(state="failed", error=wwt.error_tail(run.output, 600)
                or f"the trainer exited {run.returncode}")
        return
    clips = sorted(p.name for p in out.glob("preview_*.wav") if _CLIP_RE.match(p.name))
    spoken = None
    try:
        spoken = json.loads((out / "preview.json").read_text(encoding="utf-8")).get("spoken_as")
    except (OSError, ValueError, AttributeError):
        pass
    if not clips:
        _update(state="failed", error="the trainer finished but wrote no clips")
        return
    _update(state="done", clips=clips, spoken_as=spoken, last_line=None)


async def shutdown() -> None:
    """The core is stopping: stop a running preview."""
    _CANCEL.set()
    task = _TASK
    if task is not None and not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=40)
        except BaseException:  # noqa: BLE001 - shutting down regardless
            pass
