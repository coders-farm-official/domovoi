#!/usr/bin/env python3
"""Portable, capability-sensing host wrapper for openWakeWord training.

This file IS the value of ``settings.wake_word_train_command``. The core's
``wake_word_trainer`` worker shells out to it (see
``domovoi/workers/wake_word_trainer.py``) with four placeholders already
substituted into argv::

    python "<repo>/scripts/wake_word/train_wake_word.py" \
        --clips "{clips_dir}" --phrase "{phrase}" --slug "{slug}" --out "{out}"

WHY THIS LIVES ON THE HOST (and is stdlib-only)
===============================================
openWakeWord automatic training is **Linux-only** (``piper-sample-generator``
needs ``espeak-ng`` + a C toolchain), so the actual training runs inside a
Docker-Linux container. But the Domovoi server is heterogeneous across the
people who share this system: multi-GPU Windows boxes, CPU-only laptops, Linux,
maybe Mac. The host may not have ``torch`` (it's only a Domovoi server), so this
wrapper imports **nothing outside the standard library**. All it does is sense
the host's capabilities and assemble the correct ``docker run``.

WHAT IT SENSES AT RUN TIME
==========================
1. Is ``docker`` on PATH and is the daemon reachable? (clear error if not)
2. Does this Docker support GPU passthrough? Probed with a real
   ``docker run --gpus all ... true`` so a GPU-less host never *errors* on
   ``--gpus`` — we just omit the flag and run on CPU.
3. If GPUs exist, which is **least used**? Picked via
   ``nvidia-smi --query-gpu=index,memory.used`` so we never hardcode an index
   and never fight another tenant for a busy card.

It then builds the right command:
  * GPU, multiple cards  -> ``--gpus "device=<N>"`` for the least-used index
  * GPU, single card     -> ``--gpus all``
  * No GPU               -> ``--gpus`` OMITTED entirely (works on any host)

Mounts (host path syntax handled for BOTH Windows and POSIX):
  * ``{clips_dir}``           -> ``/data/clips``  (read-only, the positive set)
  * ``dirname({out})``        -> ``/data/models`` (the container writes
                                  ``/data/models/<slug>.onnx``; the bind mount
                                  makes it appear at the host ``{out}`` the
                                  worker then stat()s for success)
  * named volume ``WAKE_TRAINER_CACHE_VOLUME`` -> ``/data/cache`` (the ~7 GB
                                  negative/feature dataset + piper model,
                                  downloaded once, reused forever)

It streams the container's stdout/stderr straight through and **returns the
container's exit code unchanged** — the worker's success contract is
``exit 0 AND <slug>.onnx exists at {out}``, so we must not mask the code.

NOTHING IS HARDCODED TO ONE MACHINE: no fixed GPU index, no ``~/.domovoi`` path
baked in (``{out}`` and ``{clips_dir}`` arrive as args; ``models_dir`` is
derived here as ``dirname(out)``), no assumption a GPU (or even Docker on a
particular OS) exists.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath

# ── Tunables (env-overridable; sensible defaults so the bare template works) ──
# Image name/tag. Override WAKE_TRAINER_IMAGE to use a registry-published image
# (e.g. ghcr.io/kamronk/domovoi-wake-trainer:1) so sharers `docker pull` instead
# of building locally — see scripts/wake_word/DOCKER_TRAINER.md.
IMAGE = os.environ.get("WAKE_TRAINER_IMAGE", "domovoi-wake-trainer:latest")
# Named Docker volume holding the one-time ~7 GB negative/feature dataset +
# piper voice model. A NAMED volume (not a bind mount) because Windows C:\ bind
# mounts are dramatically slower for the I/O-heavy feature reads.
CACHE_VOLUME = os.environ.get("WAKE_TRAINER_CACHE_VOLUME", "domovoi_oww_cache")
# Override to point at a different docker binary (e.g. "podman", or on a Windows
# host that only has docker inside WSL: "wsl docker" won't work as argv[0] —
# install Docker Desktop, which puts a real docker.exe on PATH).
DOCKER = os.environ.get("WAKE_TRAINER_DOCKER", "docker")
# How many synthetic positive/validation clips the container should generate,
# and how many training steps to run. Surfaced as env so an operator can trade
# quality for speed (or iterate fast) without rebuilding the image. Empty ->
# the image's own defaults (n=5000 / val=1000 / steps=50000).
N_SAMPLES = os.environ.get("WAKE_TRAINER_N_SAMPLES", "")          # "" -> image default
N_SAMPLES_VAL = os.environ.get("WAKE_TRAINER_N_SAMPLES_VAL", "")  # "" -> image default
STEPS = os.environ.get("WAKE_TRAINER_STEPS", "")                  # "" -> image default (50000)
# Shared-memory size for the container. The training DataLoader's worker procs
# pass batches via /dev/shm; Docker's 64 MB default overflows mid-train. 2 GB is
# ample for the default batch sizes; raise for very large batch_n_per_class.
SHM_SIZE = os.environ.get("WAKE_TRAINER_SHM_SIZE", "2g")
# Oversample the real recorded clips so they reach this fraction of the synthetic
# positive set (the entrypoint duplicates each real clip; augmentation then varies
# the copies). "" -> the image default (0.2). Crank toward 0.3–0.5 for a hard mic
# like the XVF3800 once you've recorded a LARGE, varied real set — but more unique
# clips beat a bigger fraction on too-few recordings (overfit risk).
REAL_TARGET_FRACTION = os.environ.get("WAKE_TRAINER_REAL_TARGET_FRACTION", "")


def log(msg: str) -> None:
    """Single-channel logging to stderr. The worker captures stdout+stderr and,
    on failure, persists the last 1000 chars of stderr (falling back to stdout)
    — so keep our own diagnostics on stderr where they survive."""
    print(f"[wake-trainer] {msg}", file=sys.stderr, flush=True)


def docker_argv() -> list[str]:
    """DOCKER may be a multi-token string (rare, e.g. 'podman --foo'); split it
    so it can prefix an argv list."""
    return DOCKER.split()


def docker_available() -> bool:
    """docker on PATH AND daemon reachable. ``docker info`` exits non-zero if the
    daemon is down, which is exactly the 'Docker Desktop not started' case."""
    exe = docker_argv()[0]
    if shutil.which(exe) is None:
        return False
    try:
        r = subprocess.run(
            [*docker_argv(), "info"],
            capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def gpu_runtime_available() -> bool:
    """Authoritatively answer 'will --gpus work here?' by actually trying it.

    We run a throwaway probe that asks torch INSIDE the container whether it can
    see a CUDA device. This is the only reliable cross-platform test: ``docker
    info`` GPU hints differ between Docker Desktop (toolkit bundled) and native
    Linux, and the host's nvidia-smi says nothing about the *container's* GPU
    access. We MUST override the image's ENTRYPOINT (``python /opt/entrypoint.py``,
    which needs WAKE_SLUG/WAKE_PHRASE and would otherwise exit non-zero and look
    like a GPU failure) with ``--entrypoint python`` and run a tiny torch check.
    Exit 0 == torch sees the GPU; any non-zero/exception -> treat as CPU-only.

    Uses our own image (already built) so we don't pull anything extra.
    """
    try:
        r = subprocess.run(
            [*docker_argv(), "run", "--rm", "--gpus", "all",
             "--entrypoint", "python", IMAGE,
             "-c", "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)"],
            capture_output=True, text=True, timeout=180,
        )
        if r.returncode == 0:
            return True
        log(f"GPU probe non-zero ({r.returncode}); falling back to CPU. "
            f"stderr tail: {(r.stderr or '').strip()[-300:]}")
        return False
    except (OSError, subprocess.SubprocessError) as e:
        log(f"GPU probe failed ({e}); falling back to CPU.")
        return False


def select_gpu() -> tuple[str, int]:
    """Return ('all', count) on success, choosing the LEAST-USED GPU index.

    We shell out to the HOST's nvidia-smi (not inside a container) to rank cards
    by used memory, so a multi-tenant box doesn't pile this run onto a card
    another job is hammering. Returns:
      * ('all', 1)           -> single GPU, use --gpus all
      * (str(index), n)      -> multi-GPU, use --gpus "device=<index>"
      * ('', 0)              -> nvidia-smi unavailable; caller may still try
                                --gpus all if the container GPU probe passed.
    """
    if shutil.which("nvidia-smi") is None:
        return ("", 0)
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ("", 0)
    if r.returncode != 0:
        return ("", 0)

    cards: list[tuple[int, int]] = []  # (mem_used_mib, index)
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 2:
            continue
        try:
            idx, used = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        cards.append((used, idx))

    if not cards:
        return ("", 0)
    if len(cards) == 1:
        return ("all", 1)
    cards.sort()  # least-used memory first
    return (str(cards[0][1]), len(cards))


def to_docker_mount_source(host_path: str) -> str:
    """Normalize a host path into a Docker-bind-mount source that is safe across
    Windows and POSIX *and* survives any later shlex round-trips.

    On Windows, ``C:\\Users\\Kamron\\.domovoi\\wake_clips\\hey_domovoi`` becomes
    ``C:/Users/Kamron/.domovoi/wake_clips/hey_domovoi`` (forward slashes — Docker
    Desktop accepts this and it dodges backslash-as-escape pitfalls). On POSIX
    the path is already fine. We resolve to an absolute path so a relative arg
    can't produce a surprise anonymous volume.
    """
    p = Path(host_path)
    try:
        p = p.resolve()
    except (OSError, RuntimeError):
        p = Path(os.path.abspath(host_path))
    s = str(p)
    # Detect a Windows drive path even when running under git-bash (where
    # os.name may be 'posix' but the path is still 'C:\...').
    if (os.name == "nt") or (len(s) >= 2 and s[1] == ":"):
        # Use PureWindowsPath to flip separators deterministically.
        s = str(PureWindowsPath(s)).replace("\\", "/")
    return s


def build_run_argv(
    clips_src: str,
    models_src: str,
    phrase: str,
    slug: str,
    gpu_flag: list[str],
) -> list[str]:
    """Assemble the full ``docker run`` argv. ``gpu_flag`` is e.g.
    ['--gpus', 'all'] or ['--gpus', 'device=1'] or [] (CPU)."""
    argv: list[str] = [
        *docker_argv(), "run", "--rm",
        # The training DataLoader uses multiprocessing workers that pass tensor
        # batches through /dev/shm. Docker's default shm is only 64 MB, which
        # overflows mid-train ("RuntimeError: No space left on device" on a
        # /torch_* file). Bump it; override via WAKE_TRAINER_SHM_SIZE.
        "--shm-size", SHM_SIZE,
        *gpu_flag,
        "-v", f"{clips_src}:/data/clips:ro",
        "-v", f"{models_src}:/data/models",
        "-v", f"{CACHE_VOLUME}:/data/cache",
        # Pass the training parameters as env into the container's entrypoint.
        "-e", f"WAKE_PHRASE={phrase}",
        "-e", f"WAKE_SLUG={slug}",
    ]
    if N_SAMPLES:
        argv += ["-e", f"WAKE_N_SAMPLES={N_SAMPLES}"]
    if N_SAMPLES_VAL:
        argv += ["-e", f"WAKE_N_SAMPLES_VAL={N_SAMPLES_VAL}"]
    if STEPS:
        argv += ["-e", f"WAKE_STEPS={STEPS}"]
    if REAL_TARGET_FRACTION:
        argv += ["-e", f"WAKE_REAL_TARGET_FRACTION={REAL_TARGET_FRACTION}"]
    argv.append(IMAGE)
    return argv


def main() -> int:
    ap = argparse.ArgumentParser(description="Portable openWakeWord docker trainer wrapper")
    ap.add_argument("--clips", required=True, help="host positive-clips dir ({clips_dir})")
    ap.add_argument("--phrase", required=True, help="wake phrase, e.g. 'hey domovoi' ({phrase})")
    ap.add_argument("--slug", required=True, help="model slug; output is <slug>.onnx ({slug})")
    ap.add_argument("--out", required=True, help="host output path <...>/<slug>.onnx ({out})")
    args = ap.parse_args()

    out_path = Path(args.out)
    slug = args.slug

    # (1) Derive models_dir OURSELVES from --out. The worker guarantees this dir
    # exists (out_path.parent.mkdir), but we don't depend on that — make sure.
    models_dir = out_path.parent
    try:
        models_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log(f"ERROR: cannot create models dir {models_dir}: {e}")
        return 2

    # Sanity: the contract is that <slug>.onnx lands at {out}. If the operator's
    # template ever drifts so {out} stem != slug, warn loudly — the worker
    # stat()s {out} but the container writes /data/models/<slug>.onnx.
    if out_path.name != f"{slug}.onnx":
        log(f"WARNING: --out basename {out_path.name!r} != {slug}.onnx; "
            f"the container writes <slug>.onnx into the models mount, so the "
            f"worker may not find {out_path.name}.")

    clips_dir = Path(args.clips)
    if not clips_dir.is_dir():
        log(f"ERROR: clips dir does not exist: {clips_dir}. Record positive "
            f"clips first (Wake Words tab) — nothing to train on.")
        return 2

    # (2) Probe capabilities.
    if not docker_available():
        log("ERROR: Docker is not available. The 'docker' binary must be on "
            "PATH and the daemon running.\n"
            "  * Windows/Mac: install & start Docker Desktop (WSL2 engine on "
            "Windows).\n"
            "  * Linux: install docker-ce + start the daemon (and the "
            "nvidia-container-toolkit if you want GPU).\n"
            "openWakeWord training is Linux-only, so a Linux container is "
            "required even on a Windows/Mac host. See "
            "scripts/wake_word/DOCKER_TRAINER.md.")
        return 3

    # Ensure the image exists locally; if not, tell the operator how to get it.
    img_check = subprocess.run(
        [*docker_argv(), "image", "inspect", IMAGE],
        capture_output=True, text=True,
    )
    if img_check.returncode != 0:
        log(f"ERROR: image {IMAGE!r} not found locally. Build it once:\n"
            f"  docker build -t {IMAGE} scripts/wake_word/docker\n"
            f"or pull a published one and set WAKE_TRAINER_IMAGE. See "
            f"scripts/wake_word/DOCKER_TRAINER.md.")
        return 3

    # (3) Decide GPU vs CPU. Two independent signals must agree for GPU:
    #     (a) the container can actually use --gpus (gpu_runtime_available), and
    #     (b) we can pick a card (or it's a single-GPU host).
    gpu_flag: list[str] = []
    backend = "CPU"
    if gpu_runtime_available():
        device, count = select_gpu()
        if count > 1 and device not in ("", "all"):
            gpu_flag = ["--gpus", f"device={device}"]
            backend = f"GPU device {device} (of {count}, least-used)"
        else:
            # Single GPU, or nvidia-smi unavailable but the container probe
            # passed -> let Docker expose all GPUs.
            gpu_flag = ["--gpus", "all"]
            backend = "GPU all" if count <= 1 else "GPU all (smi unavailable)"
    else:
        log("No usable GPU runtime detected; training on CPU (slower, "
            "~12-24h vs ~1-2h on GPU). This is fully supported — the same "
            "image runs CPU-only when --gpus is omitted.")

    # (4) Build + run.
    clips_src = to_docker_mount_source(args.clips)
    models_src = to_docker_mount_source(str(models_dir))
    run_argv = build_run_argv(clips_src, models_src, args.phrase, slug, gpu_flag)

    # (5) Log the chosen backend + the exact command (stderr).
    log(f"backend = {backend}")
    log(f"image   = {IMAGE}")
    log(f"clips   = {clips_src} -> /data/clips:ro")
    log(f"models  = {models_src} -> /data/models  (expecting /data/models/{slug}.onnx)")
    log(f"cache   = {CACHE_VOLUME} -> /data/cache")
    log("running: " + " ".join(run_argv))

    # Stream stdout/stderr straight through (inherit this process's fds) and
    # return the container's exit code UNCHANGED — the worker's success gate is
    # exit 0 + <slug>.onnx present at {out}, so we must not remap the code.
    try:
        proc = subprocess.run(run_argv)
    except OSError as e:
        log(f"ERROR: failed to launch docker: {e}")
        return 3
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
