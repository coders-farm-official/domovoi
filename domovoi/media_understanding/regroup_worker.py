"""The nightly face re-group's maths in a short-lived child process whose
maths library runs on one thread (security review H4).

numpy's BLAS (OpenBLAS, MKL or Accelerate) starts a thread per CPU core
the first time numpy loads, and the re-group's block products would use
them all, starving voice turns. The only reliable way to hold it to one
thread without a helper package is to set its thread-count variables
before numpy loads, which in the core itself is too late (and would also
reach every other library in the process). So :func:`run` starts
``python -I`` with :data:`ONE_THREAD_ENV` set, at lowered priority, and
hands it the re-group's input; the child runs
:func:`domovoi.media_understanding.faces.plan_regroup` and answers with
plain JSON (ids and labels, never embeddings: the parent rebuilds the rest
from what it already holds).

Voice still comes first: while ``pause()`` says yes, the parent keeps a
flag file in a private temporary folder, and the child sleeps between
blocks while it exists. The child is killed after
:data:`MAX_SEC`, or when anything else goes wrong in the parent.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Set in the child before numpy loads: every BLAS numpy may be built
#: against (OpenBLAS, OpenMP builds, MKL, macOS Accelerate) on one thread.
ONE_THREAD_ENV = {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
}
#: The longest one night's re-group may run (voice pauses included).
MAX_SEC = 3600.0
#: How often the parent looks at ``pause()`` and the child.
POLL_SEC = 0.25
_REPO = str(Path(__file__).resolve().parents[2])
_CHILD_CODE = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from domovoi.media_understanding.regroup_worker import _child; _child()"
)


class RegroupWorkerFailed(RuntimeError):
    """The child exited badly, answered nonsense or ran past :data:`MAX_SEC`."""


def child_env() -> dict[str, str]:
    """The child's environment: the core's, with :data:`ONE_THREAD_ENV` on top."""
    env = dict(os.environ)
    env.update(ONE_THREAD_ENV)
    return env


def _os_threads() -> int | None:
    """The process's OS threads (Linux), else None."""
    try:
        return len(os.listdir("/proc/self/task"))
    except OSError:
        return None


def _lower_priority() -> None:
    if sys.platform == "win32":
        return
    try:
        os.nice(10)
    except Exception:  # noqa: BLE001 - best effort
        pass


def _child() -> None:
    """The child's whole life: the input pickled on stdin (from the parent,
    which started it), the plan as JSON on stdout."""
    _lower_priority()
    out = sys.stdout
    sys.stdout = sys.stderr                    # nothing but the answer on stdout
    payload = pickle.loads(sys.stdin.buffer.read())
    from domovoi.media_understanding import faces

    flag = payload.get("pause_flag")
    pause = (lambda: os.path.exists(flag)) if flag else None
    plan = faces.plan_regroup(payload["rows"], payload["deny"], payload["named"], pause=pause)
    answer = {
        "gone": [int(f) for f in plan.gone],
        "suggest": [[int(f), int(g), int(p)] for f, g, p in plan.suggest],
        "free": [int(f) for f, _v, _g in plan.free],
        "labels": [int(x) for x in plan.labels.tolist()],
        "threads": {
            "env": {k: os.environ.get(k) for k in ONE_THREAD_ENV},
            "os": _os_threads(),
        },
    }
    out.write(json.dumps(answer))
    out.flush()


#: What the last child said about its threads (logged; the tests read it).
LAST_THREADS: dict[str, Any] = {}


def run(rows: list[Any], deny: Any, named: list[Any], *, pause: Any = None,
        timeout: float = MAX_SEC) -> dict[str, Any]:
    """Blocking (call it in a worker thread): the child's answer, a dict of
    ``gone``, ``suggest``, ``free`` (face ids in order) and ``labels``.
    :class:`RegroupWorkerFailed` when it can't give one."""
    import time

    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        [sys.executable, "-I", "-c", _CHILD_CODE, _REPO],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=child_env(), creationflags=flags, close_fds=True,
    )
    deadline = time.monotonic() + timeout
    paused = False
    flag_dir = flag = None
    try:
        if pause is not None:
            flag_dir = tempfile.mkdtemp(prefix="domovoi-regroup-")
            flag = os.path.join(flag_dir, "pause")
        send: bytes | None = pickle.dumps(
            {"rows": rows, "deny": deny, "named": named, "pause_flag": flag},
            protocol=pickle.HIGHEST_PROTOCOL)
        while True:
            if flag is not None:
                want = bool(pause())
                if want != paused:
                    if want:
                        Path(flag).touch()
                    else:
                        Path(flag).unlink(missing_ok=True)
                    paused = want
            try:
                out, err = proc.communicate(send, timeout=POLL_SEC)
                break
            except subprocess.TimeoutExpired:
                send = None
                if time.monotonic() > deadline:
                    raise RegroupWorkerFailed(f"the face re-group ran past {timeout:g} s") from None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
        if flag_dir is not None:
            shutil.rmtree(flag_dir, ignore_errors=True)
    if proc.returncode != 0:
        tail = (err or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        raise RegroupWorkerFailed(f"the face re-group exited {proc.returncode}: {tail[0][:300]}")
    try:
        answer = json.loads(out.decode("utf-8"))
        threads = answer.get("threads") or {}
        LAST_THREADS.clear()
        LAST_THREADS.update(threads)
        log.debug("face re-group child threads: %s", threads)
        return answer
    except (ValueError, AttributeError) as e:
        raise RegroupWorkerFailed("the face re-group answered nonsense") from e


__all__ = ["MAX_SEC", "ONE_THREAD_ENV", "RegroupWorkerFailed", "child_env", "run"]
