#!/usr/bin/env python3
"""Portable, capability-sensing host wrapper for openWakeWord training.

This file IS the value of ``settings.wake_word_train_command`` (Settings →
Wake Words → "Turn on wake-word training" writes that). The core's
``wake_word_trainer`` worker shells out to it (see
``domovoi/workers/wake_word_trainer.py``) with four placeholders already
substituted into argv::

    python "<repo>/scripts/wake_word/train_wake_word.py" \
        --clips "{clips_dir}" --phrase "{phrase}" --slug "{slug}" --out "{out}"

and ``python train_wake_word.py --stop`` removes a training container (the
worker runs that on a timeout, a cancel and at its first tick after a
start, for one a crashed core left behind).

WHY THIS LIVES ON THE HOST (and is stdlib-only)
===============================================
openWakeWord automatic training is **Linux-only** (``piper-sample-generator``
needs ``espeak-ng`` + a C toolchain), so the actual training runs inside a
Docker-Linux container: on a Linux server's own Docker Engine (the Windows
install's WSL distro included), or Docker Desktop on a Windows/Mac dev box.
The host may not have ``torch`` (it's only a Domovoi server), so this
wrapper imports **nothing outside the standard library**. All it does is sense
the host's capabilities and assemble the correct ``docker run``.

WHAT IT SENSES AT RUN TIME
==========================
1. Is ``docker`` on PATH and is the daemon reachable? (clear error if not)
2. Does this Docker support GPU passthrough? Probed with a real
   ``docker run --gpus all ...`` so a GPU-less host never *errors* on
   ``--gpus`` — we just omit the flag and run on CPU.
3. If GPUs exist, which is **least used**? Picked via
   ``nvidia-smi --query-gpu=index,uuid,memory.used`` so the synthetic speech
   generation (which fills a 16 GB card) stays off the card holding the
   chat model.
4. How many CPUs and how much memory the Docker host has (``docker info``),
   to cap the container so live voice keeps working while it trains.

It then builds the right command:
  * GPU, multiple cards  -> ``--gpus "device=<N>"`` + CUDA_VISIBLE_DEVICES=<uuid>
  * GPU, single card     -> ``--gpus all``
  * No GPU               -> ``--gpus`` OMITTED entirely (works on any host)
  * always: ``--name domovoi-wake-train`` + a label, ``--cpus`` (all but a
    quarter of the host's CPUs, at least two left free), a low
    ``--cpu-shares`` (live voice wins every contended CPU slice),
    ``--memory`` 12g (no swap); the entrypoint also nices itself.

ONE TRAINING AT A TIME, AND NOTHING LEFT RUNNING
================================================
The container has a fixed name and the label ``org.domovoi.wake-train``.
Before a run, any container with that label is removed (the core trains one
word at a time, so one still there is left over from a run that was cut
off). The wrapper stops its container itself when it is told to stop
(SIGTERM, SIGINT, SIGHUP: the worker's timeout or cancel, or systemd
stopping domovoi-core) and when ``WAKE_TRAINER_DEADLINE_SEC`` runs out (the
worker sets it a minute below its own limit). ``docker run`` alone would
leave the container running when the docker client is killed.

Mounts (host path syntax handled for BOTH Windows and POSIX):
  * ``{clips_dir}``           -> ``/data/clips``  (read-only, the positive set)
  * ``dirname({out})``        -> ``/data/models`` (the container writes
                                  ``/data/models/<slug>.onnx``; the bind mount
                                  makes it appear at the host ``{out}`` the
                                  worker then stat()s for success)
  * named volume ``WAKE_TRAINER_CACHE_VOLUME`` -> ``/data/cache`` (the ~17.9 GB
                                  negative/feature dataset, downloaded once,
                                  reused forever)

It streams the container's stdout/stderr straight through and **returns the
container's exit code unchanged** — the worker's success contract is
``exit 0 AND <slug>.onnx exists at {out}``, so we must not mask the code.
(124: stopped at the deadline; 128+N: stopped by signal N.)

NOTHING IS HARDCODED TO ONE MACHINE: no fixed GPU index, no ``~/.domovoi`` path
baked in (``{out}`` and ``{clips_dir}`` arrive as args; ``models_dir`` is
derived here as ``dirname(out)``), no assumption a GPU (or even Docker on a
particular OS) exists.

The ``WAKE_TRAINER_*`` names below are read from the process environment.
The core's worker puts them there from its settings (``domovoi/.env`` or
Settings → Configuration → Wake words), so a household never needs a shell.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path, PureWindowsPath

# ── Tunables (env-overridable; sensible defaults so the bare template works) ──
# Image name/tag. Override WAKE_TRAINER_IMAGE to use a registry-published image
# so sharers `docker pull` instead of building locally — see
# scripts/wake_word/DOCKER_TRAINER.md.
IMAGE = os.environ.get("WAKE_TRAINER_IMAGE", "domovoi-wake-trainer:latest")
# Named Docker volume holding the one-time ~17.9 GB negative/feature dataset.
# A NAMED volume (not a bind mount) because Windows C:\ bind mounts are
# dramatically slower for the I/O-heavy feature reads.
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
# The container's name: fixed, so Docker itself refuses a second one.
CONTAINER = os.environ.get("WAKE_TRAINER_CONTAINER", "domovoi-wake-train")
# CPUs the container may use ("" = all but a quarter of the Docker host's,
# with at least two left free; see resource_limits), its CPU weight against
# everything else (1024 is Docker's default; 128 means the core, Whisper and
# Ollama win every contended slice), an optional fixed set of CPUs
# ("16-31"), and its memory cap (no swap on top: a run that outgrows it
# stops with an out-of-memory error instead of swapping the box to a crawl).
CPUS = os.environ.get("WAKE_TRAINER_CPUS", "")
CPU_SHARES = os.environ.get("WAKE_TRAINER_CPU_SHARES", "128")
CPUSET = os.environ.get("WAKE_TRAINER_CPUSET", "")
MEMORY = os.environ.get("WAKE_TRAINER_MEMORY", "12g")
# Seconds this wrapper may run before it stops the container itself ("" =
# no limit of its own). The core's worker sets it a minute under its own
# limit, so the container is gone before the worker gives up on the run.
DEADLINE_SEC = os.environ.get("WAKE_TRAINER_DEADLINE_SEC", "")
# "1" when the trainer was set up for a GPU: a training then refuses to run
# on the CPU (exit 75) when the GPU can't be reached, instead of taking
# over an hour under a limit sized for the GPU.
REQUIRE_GPU = os.environ.get("WAKE_TRAINER_REQUIRE_GPU", "") == "1"
EXIT_NO_GPU = 75
# Pronunciations for the phrase's words from the core's own phonemizer
# ("domovoi=D OW1 M OW2 V OY2;..."), passed to the container's
# train_runner.py, which prefers them to espeak-ng's.
PRONUNCIATIONS = os.environ.get("WAKE_PRONUNCIATIONS", "")
# The same words as IPA in the voice's symbols ("domovoi=dˈoʊmoʊvˌɔɪ"): the
# synthetic positives are spoken from these (train_runner.py).
SYNTH_IPA = os.environ.get("WAKE_SYNTH_IPA", "")

LABEL = "org.domovoi.wake-train"

_CPUSET_RE = re.compile(r"^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$")
_MEMORY_RE = re.compile(r"^[0-9]+(\.[0-9]+)?[bkmg]?$", re.IGNORECASE)
_PRON_RE = re.compile(r"^[a-z0-9'.=; A-Z-]*$")
# word=IPA;...: plain words, IPA without blanks, quotes or shell characters.
_SYNTH_RE = re.compile(r"^[a-z0-9'.-]+=[^\s=;\"'`$\\]{1,64}(;[a-z0-9'.-]+=[^\s=;\"'`$\\]{1,64})*$")


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


def docker_host_resources() -> tuple[int, int]:
    """(CPUs, memory bytes) of the Docker host as the daemon sees it (Docker
    Desktop's VM can differ from Windows' own count), or (0, 0)."""
    try:
        r = subprocess.run(
            [*docker_argv(), "info", "--format", "{{.NCPU}} {{.MemTotal}}"],
            capture_output=True, text=True, timeout=30,
        )
        ncpu, mem = r.stdout.split()
        return int(ncpu), int(mem)
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0, 0


def _cpuset_size(cpuset: str) -> int:
    """How many CPUs a list like "0-3,8" names."""
    n = 0
    for part in cpuset.split(","):
        lo, _, hi = part.partition("-")
        n += (int(hi) - int(lo) + 1) if hi else 1
    return max(0, n)


def resource_limits(ncpu: int, mem_bytes: int, *, cpus: str = "", cpu_shares: str = "128",
                    cpuset: str = "", memory: str = "12g") -> list[str]:
    """The ``docker run`` flags that keep live voice working while a wake
    word trains: a CPU cap that leaves the core, Whisper and Ollama their
    own cores (a quarter of the host's, at least two), a low CPU weight,
    an optional fixed CPU set, and a memory cap with no swap on top.
    ``ncpu``/``mem_bytes`` are the Docker host's (0 = unknown); the other
    values are the WAKE_TRAINER_* knobs (empty = the defaults here). A
    malformed knob is left out, never passed to docker."""
    flags: list[str] = []
    want = 0.0
    if cpus:
        try:
            want = float(cpus)
        except ValueError:
            log(f"WAKE_TRAINER_CPUS={cpus!r} is not a number; using the default")
    if want <= 0 and ncpu > 0:
        want = float(max(1, ncpu - max(2, ncpu // 4)))
    pinned = _cpuset_size(cpuset) if cpuset and _CPUSET_RE.match(cpuset) else 0
    if want > 0:
        if ncpu > 0:
            want = min(want, float(ncpu))
        if pinned:
            want = min(want, float(pinned))
        flags += ["--cpus", f"{want:g}"]
    if cpu_shares:
        if cpu_shares.isdigit() and 2 <= int(cpu_shares) <= 262144:
            flags += ["--cpu-shares", cpu_shares]
        else:
            log(f"WAKE_TRAINER_CPU_SHARES={cpu_shares!r} is not 2-262144; left out")
    if cpuset:
        if _CPUSET_RE.match(cpuset):
            flags += ["--cpuset-cpus", cpuset]
        else:
            log(f"WAKE_TRAINER_CPUSET={cpuset!r} is not a CPU list like 16-31; left out")
    if memory:
        if _MEMORY_RE.match(memory):
            # Never more than the Docker host minus 6 GiB for the core,
            # Whisper, Ollama and Postgres (at least 4 GiB for training).
            want_b = _size_bytes(memory)
            if mem_bytes > 0 and want_b > mem_bytes - 6 * 2**30:
                capped = max(4 * 2**30, mem_bytes - 6 * 2**30)
                if capped < want_b:
                    log(f"memory cap {memory} is more than this Docker host can spare; "
                        f"using {capped // 2**20}m")
                    memory = f"{capped // 2**20}m"
            flags += ["--memory", memory, "--memory-swap", memory]
        else:
            log(f"WAKE_TRAINER_MEMORY={memory!r} is not a size like 12g; left out")
    return flags


def _size_bytes(size: str) -> int:
    unit = size[-1].lower() if size[-1].isalpha() else "b"
    num = float(size[:-1] if size[-1].isalpha() else size)
    return int(num * {"b": 1, "k": 2**10, "m": 2**20, "g": 2**30}[unit])


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

    Uses our own image (already built) so we don't pull anything extra. The
    probe container carries the training label, so ``--stop`` removes it too.
    """
    try:
        r = subprocess.run(
            [*docker_argv(), "run", "--rm", "--label", f"{LABEL}=probe", "--gpus", "all",
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


# WSL puts nvidia-smi here, outside the PATH a systemd unit gets (the core's
# own GPU probe has the same fallback, domovoi/handlers/homelab.py).
WSL_NVIDIA_SMI = "/usr/lib/wsl/lib/nvidia-smi"


def nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if os.path.isfile(WSL_NVIDIA_SMI) and os.access(WSL_NVIDIA_SMI, os.X_OK):
        return WSL_NVIDIA_SMI
    return None


def select_gpu() -> tuple[str, int, str]:
    """Choose the LEAST-USED GPU: returns (device, count, uuid).

    We shell out to the HOST's nvidia-smi (not inside a container) to rank cards
    by used memory, so a multi-tenant box doesn't pile this run onto a card
    another job (Ollama) is hammering. Returns:
      * ('all', 1, uuid)       -> single GPU, use --gpus all
      * (str(index), n, uuid)  -> multi-GPU, use --gpus "device=<index>" AND
                                  CUDA_VISIBLE_DEVICES=<uuid> (see build_run_argv)
      * ('', 0, '')            -> nvidia-smi unavailable; caller may still try
                                  --gpus all if the container GPU probe passed.
    """
    smi = nvidia_smi()
    if smi is None:
        return ("", 0, "")
    try:
        r = subprocess.run(
            [smi,
             "--query-gpu=index,uuid,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ("", 0, "")
    if r.returncode != 0:
        return ("", 0, "")

    cards: list[tuple[int, int, str]] = []  # (mem_used_mib, index, uuid)
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            idx, used = int(parts[0]), int(parts[2])
        except ValueError:
            continue
        cards.append((used, idx, parts[1]))

    if not cards:
        return ("", 0, "")
    if len(cards) == 1:
        return ("all", 1, cards[0][2])
    cards.sort()  # least-used memory first
    return (str(cards[0][1]), len(cards), cards[0][2])


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
    cuda_visible: str = "",
    limits: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Assemble the full ``docker run`` argv. ``gpu_flag`` is e.g.
    ['--gpus', 'all'] or ['--gpus', 'device=1'] or [] (CPU); ``limits`` is
    :func:`resource_limits`' flags.

    ``cuda_visible`` (the chosen card's UUID) becomes CUDA_VISIBLE_DEVICES in
    the container. Under WSL2 (Docker Desktop, or Docker Engine in a distro
    with the NVIDIA Container Toolkit) ``--gpus device=N`` does not isolate a
    card: every GPU is reached through /dev/dxg, the container sees them all
    and torch takes cuda:0, whichever card was picked (measured 2026-10-13).
    CUDA honours CUDA_VISIBLE_DEVICES there, and a UUID can't be confused by
    CUDA's and nvidia-smi's different index orders. Harmless on native Linux,
    where the one card the container sees has that UUID."""
    argv: list[str] = [
        *docker_argv(), "run", "--rm",
        # An init as PID 1, so SIGTERM (docker stop, the docker client's
        # signal proxy) reaches the entrypoint, which then removes its
        # copies of the recorded clips; PID 1 itself ignores SIGTERM.
        "--init",
        # A fixed name (Docker refuses a second container by that name) and
        # the label --stop and the next run look for.
        "--name", CONTAINER,
        "--label", f"{LABEL}={slug}",
        # The training DataLoader uses multiprocessing workers that pass tensor
        # batches through /dev/shm. Docker's default shm is only 64 MB, which
        # overflows mid-train ("RuntimeError: No space left on device" on a
        # /torch_* file). Bump it; override via WAKE_TRAINER_SHM_SIZE.
        "--shm-size", SHM_SIZE,
        *limits,
        *gpu_flag,
        "-v", f"{clips_src}:/data/clips:ro",
        "-v", f"{models_src}:/data/models",
        "-v", f"{CACHE_VOLUME}:/data/cache",
        # Pass the training parameters as env into the container's entrypoint.
        "-e", f"WAKE_PHRASE={phrase}",
        "-e", f"WAKE_SLUG={slug}",
    ]
    if cuda_visible:
        argv += ["-e", f"CUDA_VISIBLE_DEVICES={cuda_visible}"]
    if N_SAMPLES:
        argv += ["-e", f"WAKE_N_SAMPLES={N_SAMPLES}"]
    if N_SAMPLES_VAL:
        argv += ["-e", f"WAKE_N_SAMPLES_VAL={N_SAMPLES_VAL}"]
    if STEPS:
        argv += ["-e", f"WAKE_STEPS={STEPS}"]
    if REAL_TARGET_FRACTION:
        argv += ["-e", f"WAKE_REAL_TARGET_FRACTION={REAL_TARGET_FRACTION}"]
    if PRONUNCIATIONS and _PRON_RE.match(PRONUNCIATIONS):
        argv += ["-e", f"WAKE_PRONUNCIATIONS={PRONUNCIATIONS}"]
    if SYNTH_IPA and _SYNTH_RE.match(SYNTH_IPA):
        argv += ["-e", f"WAKE_SYNTH_IPA={SYNTH_IPA}"]
    argv.append(IMAGE)
    return argv


# How long a stopped training container gets to clean up after itself
# (the entrypoint removes its copies of the recorded clips on SIGTERM)
# before it is killed.
STOP_GRACE_SEC = 10
_SLUG_RE = re.compile(r"^[a-z0-9_]{1,80}$")


def _docker(args: list[str], timeout: float) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run([*docker_argv(), *args], capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"docker {args[0]} failed ({e})")
        return None


def remove_training_containers(reason: str) -> int:
    """Stop every container carrying the training label (the probe's
    included): ``docker stop`` first, so a training run gets
    ``STOP_GRACE_SEC`` to remove its copies of the recorded clips, then
    ``docker rm -f``; then :func:`scrub_cache` for whatever a killed run
    left. Returns how many containers it removed. Never raises."""
    r = _docker(["ps", "-aq", "--filter", f"label={LABEL}"], 30)
    ids = r.stdout.split() if r is not None and r.returncode == 0 else []
    if ids:
        _docker(["stop", "-t", str(STOP_GRACE_SEC), *ids], 30 + STOP_GRACE_SEC)
        _docker(["rm", "-f", *ids], 60)
        log(f"removed {len(ids)} training container(s) ({reason})")
    scrub_cache(f"find /data/cache/runs -path '*/positive_train/real_*.wav' -delete",
                "copies of recorded clips")
    return len(ids)


def _cache_volume_exists() -> bool:
    r = _docker(["volume", "inspect", CACHE_VOLUME], 30)
    return r is not None and r.returncode == 0


def scrub_cache(script: str, what: str) -> bool:
    """Run ``script`` (sh) in a throwaway container of the trainer image
    with the cache volume at /data/cache: how the copies of recorded clips
    a stopped or killed run left, or a deleted word's files, are removed.
    Nothing when the volume or the image isn't there (training never
    turned on: never creates the volume). True when it ran."""
    if not _cache_volume_exists():
        return False
    r = _docker(["image", "inspect", IMAGE], 30)
    if r is None or r.returncode != 0:
        log(f"no {IMAGE} image to remove {what} with")
        return False
    r = _docker(["run", "--rm", "--label", f"{LABEL}-clean=1", "-v", f"{CACHE_VOLUME}:/data/cache",
                 "--entrypoint", "sh", IMAGE, "-c", f"mkdir -p /data/cache/runs && {script}"], 300)
    ok = r is not None and r.returncode == 0
    if not ok:
        log(f"could not remove {what} from {CACHE_VOLUME}")
    return ok


def forget_words(slugs: list[str]) -> bool:
    """Remove these words' run folders (synthetic clips, features, any
    copies of recordings) from the cache volume: a deleted wake word."""
    slugs = [x for x in slugs if _SLUG_RE.match(x)]
    if not slugs:
        return True
    return scrub_cache("rm -rf " + " ".join(f"/data/cache/runs/{x}" for x in slugs),
                       "the deleted words' training files")


def keep_only_words(slugs: list[str]) -> bool:
    """Remove every run folder but these words' (wake words deleted while
    the core couldn't be told)."""
    keep = " ".join(x for x in slugs if _SLUG_RE.match(x))
    script = ('for d in /data/cache/runs/*; do [ -d "$d" ] || continue; '
              'case " ' + keep + ' " in *" ${d##*/} "*) ;; *) rm -rf "$d" ;; esac; done')
    return scrub_cache(script, "deleted words' training files")


class _Stopped(Exception):
    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def _on_signal(signum, _frame) -> None:  # pragma: no cover - exercised via a subprocess
    raise _Stopped(signum)


def _install_signal_handlers() -> None:
    for name in ("SIGTERM", "SIGINT", "SIGHUP", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _on_signal)
            except (OSError, ValueError):
                pass


def _ignore_signals() -> None:
    for name in ("SIGTERM", "SIGINT", "SIGHUP", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, signal.SIG_IGN)
            except (OSError, ValueError):
                pass


def run_container(run_argv: list[str], deadline_sec: float | None) -> int:
    """Run ``docker run`` (output straight through) and return its exit
    code. On a stop signal or at the deadline the container is removed
    first: 128+signal, or 124 at the deadline."""
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(run_argv)
        if deadline_sec is None:
            return proc.wait()
        try:
            return proc.wait(timeout=max(1.0, deadline_sec))
        except subprocess.TimeoutExpired:
            _ignore_signals()
            log(f"stopped after {int(deadline_sec)} s: the training time limit "
                f"(Settings > Configuration > Wake words) ran out")
            remove_training_containers("time limit")
            _reap(proc)
            return 124
    except _Stopped as e:
        _ignore_signals()
        log(f"told to stop (signal {e.signum}); removing the training container")
        remove_training_containers("stopped")
        if proc is not None:
            _reap(proc)
        return 128 + e.signum


def _reap(proc: subprocess.Popen) -> None:
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Portable openWakeWord docker trainer wrapper")
    ap.add_argument("--stop", action="store_true",
                    help="remove any training container and the copies of recorded clips "
                         "it left, and exit (the worker's timeout/cancel/stop)")
    ap.add_argument("--forget", action="append", default=[], metavar="SLUG",
                    help="remove a deleted word's training files from the cache volume")
    ap.add_argument("--keep-only", metavar="SLUG,SLUG",
                    help="remove the training files of every word not listed")
    ap.add_argument("--clips", help="host positive-clips dir ({clips_dir})")
    ap.add_argument("--phrase", help="wake phrase, e.g. 'hey domovoi' ({phrase})")
    ap.add_argument("--slug", help="model slug; output is <slug>.onnx ({slug})")
    ap.add_argument("--out", help="host output path <...>/<slug>.onnx ({out})")
    args = ap.parse_args()

    if args.stop or args.forget or args.keep_only is not None:
        ok = True
        if args.stop:
            if not remove_training_containers("asked to stop"):
                log("no training container to stop")
        if args.forget:
            ok = forget_words(args.forget) and ok
        if args.keep_only is not None:
            ok = keep_only_words([x for x in args.keep_only.split(",") if x]) and ok
        return 0 if ok else 1
    missing = [f"--{k}" for k in ("clips", "phrase", "slug", "out") if not getattr(args, k)]
    if missing:
        ap.error("the following arguments are required: " + ", ".join(missing))

    started = time.monotonic()
    _install_signal_handlers()
    deadline: float | None = None
    if DEADLINE_SEC:
        try:
            deadline = float(DEADLINE_SEC)
        except ValueError:
            log(f"WAKE_TRAINER_DEADLINE_SEC={DEADLINE_SEC!r} is not a number; no deadline")
        if deadline is not None and deadline <= 0:
            deadline = None

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

    try:
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
            log(f"ERROR: image {IMAGE!r} not found locally. Turn training on in "
                f"Settings > Wake Words (it builds the image), or build it once:\n"
                f"  docker build -t {IMAGE} scripts/wake_word/docker\n"
                f"See scripts/wake_word/DOCKER_TRAINER.md.")
            return 3

        # One training at a time: anything still carrying the label is left
        # over from a run that was cut off.
        remove_training_containers("left over from an earlier run")

        # (3) Decide GPU vs CPU. Two independent signals must agree for GPU:
        #     (a) the container can actually use --gpus (gpu_runtime_available), and
        #     (b) we can pick a card (or it's a single-GPU host).
        gpu_flag: list[str] = []
        cuda_visible = ""
        backend = "CPU"
        if gpu_runtime_available():
            device, count, uuid = select_gpu()
            if count > 1 and device not in ("", "all"):
                gpu_flag = ["--gpus", f"device={device}"]
                cuda_visible = uuid
                backend = f"GPU device {device} {uuid} (of {count}, least-used)"
            else:
                # Single GPU, or nvidia-smi unavailable but the container probe
                # passed -> let Docker expose all GPUs.
                gpu_flag = ["--gpus", "all"]
                backend = "GPU all" if count <= 1 else "GPU all (smi unavailable)"
        elif REQUIRE_GPU:
            log("ERROR: training was set up for the GPU, and Docker can't reach it now "
                "(the GPU probe above says why). Not training on the CPU: it would take "
                "over an hour under a time limit sized for the GPU. Settings > Wake Words "
                "> Check the GPU and trainer again (Restart Domovoi first if the NVIDIA "
                "Container Toolkit was lost), or turn training on again with the CPU.")
            return EXIT_NO_GPU
        else:
            log("No usable GPU runtime detected; training on CPU (at the defaults "
                "about 75 min on 8 cores against about 20 min on an RTX 4070 Ti "
                "SUPER, measured 2026-10-13; the synthetic speech generation is "
                "the slow part). The same image runs CPU-only when --gpus is "
                "omitted. On Docker Engine with an NVIDIA card, install the "
                "NVIDIA Container Toolkit (DOCKER_TRAINER.md).")

        ncpu, mem = docker_host_resources()
        limits = resource_limits(ncpu, mem, cpus=CPUS, cpu_shares=CPU_SHARES,
                                 cpuset=CPUSET, memory=MEMORY)

        # (4) Build + run.
        clips_src = to_docker_mount_source(args.clips)
        models_src = to_docker_mount_source(str(models_dir))
        run_argv = build_run_argv(clips_src, models_src, args.phrase, slug, gpu_flag,
                                  cuda_visible, limits)

        # (5) Log the chosen backend + the exact command (stderr).
        log(f"backend = {backend}")
        log(f"image   = {IMAGE}")
        log(f"limits  = {' '.join(limits) or 'none'} (Docker host: {ncpu} CPUs)")
        log(f"clips   = {clips_src} -> /data/clips:ro")
        log(f"models  = {models_src} -> /data/models  (expecting /data/models/{slug}.onnx)")
        log(f"cache   = {CACHE_VOLUME} -> /data/cache")
        log("running: " + " ".join(run_argv))

        # Stream stdout/stderr straight through (inherit this process's fds) and
        # return the container's exit code UNCHANGED — the worker's success gate is
        # exit 0 + <slug>.onnx present at {out}, so we must not remap the code.
        remaining = None if deadline is None else deadline - (time.monotonic() - started)
        try:
            return run_container(run_argv, remaining)
        except OSError as e:
            log(f"ERROR: failed to launch docker: {e}")
            return 3
    except _Stopped as e:
        # Told to stop while still probing: nothing of ours may be left.
        _ignore_signals()
        remove_training_containers("stopped")
        return 128 + e.signum


if __name__ == "__main__":
    sys.exit(main())
