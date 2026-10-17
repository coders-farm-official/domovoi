"""Settings → Wake Words → "Turn on wake-word training".

Training a wake word runs openWakeWord's recipe in a Linux container
(``scripts/wake_word/train_wake_word.py`` and ``scripts/wake_word/docker/``).
That needs a ~12 GB Docker image and ~18 GB of training data, so it is off
until a household asks for it, and this module is how they ask, from the
dashboard, with no shell:

1. :func:`status` says what training needs and what this server has: the
   disk (about 32 GB the first time), the one-time download, the memory
   (the standard tier's 24 GB), Docker, and whether training would run on
   a GPU (about 20 min a word) or the CPU (about 75 min on 8 cores, and
   the voice pipeline slower meanwhile). It also gives the reasons it
   would refuse, so the page never shows a button that can't work:
   internet access turned off, too little disk, too little memory, no
   Docker.
2. :func:`start` (an admin's yes; on a CPU-only server a second yes for the
   CPU) runs one background job as the core's own user, the service user:

   * ``image``: ``docker build`` of ``scripts/wake_word/docker`` (pinned
     base, commits and pip versions), labelled with the git tree id of that
     directory so ``scripts/linux/apply-update.sh`` rebuilds it after an
     update that changes it;
   * ``gpu``: a one-line torch probe with ``--gpus all``, when the host has
     an NVIDIA card. A card Docker can't reach stops the job and asks
     again, with the CPU estimate, rather than silently training on the
     CPU;
   * ``data``: the image's ``--warm`` mode downloads the datasets into the
     cache volume, so the first Train doesn't start with an 18 GB download;
   * ``settings``: ``WAKE_WORD_TRAINER_ENABLED=true`` and
     ``WAKE_WORD_TRAIN_COMMAND`` (the repo's wrapper, run by this venv's
     Python) go into ``domovoi/.env`` through the repo's ``.env`` writer and
     onto the live settings, and the trainer worker starts.

   Progress (stage, percent, a sentence) and a failure (what failed, the
   tail of its output) are kept in ``~/.domovoi/wake_trainer/setup.json``,
   so a reload, another browser or a restart still shows them.
3. :func:`auto_timeout_sec` sizes the worker's time limit to what the job
   found (``wake_word_train_timeout_sec`` = 0).

One job at a time, never while a word is training (the image would change
under it), and the worker trains nothing while a job runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from domovoi import egress, git_run
from domovoi.config import settings

log = logging.getLogger(__name__)

# ── Measured 2026-10-13 (design-notes/WAKE-TRAINING-SPIKE.md §2) ──────────
IMAGE_GB = 12.4            # the trainer image on disk
DATA_GB = 17.9             # the three datasets in the cache volume
PER_WORD_GB = 0.5          # one word's synthetic clips and features
HEADROOM_GB = 5.0          # what must still be free afterwards
# On the Windows install the free space of C: can't be seen from the
# distro: "Turn on" asks the person to confirm this much is free there.
WSL_CONFIRM_GB = 40
# The one-time download, measured in the 2026-10-13 end-to-end run: the
# 3.2 GB base image, the packages and models the build fetches, and the
# 17.9 GB of training data (about 22.5 GB came in over the network).
DOWNLOAD_GB = 22
GPU_MIN_PER_WORD = 20      # RTX 4070 Ti SUPER, warm cache
GPU_FIRST_MIN = 26         # the first word, with the download
CPU_MIN_PER_WORD_8 = 75    # 8 cores, warm cache
# The standard tier's line (scripts/linux/provision-manifest.conf,
# tier_standard_min_mem_mib): a "24 GB" box's MemTotal reads about
# 23.3 GiB. Training peaked at 9.3 GiB of its own memory (14.7 with
# cache): too much beside the floor tier's models.
MIN_MEM_MIB = 22528
# What the warm job's progress is measured against: the datasets plus the
# impulse responses and background clips the entrypoint also fetches.
DATA_BYTES = int(18.6e9)

LABEL_SRC = "org.domovoi.wake-trainer.src"
WARM_CONTAINER = "domovoi-wake-warm"
WARM_LABEL = "org.domovoi.wake-trainer-setup"

BUILD_TIMEOUT_SEC = 3600.0
PROBE_TIMEOUT_SEC = 300.0
WARM_TIMEOUT_SEC = 4 * 3600.0

# The job's stages and their share of the progress bar.
STAGES = ("image", "gpu", "data", "settings")
_WEIGHT = {"image": (0, 45), "gpu": (45, 50), "data": (50, 97), "settings": (97, 100)}

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_LOCK = threading.Lock()
_STATE: dict[str, Any] | None = None
_TASK: asyncio.Task | None = None
# A start (or a turn-off) between its first check and its job.
_STARTING = False
_CANCEL = threading.Event()
# Why the running job was told to stop ("" = the core is shutting down).
_CANCEL_REASON = ""
_REQ_CACHE: tuple[float, dict[str, Any]] | None = None
_REQ_TTL_SEC = 10.0


# ── paths ────────────────────────────────────────────────────────────────

def repo_dir() -> Path:
    return Path(settings.repo_dir)


def trainer_dir() -> Path:
    return repo_dir() / "scripts" / "wake_word"


def wrapper_path() -> Path:
    return trainer_dir() / "train_wake_word.py"


def state_dir() -> Path:
    """``~/.domovoi/wake_trainer`` (beside wake_models)."""
    return Path(settings.wake_models_dir).parent / "wake_trainer"


def state_file() -> Path:
    return state_dir() / "setup.json"


def default_train_command() -> str:
    """The command template "Turn on" writes: this venv's Python running the
    repo's wrapper. Double-quoted tokens, which build_train_argv splits the
    same way on POSIX and (quotes stripped) on Windows."""
    return (
        f'"{sys.executable}" "{wrapper_path()}" --clips "{{clips_dir}}" '
        f'--phrase "{{phrase}}" --slug "{{slug}}" --out "{{out}}"'
    )


def wrapper_stop_argv(cmd_template: str) -> list[str] | None:
    """``[python, …/train_wake_word.py, --stop]`` when the configured
    command runs the repo's wrapper, else None (a hand-written command is
    the operator's to stop)."""
    from domovoi.workers.wake_word_trainer import build_train_argv

    try:
        argv = build_train_argv(cmd_template, clips_dir="x", phrase="x", slug="x", out="x")
    except (KeyError, IndexError, ValueError):
        return None
    for i, tok in enumerate(argv):
        if tok.replace("\\", "/").endswith("scripts/wake_word/train_wake_word.py"):
            return [*argv[:i + 1], "--stop"]
    return None


# ── persisted state ──────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _blank() -> dict[str, Any]:
    return {
        "state": "idle",          # idle | running | failed | done
        "stage": None,
        "percent": 0,
        "message": "",
        "error": None,
        "log_tail": [],
        "needs_cpu_confirm": False,
        "started_at": None,
        "finished_at": None,
        "gpu": None,              # what the probe found: True / False / None (never probed)
        "gpu_name": None,
        "cpu_estimate_min": None,
        "image_src": None,
        "data_ready": False,
    }


def _load() -> dict[str, Any]:
    global _STATE
    if _STATE is None:
        st = _blank()
        try:
            st.update(json.loads(state_file().read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        if st.get("state") == "running" and (_TASK is None or _TASK.done()):
            # This process never ran it: the core stopped while it worked.
            st.update(state="failed", error="Interrupted: Domovoi restarted while "
                      "training was being set up. Turn it on again to finish.",
                      finished_at=_now())
        _STATE = st
    return _STATE


def _save() -> None:
    st = _STATE
    if st is None:
        return
    path = state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(json.dumps(st, indent=1).encode("utf-8"))
        os.replace(tmp, path)
    except OSError as e:
        log.warning("wake-word training setup: could not save its state: %s", e)


def _update(**kw: Any) -> None:
    with _LOCK:
        _load().update(kw)
        _save()


def reset_for_tests() -> None:
    global _STATE, _TASK, _REQ_CACHE, _STARTING
    _STATE, _TASK, _REQ_CACHE, _STARTING = None, None, None, False
    _CANCEL.clear()


def job_running() -> bool:
    return _TASK is not None and not _TASK.done()


def recorded_gpu() -> bool | None:
    with _LOCK:
        return _load().get("gpu")


# ── docker, the host ─────────────────────────────────────────────────────

def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False, creationflags=_NO_WINDOW,
    )


def _docker_info() -> dict[str, Any]:
    """What the daemon says about itself, or {"ok": False, "error": ...}."""
    try:
        r = _run(["docker", "info", "--format", "{{json .}}"], 30)
    except FileNotFoundError:
        return {"ok": False, "error": "Docker is not installed on this server"}
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": f"Docker did not answer ({e})"}
    try:
        info = json.loads(r.stdout or "{}")
    except ValueError:
        info = {}
    if r.returncode != 0 or not info.get("ServerVersion"):
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        return {"ok": False, "error": "Docker is not running"
                + (f" ({tail[-1][:200]})" if tail else "")}
    runtimes = sorted((info.get("Runtimes") or {}).keys())
    return {
        "ok": True,
        "version": info.get("ServerVersion"),
        "ncpu": int(info.get("NCPU") or 0),
        "mem_bytes": int(info.get("MemTotal") or 0),
        "root": info.get("DockerRootDir") or "",
        "os": info.get("OperatingSystem") or "",
        "runtimes": runtimes,
    }


def _image_label(image: str) -> tuple[bool, str | None]:
    """(present, its source label)."""
    try:
        r = _run(["docker", "image", "inspect", "-f",
                  "{{ index .Config.Labels \"" + LABEL_SRC + "\" }}", image], 30)
    except (OSError, subprocess.SubprocessError):
        return False, None
    if r.returncode != 0:
        return False, None
    label = (r.stdout or "").strip()
    return True, (label if label and label != "<no value>" else None)


def source_tree_id() -> str:
    """The git tree id of scripts/wake_word/docker at HEAD (what
    apply-update.sh compares the image's label with), or "unknown"."""
    try:
        r = git_run.run(
            ["git", "-C", str(repo_dir()), "rev-parse", "HEAD:scripts/wake_word/docker"], timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    out = (r.stdout or "").strip()
    return out if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", out) else "unknown"


def _disk_free_gb(info: dict[str, Any]) -> tuple[float | None, str]:
    """Free space where Docker keeps images and volumes: its data root on
    this machine (Docker Engine, the Windows install's distro included),
    else the home drive (Docker Desktop keeps its disk image there)."""
    candidates = [info.get("root") or "", str(Path.home())]
    for path in candidates:
        if not path:
            continue
        try:
            if os.path.isdir(path):
                return shutil.disk_usage(path).free / 1e9, path
        except OSError:
            continue
    return None, ""


def on_wsl() -> bool:
    """The Windows install's distro (or any WSL): Domovoi can't see the
    Windows drive there, and its memory is a share of the PC's."""
    try:
        from domovoi.host_kind import is_wsl

        return is_wsl()
    except Exception:  # noqa: BLE001
        return False


def pc_memory_gb(distro_mib: int) -> tuple[int | None, str]:
    """(the PC's memory in GB, as words) behind a WSL distro's MemTotal,
    by the Windows installer's rule (windows/common/WslConfig.cs
    MemoryCapGb): a PC under 24 GB gives the distro 10 GB, a larger one
    its memory minus 8 GB, at most 64."""
    gb = distro_mib / 1024
    if gb < 12:
        return None, "less than 24 GB"
    if gb >= 63:
        return None, "72 GB or more"
    pc = int(round(gb + 8))
    return pc, f"about {pc} GB"


def _mem_total_mib() -> int | None:
    try:
        import psutil

        return int(psutil.virtual_memory().total / (1024 * 1024))
    except Exception:  # noqa: BLE001 - psutil missing: unknown, not a refusal
        return None


def _physical_cores() -> int | None:
    try:
        import psutil

        return psutil.cpu_count(logical=False)
    except Exception:  # noqa: BLE001
        return None


async def _host_gpus() -> list[dict[str, Any]]:
    try:
        from domovoi.handlers.homelab import _query_gpus

        gpus = await _query_gpus()
    except Exception:  # noqa: BLE001 - no nvidia-smi, or no subprocess support
        return []
    return [{"name": g.name, "mem_used_mb": g.mem_used_mb, "mem_total_mb": g.mem_total_mb}
            for g in gpus]


def training_cpus(ncpu: int) -> float:
    """CPUs a training may use: the setting, else all but a quarter of the
    Docker host's (at least two left free). The wrapper's own rule
    (resource_limits), so the estimate matches what it runs with."""
    want = float(settings.wake_trainer_cpus or 0)
    if want <= 0:
        want = float(max(1, ncpu - max(2, ncpu // 4))) if ncpu > 0 else 0.0
    return min(want, float(ncpu)) if ncpu > 0 else want


def cpu_estimate_min(ncpu: int, physical: int | None) -> int:
    """Minutes for one word on the CPU: 75 measured on 8 cores (the
    synthetic speech is the slow part and scales with cores; hyperthreads
    add little, so count physical cores)."""
    cpus = training_cpus(ncpu) or 8.0
    cores = min(cpus, float(physical)) if physical else cpus
    return int(max(30, round(CPU_MIN_PER_WORD_8 * 8 / max(1.0, cores))))


def auto_timeout_sec() -> int:
    """The worker's time limit when ``wake_word_train_timeout_sec`` is 0:
    an hour when the setup job found a GPU, else twice the CPU estimate
    and never under two hours (a hand-configured trainer the job never
    saw gets the two hours)."""
    with _LOCK:
        st = _load()
        gpu, est = st.get("gpu"), st.get("cpu_estimate_min")
    if gpu:
        return 3600
    return int(max(7200, (est or 0) * 60 * 2))


def train_timeout_sec() -> int:
    v = int(settings.wake_word_train_timeout_sec or 0)
    return v if v > 0 else auto_timeout_sec()


# ── what the page shows ──────────────────────────────────────────────────

def enabled() -> bool:
    return bool(settings.wake_word_trainer_enabled and (settings.wake_word_train_command or "").strip())


async def requirements(force: bool = False) -> dict[str, Any]:
    """What training needs and what this server has, and the refusals.
    Cached for a few seconds, and for as long as a setup job or a training
    runs: the page polls every 2 s then, and while 18 GB were landing in
    the cache volume a read of these (docker info, image inspect,
    nvidia-smi) took over 30 s twice in the 2026-10-13 end-to-end run, so
    the dashboard's read timed out; nothing here changes meanwhile that
    the page needs."""
    global _REQ_CACHE
    from domovoi.workers import wake_word_trainer as wwt

    now = time.monotonic()
    if not force and _REQ_CACHE is not None and (
        now - _REQ_CACHE[0] < _REQ_TTL_SEC or job_running() or wwt.training_active()
    ):
        return _REQ_CACHE[1]
    info = await asyncio.to_thread(_docker_info)
    gpus = await _host_gpus()
    image = settings.wake_trainer_image
    present, label = (await asyncio.to_thread(_image_label, image)) if info.get("ok") else (False, None)
    src = await asyncio.to_thread(source_tree_id)
    with _LOCK:
        st = dict(_load())
    image_current = present and label == src and src != "unknown"
    data_ready = bool(st.get("data_ready"))
    wsl = on_wsl()
    if wsl:
        # Docker's data root is inside the distro's virtual disk (up to
        # 1 TB, whatever C: holds; 1015 GB "free" in the 2026-10-13 test),
        # and the distro can't see C: (automount is off). No number, then:
        # the person confirms C: has the room.
        free_gb, disk_path = None, ""
    else:
        free_gb, disk_path = await asyncio.to_thread(_disk_free_gb, info)
    mem_mib = _mem_total_mib()
    physical = _physical_cores()
    ncpu = int(info.get("ncpu") or 0) or (os.cpu_count() or 0)

    # An image built from other sources is rebuilt: count it as needed.
    need_gb = (0.0 if image_current else IMAGE_GB) + (0.0 if data_ready else DATA_GB) + PER_WORD_GB
    refusals: list[str] = []
    notes: list[str] = []

    if egress.internet_turned_off():
        refusals.append(
            "Internet access is turned off for this server (Settings → Internet), and "
            f"training downloads about {DOWNLOAD_GB} GB the first time."
        )
    if not info.get("ok"):
        refusals.append(f"{info.get('error') or 'Docker is not available'}: training runs "
                        "in a Docker container.")
    if mem_mib is not None and mem_mib < MIN_MEM_MIB:
        if wsl:
            _pc, pc_words = pc_memory_gb(mem_mib)
            refusals.append(
                f"Training needs a PC with about 32 GB of memory or more, and this PC has "
                f"{pc_words}: Windows gives Domovoi the PC's memory minus 8 GB (here "
                f"{mem_mib / 1024:.0f} GB), and training needs 24 GB of that (it uses about "
                "10 GB while the voice pipeline keeps running)."
            )
        else:
            refusals.append(
                f"This server has {mem_mib / 1024:.0f} GB of memory; training needs a 24 GB "
                "server (it uses about 10 GB while the voice pipeline keeps running)."
            )
    if free_gb is not None and free_gb < need_gb + HEADROOM_GB:
        refusals.append(
            f"Only {free_gb:.0f} GB of disk is free; training needs about {need_gb:.0f} GB "
            f"plus {HEADROOM_GB:.0f} GB to spare."
        )
    elif free_gb is None and wsl:
        if need_gb >= 1:
            notes.append(
                "Domovoi can't see how much space is free on the Windows drive (C:), where "
                f"its disk lives. Training needs about {need_gb:.0f} GB of it: check that C: "
                f"has at least {WSL_CONFIRM_GB} GB free before you turn it on.")
        else:
            notes.append(
                "Domovoi can't see how much space is free on the Windows drive (C:), where "
                "its disk lives. Each wake word takes about half a gigabyte of it.")
    elif free_gb is None:
        notes.append("Couldn't measure the free disk space; training needs about "
                     + (f"{need_gb:.0f} GB." if need_gb >= 1 else "half a gigabyte a wake word."))

    runtime_ok = "nvidia" in (info.get("runtimes") or []) or "docker desktop" in (info.get("os") or "").lower()
    probe = st.get("gpu") if image_current else None
    if probe is not None:
        gpu = bool(probe)
    else:
        gpu = bool(gpus) and runtime_ok
    if gpus and not runtime_ok:
        notes.append(
            f"This server has an NVIDIA card ({gpus[0]['name']}), but Docker can't use it "
            "yet: Domovoi sets up the NVIDIA Container Toolkit at its next update or "
            "Restart (scripts/linux/gpu-runtime.sh; after a failed try it waits a day, "
            "unless you turn training on, or check again, first). Until then training "
            "runs on the CPU."
        )
    if gpus and probe is False:
        notes.append("The last check found Docker can't reach the GPU, so training runs "
                     "on the CPU.")
    cpu_min = cpu_estimate_min(ncpu, physical)
    estimate = (
        {"device": "gpu", "minutes_per_word": GPU_MIN_PER_WORD, "first_minutes": GPU_FIRST_MIN,
         "gpu_names": [g["name"] for g in gpus]}
        if gpu else
        {"device": "cpu", "minutes_per_word": cpu_min, "first_minutes": cpu_min + 6,
         "cpus": training_cpus(ncpu), "physical_cores": physical,
         "measured": f"about {CPU_MIN_PER_WORD_8} min a word on 8 cores"}
    )
    out = {
        "docker": {k: info.get(k) for k in ("ok", "version", "os", "runtimes", "error")},
        "gpus": gpus,
        "gpu": gpu,
        "estimate": estimate,
        "disk": {"free_gb": None if free_gb is None else round(free_gb, 1),
                 "need_gb": round(need_gb), "headroom_gb": HEADROOM_GB, "path": disk_path,
                 "total_first_gb": round(IMAGE_GB + DATA_GB + PER_WORD_GB + 1),
                 # On WSL: no number; "Turn on" asks to confirm C: has this much.
                 "confirm_gb": WSL_CONFIRM_GB if wsl else None},
        "memory": {"total_gb": None if mem_mib is None else round(mem_mib / 1024, 1),
                   "need_gb": 24,
                   "pc_need_gb": 32 if wsl else None},
        "wsl": wsl,
        "download_gb": DOWNLOAD_GB,
        "image": {"name": image, "present": present, "current": image_current},
        "data_ready": data_ready,
        "refusals": refusals,
        "notes": notes,
    }
    _REQ_CACHE = (now, out)
    return out


async def status() -> dict[str, Any]:
    """Everything Settings → Wake Words shows about training."""
    from domovoi.workers import wake_word_trainer as wwt

    with _LOCK:
        st = dict(_load())
    req = await requirements()
    if job_running():
        st["state"] = "running"
    return {
        "enabled": enabled(),
        "job": {k: st.get(k) for k in (
            "state", "stage", "percent", "message", "error", "log_tail",
            "needs_cpu_confirm", "started_at", "finished_at")},
        "gpu_name": st.get("gpu_name"),
        "training_active": wwt.training_active(),
        "timeout_sec": train_timeout_sec(),
        "timeout_auto": int(settings.wake_word_train_timeout_sec or 0) <= 0,
        "requirements": req,
    }


# ── the job ──────────────────────────────────────────────────────────────

class Refused(Exception):
    """The job can't start; ``str(e)`` says why (an HTTP 409)."""


def _progress(stage: str, frac: float, message: str) -> None:
    lo, hi = _WEIGHT[stage]
    pct = int(lo + (hi - lo) * max(0.0, min(1.0, frac)))
    with _LOCK:
        st = _load()
        st.update(stage=stage, message=message, percent=max(int(st.get("percent") or 0), pct))
        _save()


def _stream(argv: list[str], timeout: float, on_line: Callable[[str], None],
            on_cancel: Callable[[], None] | None = None) -> tuple[int | None, list[str]]:
    """Run ``argv``, hand each output line to ``on_line``; (exit code or
    None when stopped / timed out, the last 40 lines)."""
    tail: deque[str] = deque(maxlen=40)
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
        )
    except OSError as e:
        return None, [f"could not start {argv[0]}: {e}"]
    deadline = time.monotonic() + timeout
    done = threading.Event()

    def watch() -> None:
        while not done.wait(1.0):
            if _CANCEL.is_set() or time.monotonic() > deadline:
                if on_cancel is not None:
                    on_cancel()
                proc.terminate()
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return

    w = threading.Thread(target=watch, daemon=True)
    w.start()
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\r\n")
        if not line:
            continue
        tail.append(line[-400:])
        try:
            on_line(line)
        except Exception:  # noqa: BLE001 - progress parsing never breaks the job
            pass
    rc = proc.wait()
    done.set()
    if _CANCEL.is_set():
        tail.append(f"stopped: {_CANCEL_REASON or 'Domovoi is shutting down'}")
        return None, list(tail)
    if time.monotonic() > deadline:
        tail.append(f"stopped after {int(timeout)} s")
        return None, list(tail)
    return rc, list(tail)


_BUILD_STEP = re.compile(r"^#\d+ \[(?:[^\]]*?\s)?(\d+)/(\d+)\]")
_WARM_BYTES = re.compile(r"warm: (\d+) bytes")


def _fail(stage: str, error: str, tail: list[str], **extra: Any) -> None:
    if _CANCEL.is_set() and _CANCEL_REASON:
        error = f"Stopped: {_CANCEL_REASON}. Turn training on again once that has changed."
    _update(state="failed", stage=stage, error=error, log_tail=tail[-25:],
            finished_at=_now(), **extra)
    log.warning("wake-word training setup failed at %s: %s", stage, error)


def _build(src: str) -> bool:
    image = settings.wake_trainer_image
    _progress("image", 0.0, "Building the trainer (about 8 minutes the first time, "
              "with a 3 GB download)")

    def line(s: str) -> None:
        m = _BUILD_STEP.match(s)
        if m:
            k, n = int(m.group(1)), int(m.group(2))
            _progress("image", k / max(1, n), f"Building the trainer: step {k} of {n}")

    rc, tail = _stream(
        ["docker", "build", "--progress=plain", "--label", f"{LABEL_SRC}={src}",
         "-t", image, str(trainer_dir() / "docker")],
        BUILD_TIMEOUT_SEC, line,
    )
    if rc != 0:
        _fail("image", "Building the trainer image failed"
              + (f": {tail[-1]}" if tail else ""), tail)
        return False
    _update(image_src=src)
    return True


def _probe_gpu() -> tuple[bool, str]:
    """(torch sees a GPU through --gpus all, its name)."""
    _progress("gpu", 0.0, "Checking that the trainer can use the GPU")
    try:
        r = _run(["docker", "run", "--rm", "--label", f"{WARM_LABEL}=probe", "--gpus", "all",
                  "--entrypoint", "python", settings.wake_trainer_image, "-c",
                  "import torch,sys\n"
                  "ok = torch.cuda.is_available()\n"
                  "print(torch.cuda.get_device_name(0) if ok else 'no cuda')\n"
                  "sys.exit(0 if ok else 1)"],
                 PROBE_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    lines = (r.stdout or "").strip().splitlines()
    if r.returncode == 0 and lines:
        return True, lines[-1][:120]
    err = (r.stderr or r.stdout or "").strip().splitlines()
    return False, (err[-1][:300] if err else f"exit {r.returncode}")


def _remove_warm() -> None:
    try:
        _run(["docker", "rm", "-f", WARM_CONTAINER], 60)
    except (OSError, subprocess.SubprocessError):
        pass


def _warm() -> bool:
    _progress("data", 0.0, f"Downloading the training data (about {DATA_GB:.0f} GB, "
              "once)")
    _remove_warm()

    def line(s: str) -> None:
        m = _WARM_BYTES.search(s)
        if m:
            n = int(m.group(1))
            _progress("data", n / DATA_BYTES,
                      f"Downloading the training data: {n / 1e9:.1f} of about "
                      f"{DATA_BYTES / 1e9:.0f} GB")

    rc, tail = _stream(
        ["docker", "run", "--rm", "--name", WARM_CONTAINER, "--label", f"{WARM_LABEL}=warm",
         "--cpu-shares", "128", "-v", f"{settings.wake_trainer_cache_volume}:/data/cache",
         settings.wake_trainer_image, "--warm"],
        WARM_TIMEOUT_SEC, line, on_cancel=_remove_warm,
    )
    if rc != 0:
        _remove_warm()
        _fail("data", "Downloading the training data failed"
              + (f": {tail[-1]}" if tail else ""), tail)
        return False
    _update(data_ready=True)
    return True


def _switch_on() -> None:
    from domovoi.config_env_writer import write_env_values

    _progress("settings", 0.0, "Switching training on")
    cmd = (settings.wake_word_train_command or "").strip() or default_train_command()
    if wrapper_stop_argv(cmd) is None:
        # A hand-written command was there: "Turn on" means the repo's own.
        cmd = default_train_command()
    write_env_values({"wake_word_trainer_enabled": True, "wake_word_train_command": cmd})
    settings.wake_word_train_command = cmd
    settings.wake_word_trainer_enabled = True
    _start_worker()


def _start_worker() -> None:
    try:
        from domovoi.plugins_runtime.workers import WORKERS

        WORKERS.start_worker("core", "wake_word_trainer")
    except Exception as e:  # noqa: BLE001 - it starts at the next boot anyway
        log.warning("wake-word trainer worker not started now (%s); it starts at the "
                    "next restart", e)


async def _job(cpu_ok: bool, gpus: list[dict[str, Any]], ncpu: int,
               physical: int | None) -> None:
    """The job: the blocking stages run in worker threads, the switch-on
    (settings, the worker start) on the event loop."""
    global _REQ_CACHE
    try:
        src = await asyncio.to_thread(source_tree_id)
        if not await asyncio.to_thread(_build, src):
            return
        gpu, name = False, None
        if gpus:
            gpu, name = await asyncio.to_thread(_probe_gpu)
            if not gpu and not cpu_ok:
                est = cpu_estimate_min(ncpu, physical)
                _fail("gpu",
                      f"Docker can't reach the GPU ({name}), so training would run on the "
                      f"CPU: about {est} minutes a wake word, and voice gets slower "
                      "meanwhile. Use the CPU, or turn it on again after the next Domovoi "
                      "update (it installs the NVIDIA Container Toolkit).",
                      [name or ""], needs_cpu_confirm=True, gpu=False, gpu_name=None,
                      cpu_estimate_min=est)
                return
            name = name if gpu else None
        _update(gpu=gpu, gpu_name=name, cpu_estimate_min=cpu_estimate_min(ncpu, physical))
        if _CANCEL.is_set():
            if _CANCEL_REASON:          # told to stop (internet turned off): say so
                _fail("data", "Stopped", [])
            return                      # a shutdown: the next start reads "interrupted"
        if not await asyncio.to_thread(_warm):
            return
        _switch_on()
        _update(state="done", stage=None, percent=100, finished_at=_now(),
                message=("Training is on: about 20 minutes a wake word on the GPU" if gpu else
                         f"Training is on: about {cpu_estimate_min(ncpu, physical)} minutes "
                         "a wake word on the CPU"),
                error=None, log_tail=[], needs_cpu_confirm=False)
        log.info("wake-word training is on (%s)", f"GPU {name}" if gpu else "CPU")
    except asyncio.CancelledError:
        _CANCEL.set()
        raise
    except Exception as e:  # noqa: BLE001 - every failure must land in the state
        log.exception("wake-word training setup crashed")
        _fail(_load().get("stage") or "image", f"Setting up training failed: {e}", [])
    finally:
        _REQ_CACHE = None


def busy() -> bool:
    """A job runs, or a "Turn on" (or "Turn off") is between its checks and
    its job: the next one is refused."""
    return _STARTING or job_running()


def gpu_runtime_retry_flag() -> Path:
    """Read by scripts/linux/apply-update.sh (as root) at the next update
    or Restart: set up Docker's GPU runtime again now, instead of after its
    one-day wait since the last failure."""
    return state_dir() / "gpu-runtime-retry"


async def start(cpu_ok: bool = False, disk_ok: bool = False) -> dict[str, Any]:
    """Start the job. Raises :class:`Refused` (with the reason) when it
    can't: a refusal from :func:`requirements`, a job or a training already
    running, a CPU-only server without ``cpu_ok``, or the Windows install
    (which can't see C:'s free space) without ``disk_ok``.

    One at a time: the check and the claim are one step (``_STARTING`` is
    set before the first await), so two overlapping requests can't both
    start a job (review of 2026-10-13: the second one's cleanup removed the
    first one's download container)."""
    global _TASK, _STARTING
    from domovoi.workers import wake_word_trainer as wwt

    if busy():
        raise Refused("Training is already being set up.")
    if wwt.training_active():
        raise Refused("A wake word is training right now; wait for it to finish.")
    _STARTING = True
    try:
        return await _start(cpu_ok, disk_ok)
    finally:
        _STARTING = False


async def _start(cpu_ok: bool, disk_ok: bool) -> dict[str, Any]:
    global _TASK, _CANCEL_REASON
    req = await requirements(force=True)
    if req["refusals"]:
        raise Refused(" ".join(req["refusals"]))
    if req["disk"].get("confirm_gb") and not disk_ok:
        raise Refused(
            "Domovoi can't see how much space is free on the Windows drive (C:). Confirm "
            f"that C: has at least {req['disk']['confirm_gb']} GB free to turn training on."
        )
    if not req["gpu"] and not cpu_ok:
        raise Refused(
            f"This server trains on the CPU: about {req['estimate']['minutes_per_word']} "
            "minutes a wake word, and voice gets slower meanwhile. Confirm to use the CPU."
        )
    if req["gpus"] and "nvidia" not in (req["docker"].get("runtimes") or []) \
            and "docker desktop" not in (req["docker"].get("os") or "").lower():
        # The card is there and Docker can't use it: ask the update unit to
        # set up the GPU runtime again at the next update or Restart, not
        # after its one-day wait (scripts/linux/gpu-runtime.sh).
        try:
            flag = gpu_runtime_retry_flag()
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text(_now() + "\n", encoding="utf-8")
        except OSError as e:
            log.warning("wake-word training setup: no GPU-runtime retry flag (%s)", e)
    _CANCEL.clear()
    _CANCEL_REASON = ""
    with _LOCK:
        st = _load()
        keep = {k: st.get(k) for k in ("data_ready", "image_src")}
        st.clear()
        st.update(_blank(), **keep)
        st.update(state="running", stage="image", percent=0, started_at=_now(),
                  message="Starting")
        _save()
    info = await asyncio.to_thread(_docker_info)
    ncpu = int(info.get("ncpu") or 0) or (os.cpu_count() or 0)
    _TASK = asyncio.get_running_loop().create_task(
        _job(cpu_ok, req["gpus"], ncpu, _physical_cores()), name="wake-trainer-setup",
    )
    return await status()


def cancel_job(reason: str) -> bool:
    """Stop a running job (its docker build or download and the download
    container) for ``reason``; the job then fails saying so. True when
    one was running."""
    global _CANCEL_REASON
    if not job_running():
        return False
    _CANCEL_REASON = reason
    _CANCEL.set()
    log.warning("wake-word training setup stopped: %s", reason)
    return True


# The transient unit scripts/linux/apply-update.sh starts for an image
# rebuild after an update (as this service user, `docker build`).
REBUILD_UNIT = "domovoi-wake-trainer-build"


def stop_background_rebuild(reason: str) -> bool:
    """Stop the update's background rebuild of the trainer image, if one
    runs: the unit's main process (the `docker build` client, this same
    user's, so no privilege is needed) gets SIGTERM, and BuildKit cancels
    the build with it. Blocking; True when it stopped one."""
    import signal

    try:
        r = _run(["systemctl", "show", "-p", "MainPID", "--value", REBUILD_UNIT], 15)
        pid = int((r.stdout or "0").strip() or 0)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    if pid <= 1:
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        if b"build" not in cmdline or not any(c.endswith(b"docker") for c in cmdline):
            return False
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError) as e:
        log.warning("could not stop the trainer image rebuild (pid %s): %s", pid, e)
        return False
    log.warning("stopped the trainer image rebuild (%s): %s", REBUILD_UNIT, reason)
    return True


def on_internet_answer() -> None:
    """The ``internet_access`` reapply hook, and the boot check: under
    never, nothing of the trainer's may download, so a "Turn on" job (its
    build or the 18 GB download) and the update's image rebuild stop."""
    if not egress.internet_turned_off():
        return
    reason = "internet access was turned off (Settings > Internet)"
    cancel_job(reason)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        stop_background_rebuild(reason)
        return
    task = loop.create_task(asyncio.to_thread(stop_background_rebuild, reason),
                            name="wake-trainer-rebuild-stop")
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


_PENDING: set = set()


# ── turn off and free the space ──────────────────────────────────────────

_SIZE_RE = re.compile(r"^\s*([0-9.]+)\s*([kKMGT]?B)\s*$")


def _parse_size(text: str) -> int | None:
    m = _SIZE_RE.match(text or "")
    if not m:
        return None
    mult = {"B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}[m.group(2)]
    return int(float(m.group(1)) * mult)


def footprint() -> dict[str, Any]:
    """What "Turn off and free the space" removes, as Docker counts it: the
    trainer image's size and the cache volume's (the training data and
    every word's training files). Blocking; a part Docker can't size is
    None (shown as "size unknown", never guessed)."""
    image = settings.wake_trainer_image
    volume = settings.wake_trainer_cache_volume
    out: dict[str, Any] = {"image": image, "image_bytes": None, "volume": volume,
                           "volume_bytes": None, "image_present": False,
                           "volume_present": False}
    try:
        r = _run(["docker", "image", "inspect", "-f", "{{.Size}}", image], 30)
        if r.returncode == 0:
            out["image_present"] = True
            out["image_bytes"] = int((r.stdout or "0").strip() or 0)
        r = _run(["docker", "volume", "inspect", volume], 30)
        out["volume_present"] = r.returncode == 0
        if out["volume_present"]:
            r = _run(["docker", "system", "df", "-v", "--format", "{{json .Volumes}}"], 120)
            for v in json.loads(r.stdout or "[]") if r.returncode == 0 else []:
                if v.get("Name") == volume:
                    out["volume_bytes"] = _parse_size(v.get("Size", ""))
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log.warning("wake-word training footprint: %s", e)
    known = [b for b in (out["image_bytes"], out["volume_bytes"]) if b is not None]
    out["total_bytes"] = sum(known) if known else None
    return out


async def turn_off() -> dict[str, Any]:
    """Settings off, then the trainer image, its build cache and the
    cache volume removed (the training data and every word's synthetic
    clips, features and any copies of recordings). Trained models and the
    recorded clips stay; queued words go back to "recording". Refused
    while a job or a training runs. Returns what was freed."""
    global _STARTING, _STATE, _REQ_CACHE
    from domovoi.config_env_writer import write_env_values
    from domovoi.workers import wake_word_trainer as wwt

    if busy():
        raise Refused("Training is being set up; wait for it to finish.")
    if wwt.training_active():
        raise Refused("A wake word is training; stop it first.")
    _STARTING = True
    try:
        before = await asyncio.to_thread(footprint)
        write_env_values({"wake_word_trainer_enabled": False, "wake_word_train_command": ""})
        settings.wake_word_trainer_enabled = False
        settings.wake_word_train_command = ""
        errors: list[str] = []
        steps = []
        if before["image_present"]:
            steps.append(["docker", "image", "rm", "-f", before["image"]])
        if before["volume_present"]:
            steps.append(["docker", "volume", "rm", "-f", before["volume"]])
        steps.append(["docker", "builder", "prune", "-f"])
        for argv in steps:
            try:
                r = await asyncio.to_thread(_run, argv, 300)
                if r.returncode != 0:
                    errors.append(f"{' '.join(argv[:3])}: "
                                  f"{(r.stderr or r.stdout or '').strip().splitlines()[-1:] or ''}")
            except (OSError, subprocess.SubprocessError) as e:
                errors.append(f"{' '.join(argv[:3])}: {e}")
        try:
            from domovoi.db.session import session_scope
            from sqlalchemy import text

            async with session_scope() as s:
                await s.execute(text(
                    "UPDATE wake_words SET status = 'recording', "
                    "error = 'Training was turned off.', updated_at = NOW() "
                    "WHERE status = 'training'"))
                await s.execute(text("SELECT pg_notify('wake_words_changed', 'all')"))
        except Exception as e:  # noqa: BLE001
            errors.append(f"queued words: {e}")
        with _LOCK:
            _STATE = _blank()
            _STATE.update(message="Turned off; the trainer and its data were removed.")
            _save()
        log.info("wake-word training turned off; freed %s bytes", before.get("total_bytes"))
        return {"freed": before, "errors": errors}
    finally:
        _REQ_CACHE = None
        _STARTING = False


async def shutdown() -> None:
    """Core shutdown: stop a running job (its docker child and the warm
    container); the state then reads "interrupted" at the next start."""
    if not job_running():
        return
    _CANCEL.set()
    # The watcher thread notices within a second and removes the container;
    # whatever is left after this is the next start's "interrupted".
    try:
        await asyncio.wait_for(asyncio.shield(_TASK), timeout=2.5)  # type: ignore[arg-type]
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        pass
