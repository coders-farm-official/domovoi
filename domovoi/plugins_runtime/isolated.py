"""Decode-only plugin containers (core 1.5.0, design FRIEND-HOUSES-PLUGINS §7).

A plugin that declares ``[isolated] decoder = "<pkg>.<sub>.<module>:<fn>"``
gets a decoder that runs **out of the core's process**, in a container of
its own. Untrusted bytes (what a friend house sends, and any other input a
plugin chooses to route here) are decoded only there; the plugin's
in-process code sees only the canonical JSON the core hands back. The mode
is generic. In 1.5.0 a plugin with ``permissions.friend_houses`` must use it
(:func:`domovoi.plugins_runtime.manifest.parse_manifest` refuses it
otherwise).

**The image is the core's,** made the way it makes the Friend Houses
listener's (:mod:`domovoi.friend_houses.listener_runtime`): a build context
the core writes itself (:func:`context_tar`), never a plugin-supplied
Dockerfile.

* the Dockerfile comes from :data:`DOCKERFILE_TEMPLATE`, on the same
  digest-pinned Debian base as the listener and Tor images;
* Python and the ``[isolated] debian`` packages come from Debian, and only
  packages on :data:`~domovoi.plugins_runtime.manifest.ISOLATED_DEBIAN_ALLOWLIST`;
  no pip, ever;
* the plugin contributes only the ``.py`` files of its decoder subpackage
  (:func:`~domovoi.plugins_runtime.manifest.isolated_sources`); the
  package's top ``__init__.py`` is an empty file the core writes, so none
  of the plugin's in-process code is copied;
* the context is byte-for-byte reproducible, and its hash (a label on the
  image) decides whether the image is current: an upgrade changes the
  files, so the next start rebuilds it, as does age (:data:`MAX_AGE_DAYS`);
* the container guard trusts exactly the image ID the core built
  (:func:`domovoi.container_guard.engine.note_decoder_image`).

**The container** has the listener's wall (:func:`run_args`): no network,
read-only root, no capabilities, no new privileges, a non-root user, caps
on memory, processes and CPU, a small ``noexec`` tmpfs, no mounts, no
environment, no logs. It talks over stdin and stdout only.

**The IPC** (:class:`IsolatedDecoder`): one request at a time, each a
length-prefixed frame with a request id
(:mod:`domovoi.plugins_runtime.isolated_runner`). Every reply is checked
here: frame size, id, then the JSON itself (:func:`canonical`: UTF-8,
depth, no NaN or infinities, no duplicate keys, sane integers, size) and
re-serialized by the core. A timeout, a crash, a short or oversize frame or
a wrong id is a refused item and a restart of the container with backoff;
bad JSON in a well-formed frame is a refused item. Nothing a decoder does
raises into the caller.

**Lifecycle:** :func:`start_for` when the plugin loads (enable, install,
boot), :func:`stop_for` when it unloads (disable, uninstall, upgrade,
shutdown), :func:`forget` on uninstall (the image goes too). An upgrade
needs nothing more: the new files change the context hash, so the start
after it rebuilds the image.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import os
import struct
import sys
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from domovoi.container_guard.model import DECODER_CONTAINER_PREFIX
from domovoi.friend_houses.tor_runtime import DockerFailed, _parse_created
from domovoi.friend_houses import tor_runtime
from domovoi.plugins_runtime.manifest import (
    ISOLATED_DEBIAN_ALLOWLIST,
    PluginManifest,
    isolated_sources,
)

log = logging.getLogger(__name__)

# The same pinned base as domovoi/Dockerfile.fh-listener and Dockerfile.tor
# (a test keeps the three equal).
BASE_IMAGE = (
    "debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2"
)
BUILD_LABEL = "org.domovoi.build-sha256"
ROLE = "plugin-decoder"
MAX_AGE_DAYS = 30
BUILD_TIMEOUT_SEC = 900.0
APP_DIR = "/opt/domovoi-isolated"
RUNNER_NAME = "domovoi_isolated_runner.py"
RUNNER_SOURCE = Path(__file__).resolve().parent / "isolated_runner.py"
# Nobody: owns nothing in the image, and the container mounts nothing.
CONTAINER_USER = "65534:65534"

# Caps on the IPC (the runner keeps the same numbers).
PAYLOAD_MAX = 64 * 1024
META_MAX = 1024
REPLY_MAX = 64 * 1024
REASON_MAX = 200
DEPTH_MAX = 16
INT_MAX = 2**63 - 1
DECODE_TIMEOUT_SEC = 5.0
BACKOFF_FIRST_SEC = 1.0
BACKOFF_MAX_SEC = 60.0

DOCKERFILE_TEMPLATE = """\
# A plugin's decode-only container, written by the Domovoi core
# (domovoi/plugins_runtime/isolated.py), never by the plugin. Python and the
# packages below come from Debian; the plugin adds only its decoder's .py
# files under {app}/.
FROM {base}

RUN apt-get update \\
    && apt-get upgrade -y \\
    && apt-get install -y --no-install-recommends {packages} \\
    && rm -rf /var/lib/apt/lists/*

COPY app/ {app}/
RUN chmod -R a+rX,go-w {app} && python3 -m compileall -q {app}

LABEL org.domovoi.role="{role}"
USER {user}
ENTRYPOINT ["python3", "-I", "-B", "{app}/{runner}"]
"""

# What the docker CLI that runs a decoder may see of the core's
# environment: enough to find Docker. The container gets none of it.
_DOCKER_ENV = ("PATH", "LANG", "LC_ALL", "TZ", "HOME", "XDG_RUNTIME_DIR",
               "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "SYSTEMROOT")


def container_name(slug: str) -> str:
    return f"{DECODER_CONTAINER_PREFIX}{slug}"


def image_tag(slug: str) -> str:
    return f"{DECODER_CONTAINER_PREFIX}{slug}:latest"


async def _docker(*args: str, timeout: float = 60.0, stdin: bytes | None = None) -> tuple[int, str, str]:
    """The docker CLI (a seam the tests replace)."""
    return await tor_runtime._docker(*args, timeout=timeout, stdin=stdin)


# ─── the image ────────────────────────────────────────────────────────────

class IsolatedBuildError(ValueError):
    """The plugin's decoder files can't go into an image (a problem the
    installer would have refused; here when the files changed since)."""


def dockerfile(manifest: PluginManifest) -> str:
    assert manifest.isolated is not None
    # Filtered through the allowlist again: only names the core knows ever
    # reach the Dockerfile.
    extra = [p for p in manifest.isolated.debian if p in ISOLATED_DEBIAN_ALLOWLIST]
    return DOCKERFILE_TEMPLATE.format(
        base=BASE_IMAGE, packages=" ".join(["python3", *sorted(extra)]),
        app=APP_DIR, role=ROLE, user=CONTAINER_USER, runner=RUNNER_NAME,
    )


def context_tar(install_dir: Path, manifest: PluginManifest) -> bytes:
    """The build context: the core's Dockerfile and runner, ``decoder.json``,
    an empty top ``__init__.py`` and the decoder subpackage's ``.py`` files.
    The same files give the same bytes (fixed times, owners and order)."""
    assert manifest.isolated is not None
    files, problems = isolated_sources(install_dir, manifest)
    if problems:
        raise IsolatedBuildError("; ".join(problems))
    spec = json.dumps({"module": manifest.isolated.module, "function": manifest.isolated.function},
                      sort_keys=True).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        def add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))

        add("Dockerfile", dockerfile(manifest).encode())
        add(f"app/{RUNNER_NAME}", RUNNER_SOURCE.read_bytes())
        add("app/decoder.json", spec)
        add(f"app/{manifest.package_name}/__init__.py", b"")
        for rel, path in files:
            add(f"app/{rel}", path.read_bytes())
    return buf.getvalue()


def build_sha(context: bytes) -> str:
    return hashlib.sha256(context).hexdigest()


def build_args(slug: str, sha: str, version: str) -> list[str]:
    """``docker build`` with the context piped in. ``--pull --no-cache``:
    a rebuild runs apt again, so Debian's fixes get in."""
    return ["build", "--pull", "--no-cache", "-t", image_tag(slug),
            "--label", f"{BUILD_LABEL}={sha}",
            "--label", f"org.domovoi.plugin={slug}",
            "--label", f"org.domovoi.plugin-version={version}", "-"]


@dataclass(frozen=True)
class ImageInfo:
    image_id: str
    build_sha: str | None
    created: datetime | None

    def stale(self, want: str, *, now: datetime | None = None) -> str | None:
        """Why the image should be rebuilt, or None."""
        if self.build_sha != want:
            return "the plugin's decoder changed"
        if self.created is None:
            return "its age is unknown"
        age = (now or datetime.now(timezone.utc)) - self.created
        if age.days >= MAX_AGE_DAYS:
            return f"it is {age.days} days old"
        return None


async def image_info(slug: str) -> ImageInfo | None:
    rc, out, _ = await _docker(
        "image", "inspect", "-f",
        "{{.Id}}|{{index .Config.Labels \"" + BUILD_LABEL + "\"}}|{{.Created}}",
        image_tag(slug), timeout=15.0,
    )
    if rc != 0 or "|" not in out:
        return None
    image_id, sha, created = (out.strip().split("|") + ["", ""])[:3]
    return ImageInfo(image_id, sha or None, _parse_created(created))


async def ensure_image(slug: str, install_dir: Path, manifest: PluginManifest) -> str:
    """The image ID to run, built or rebuilt when needed. The image a
    rebuild replaces is removed (the core built it; nothing else uses it)."""
    context = context_tar(install_dir, manifest)
    want = build_sha(context)
    info = await image_info(slug)
    reason = "it isn't built yet" if info is None else info.stale(want)
    if reason is None:
        assert info is not None
        return info.image_id
    log.info("plugin %s: building its decoder image: %s", slug, reason)
    rc, _, err = await _docker(*build_args(slug, want, manifest.version),
                               timeout=BUILD_TIMEOUT_SEC, stdin=context)
    if rc != 0:
        last = err.strip().splitlines()[-1:] if err.strip() else []
        raise DockerFailed(f"building the {slug} decoder failed" + (": " + last[0][:200] if last else ""))
    built = await image_info(slug)
    if built is None:
        raise DockerFailed(f"the {slug} decoder was built but can't be found")
    try:
        from domovoi.container_guard.engine import note_decoder_image

        await note_decoder_image(slug, built.image_id)
    except Exception as e:  # noqa: BLE001 — the guard is told best-effort
        log.warning("plugin %s: the decoder image wasn't recorded for the container guard: %s", slug, e)
    if info is not None and info.image_id != built.image_id:
        await _docker("image", "rm", info.image_id, timeout=60.0)
    return built.image_id


def run_args(slug: str, image_id: str) -> list[str]:
    """The ``docker run`` arguments. A pure function so tests pin each one."""
    return [
        "run", "-i", "--rm", "--name", container_name(slug),
        "--network", "none",
        "--user", CONTAINER_USER,
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--memory", "256m", "--memory-swap", "256m", "--pids-limit", "32", "--cpus", "0.5",
        "--ipc", "none",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=8m",
        "--log-driver", "none",
        "--label", "org.domovoi.owner=core", "--label", f"org.domovoi.role={ROLE}",
        "--label", f"org.domovoi.plugin={slug}",
        image_id,
    ]


def docker_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _DOCKER_ENV if k in os.environ}
    env.setdefault("LANG", "C.UTF-8")
    return env


async def remove_container(slug: str) -> None:
    rc, _, err = await _docker("rm", "-f", container_name(slug), timeout=40.0)
    if rc != 0 and "No such container" not in err:
        raise DockerFailed(f"removing the {slug} decoder container failed: " + err.strip()[:200])


async def remove_image(slug: str) -> None:
    rc, _, err = await _docker("image", "rm", image_tag(slug), timeout=60.0)
    if rc != 0 and "No such image" not in err:
        log.warning("plugin %s: its decoder image wasn't removed: %s", slug, err.strip()[:200])


# ─── the reply checks ─────────────────────────────────────────────────────

def _depth_ok(data: str, limit: int = DEPTH_MAX) -> bool:
    """Nesting of ``[`` and ``{`` outside strings stays within ``limit``
    (checked before parsing, so a deep reply never reaches the parser)."""
    depth, in_str, esc = 0, False, False
    for ch in data:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
            if depth > limit:
                return False
        elif ch in "]}":
            depth -= 1
    return True


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def _finite(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("a number out of range")
    return value


def _bounded_int(text: str) -> int:
    if len(text) > 20:
        raise ValueError("an integer out of range")
    value = int(text)
    if abs(value) > INT_MAX:
        raise ValueError("an integer out of range")
    return value


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("a duplicate key")
        out[k] = v
    return out


def canonical(data: bytes) -> tuple[Any, bytes]:
    """``(value, canonical bytes)`` from a decoder's JSON reply, or
    ValueError. The value is parsed from the core's own re-serialization,
    so nothing of the decoder's bytes survives."""
    if len(data) > REPLY_MAX:
        raise ValueError("the reply is too large")
    text = data.decode("utf-8")  # strict: UnicodeDecodeError is a ValueError
    if not _depth_ok(text):
        raise ValueError(f"the reply is nested deeper than {DEPTH_MAX}")
    value = json.loads(text, parse_constant=_no_constant, parse_float=_finite,
                       parse_int=_bounded_int, object_pairs_hook=_no_duplicates)
    out = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")  # a lone surrogate fails here
    if len(out) > REPLY_MAX:
        raise ValueError("the reply is too large")
    return json.loads(out), out


# ─── the decoder process ──────────────────────────────────────────────────

@dataclass(frozen=True)
class DecodeResult:
    """One item's outcome. ``ok`` False is a refused item; ``reason`` says
    why in words safe to log (never the decoder's own bytes)."""

    ok: bool
    value: Any = None
    canonical: bytes = b""
    reason: str = ""


class _Broken(Exception):
    """The stream can't be trusted any more: restart the container."""


Spawn = Callable[[list[str]], Awaitable[Any]]


async def _spawn_docker(argv: list[str]) -> Any:
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":  # pragma: no cover
        import subprocess

        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return await asyncio.create_subprocess_exec(
        "docker", *argv, env=docker_env(),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, **kwargs,
    )


class IsolatedDecoder:
    """One plugin's decode-only container, and the requests to it."""

    def __init__(
        self,
        slug: str,
        *,
        install_dir: Path,
        manifest: PluginManifest,
        spawn: Spawn | None = None,
        prepare: Callable[[], Awaitable[str]] | None = None,
        timeout: float = DECODE_TIMEOUT_SEC,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.slug = slug
        self.install_dir = install_dir
        self.manifest = manifest
        self._spawn = spawn or _spawn_docker
        self._prepare = prepare or self._prepare_image
        self.timeout = timeout
        self._clock = clock
        self.image_id: str | None = None
        self.state = "stopped"          # stopped | starting | ready | backoff | failed
        self.problem: str | None = None
        self._proc: Any = None
        self._stderr_task: asyncio.Task | None = None
        self._bring_up: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._next_id = 0
        self._backoff = BACKOFF_FIRST_SEC
        self._retry_at = 0.0
        self.restarts = 0

    # ── start / stop ──
    async def _prepare_image(self) -> str:
        image_id = await ensure_image(self.slug, self.install_dir, self.manifest)
        await remove_container(self.slug)
        return image_id

    def start_soon(self) -> None:
        """Build (when needed) and start in the background: an image build
        can take minutes, and the plugin's load doesn't wait for it."""
        if self._bring_up is None or self._bring_up.done():
            self.state = "starting"
            self._bring_up = asyncio.ensure_future(self.start())

    async def start(self) -> None:
        try:
            self.image_id = await self._prepare()
        except (DockerFailed, IsolatedBuildError, OSError) as e:
            self.state, self.problem = "failed", str(e)[:300]
            log.warning("plugin %s: its decoder couldn't be set up: %s", self.slug, e)
            return
        async with self._lock:
            await self._run()

    async def _run(self) -> None:
        """Start the container (the lock is held)."""
        assert self.image_id is not None
        try:
            self._proc = await self._spawn(run_args(self.slug, self.image_id))
        except OSError as e:
            self._schedule_retry(f"the decoder couldn't start: {e}")
            return
        self.state, self.problem = "ready", None
        if getattr(self._proc, "stderr", None) is not None:
            self._stderr_task = asyncio.ensure_future(self._drain_stderr(self._proc))

    async def _drain_stderr(self, proc: Any) -> None:
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    return
                log.info("plugin %s decoder: %s", self.slug,
                         line[:300].decode("utf-8", "replace").rstrip())
        except (ValueError, ConnectionError, asyncio.LimitOverrunError):
            return

    async def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None
        if proc is not None:
            try:
                if getattr(proc, "stdin", None) is not None:
                    proc.stdin.close()
                if proc.returncode is None:
                    proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=10.0)
            except (ProcessLookupError, OSError, asyncio.TimeoutError):
                pass
        try:
            # Killing the docker client doesn't stop a hung container.
            await remove_container(self.slug)
        except (DockerFailed, OSError) as e:
            log.warning("plugin %s: %s", self.slug, e)

    def _schedule_retry(self, why: str) -> None:
        self.state, self.problem = "backoff", why
        self._retry_at = self._clock() + self._backoff
        self._backoff = min(self._backoff * 2, BACKOFF_MAX_SEC)
        self.restarts += 1
        log.warning("plugin %s: %s; starting it again in %.0f s", self.slug, why,
                    self._retry_at - self._clock())

    async def stop(self) -> None:
        if self._bring_up is not None and not self._bring_up.done():
            self._bring_up.cancel()
            try:
                await self._bring_up
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        async with self._lock:
            await self._kill()
            self.state = "stopped"

    # ── requests ──
    async def decode(self, payload: bytes, meta: dict[str, Any] | None = None) -> DecodeResult:
        """Decode one item. Never raises: anything wrong is a refusal."""
        if not isinstance(payload, (bytes, bytearray)) or len(payload) > PAYLOAD_MAX:
            return DecodeResult(False, reason="the item is too large")
        try:
            meta_bytes = json.dumps(meta or {}, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()
        except (TypeError, ValueError):
            return DecodeResult(False, reason="bad meta")
        if len(meta_bytes) > META_MAX:
            return DecodeResult(False, reason="the meta is too large")
        async with self._lock:
            if self._proc is None:
                if self.state == "backoff" and self.image_id and self._clock() >= self._retry_at:
                    await self._run()
                if self._proc is None:
                    return DecodeResult(False, reason=f"the decoder isn't running ({self.state})")
            self._next_id = (self._next_id + 1) & 0xFFFFFFFF
            rid = self._next_id
            body = struct.pack(">IH", rid, len(meta_bytes)) + meta_bytes + bytes(payload)
            try:
                status, data = await asyncio.wait_for(self._exchange(rid, body), timeout=self.timeout)
            except asyncio.TimeoutError:
                await self._fail("the decoder took too long")
                return DecodeResult(False, reason="timed out")
            except _Broken as e:
                await self._fail(str(e))
                return DecodeResult(False, reason=str(e))
            except (ConnectionError, OSError, asyncio.IncompleteReadError):
                await self._fail("the decoder stopped")
                return DecodeResult(False, reason="the decoder stopped")
        if status != 0:
            return DecodeResult(False, reason="refused by the decoder: "
                                + data[:REASON_MAX].decode("utf-8", "replace"))
        try:
            value, out = canonical(data)
        except (ValueError, RecursionError) as e:
            return DecodeResult(False, reason=f"bad output: {e}"[:REASON_MAX])
        self._backoff = BACKOFF_FIRST_SEC
        return DecodeResult(True, value=value, canonical=out)

    async def _exchange(self, rid: int, body: bytes) -> tuple[int, bytes]:
        proc = self._proc
        proc.stdin.write(struct.pack(">I", len(body)) + body)
        await proc.stdin.drain()
        head = await proc.stdout.readexactly(4)
        (size,) = struct.unpack(">I", head)
        if size < 5 or size > REPLY_MAX + 5:
            raise _Broken("the decoder's reply has a bad size")
        reply = await proc.stdout.readexactly(size)
        got, status = struct.unpack(">IB", reply[:5])
        if got != rid:
            raise _Broken("the decoder answered another request")
        if status not in (0, 1):
            raise _Broken("the decoder's reply has a bad status")
        return status, reply[5:]

    async def _fail(self, why: str) -> None:
        await self._kill()
        self._schedule_retry(why)

    def status(self) -> dict[str, Any]:
        return {"slug": self.slug, "state": self.state, "problem": self.problem,
                "image_id": self.image_id, "restarts": self.restarts}


# ─── the plugins' decoders ────────────────────────────────────────────────

DECODERS: dict[str, IsolatedDecoder] = {}


def start_for(slug: str, install_dir: Path, manifest: PluginManifest) -> IsolatedDecoder | None:
    """The plugin loaded: start its decoder (in the background). None for a
    plugin without ``[isolated]``. Never raises."""
    if manifest.isolated is None:
        return None
    old = DECODERS.get(slug)
    if old is not None and old.state != "stopped":
        log.warning("plugin %s: its decoder was still running; replacing it", slug)
    dec = IsolatedDecoder(slug, install_dir=install_dir, manifest=manifest)
    DECODERS[slug] = dec
    try:
        dec.start_soon()
    except RuntimeError as e:  # no running loop (a sync caller): starts on first use
        dec.state, dec.problem = "failed", str(e)
    return dec


async def stop_for(slug: str) -> None:
    """The plugin unloaded: stop and remove its container. Never raises."""
    dec = DECODERS.pop(slug, None)
    if dec is None:
        return
    try:
        await dec.stop()
    except Exception as e:  # noqa: BLE001 — teardown isolation
        log.warning("plugin %s: stopping its decoder failed: %s", slug, e)


async def forget(slug: str) -> None:
    """The plugin was uninstalled: its container and image go."""
    await stop_for(slug)
    try:
        await remove_container(slug)
    except (DockerFailed, OSError) as e:
        log.warning("plugin %s: %s", slug, e)
    try:
        await remove_image(slug)
    except (DockerFailed, OSError) as e:
        log.warning("plugin %s: its decoder image wasn't removed: %s", slug, e)


async def decode(slug: str, payload: bytes, meta: dict[str, Any] | None = None) -> DecodeResult:
    """Decode with ``slug``'s decoder; refused when it has none running."""
    dec = DECODERS.get(slug)
    if dec is None:
        return DecodeResult(False, reason="the plugin has no decoder running")
    return await dec.decode(payload, meta)
