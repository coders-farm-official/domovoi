"""Decode-only plugin containers (core 1.5.0, design FRIEND-HOUSES-PLUGINS §7).

A plugin that declares ``[isolated] decoder =
"domovoi_isolated_<slug>.<module>:<fn>"`` gets a decoder that runs **out of
the core's process**, in containers of its own. Untrusted bytes (what a
friend house sends, and any other input a plugin chooses to route here) are
decoded only there; the plugin's in-process code sees only the canonical
value the core hands back. The mode is generic. In 1.5.0 a plugin with
``permissions.friend_houses`` must use it
(:func:`domovoi.plugins_runtime.manifest.parse_manifest` refuses it
otherwise). It was reworked after the design review of 2026-11-23 (D1, D2,
D3, D9, D10, D11); the comments below name the finding each rule answers.

**The image is the core's** (D11), made the way it makes the Friend Houses
listener's (:mod:`domovoi.friend_houses.listener_runtime`): a build context
the core writes itself (:func:`context_tar`), never a plugin's Dockerfile.

* the Dockerfile comes from :data:`DOCKERFILE_TEMPLATE`, on the same
  digest-pinned Debian base as the listener and Tor images; apt runs before
  any plugin file is in the context's layers, and the plugin's files are
  only copied and byte-compiled: no plugin code runs at build;
* Python and the ``[isolated] debian`` packages come from Debian, and only
  packages on :data:`~domovoi.plugins_runtime.manifest.ISOLATED_DEBIAN_ALLOWLIST`;
  no pip, ever;
* the plugin contributes only the ``.py`` files of its decoder's own
  top-level package (``domovoi_isolated_<slug>``), so its in-process
  package, ``__init__`` included, is never in the image;
* the context's hash (a label on the image) and its age
  (:data:`MAX_AGE_DAYS`) decide a rebuild, which runs ``--pull
  --no-cache`` so Debian's fixes get in;
* the container guard is told the image ID before any container runs it,
  and an image it has no record of is rebuilt, never run;
* the build waits for :func:`default_gate`: the plugin is loaded (so the
  admin confirmed its install), the internet answer isn't "never", and,
  for a ``friend_houses`` plugin, Friend Houses is on and a friend has
  "Apps with this friend" on.

**One container per (plugin, friend)** (D2): a friend's bytes are only ever
decoded in that friend's own container, started on the friend's first item,
stopped after :data:`IDLE_SEC` idle, at most :data:`MAX_LIVE_PER_PLUGIN` per
plugin and :data:`MAX_LIVE_TOTAL` in all (the least recently used idle one
makes room). The friend's identity never enters the container: the core
keeps it, and the request id, on its own side. One request at a time; a
reply with another id, or any byte the container writes unasked, kills it.

**The container** (D11, :func:`run_args`): no network, read-only root, no
tmpfs, no mounts, no capabilities, no new privileges, a fixed unprivileged
uid, memory with swap equal to it, processes, CPU and file caps, no
environment, no logs. Its stderr goes nowhere (D9).

**Faults** (D3): a timeout, a crash or a broken stream refuses the item
(``outcome`` ``fault``), kills that friend's container, and backs off that
friend only. An item that faulted :data:`ITEM_FAULTS_MAX` times is dropped
for good. :data:`BREAKER_FAULTS` faults from one friend within
:data:`BREAKER_WINDOW_SEC` pause that friend's items for this plugin (the
pause doubles on each repeat) and tell :data:`PAUSE_LISTENERS`; every other
friend carries on.

**Replies** (D9, :func:`canonical`): size, UTF-8, depth, total nodes, keys
per object, key and string length, integer size, no NaN or infinities, no
duplicate keys, no NUL, control characters or lone surrogates; re-serialized
by the core, then checked in process against the schema the plugin
registered (:func:`set_schema`); with none, nothing is accepted.

**Images** (D1, D10): the plugin's decoder never handles one. The core
reassembles an image's pieces as opaque bytes (:class:`ImageChunks`) and
makes an image request itself; the core's helper in the container
(:mod:`domovoi.plugins_runtime.isolated_helper`) opens it with the core's
``open_image`` (PNG, JPEG, WebP; at most 4 megapixels by its header; first
frame) and answers **raw pixels** only. The core checks ``len == width *
height * channels`` and the caps, and writes the PNG itself
(:func:`encode_png`): no encoded byte from the container ever leaves, so
there is no metadata or colour profile to strip. :func:`image_response`
serves it with a fixed type and ``nosniff``.

**The build is memory-capped** (:data:`BUILD_CAPS`) on Docker's classic
builder: BuildKit accepts ``--memory`` and ignores it, so a Docker without
the classic builder gets no build at all (:func:`builder_caps_honoured`).

**Without Docker** the permission is inert: nothing decodes, and every item
is ``unavailable``. There is no in-process decode path in production; the
tests' stand-ins are injected (``spawn``) and no setting reaches them.

**Lifecycle:** :func:`start_for` when the plugin loads (no build, no
container yet), :func:`stop_for` when it unloads, :func:`forget` on
uninstall (the image goes too).
"""

from __future__ import annotations

import asyncio
import collections
import hashlib
import io
import json
import logging
import math
import os
import re
import struct
import sys
import tarfile
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from domovoi.container_guard.model import DECODER_CONTAINER_PREFIX, DECODER_SLOTS
from domovoi.friend_houses import tor_runtime
from domovoi.friend_houses.tor_runtime import DockerFailed, _parse_created
from domovoi.plugins_runtime.manifest import (
    ISOLATED_DEBIAN_ALLOWLIST,
    SEMVER_RE,
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
# A running core looks at the image's age again this often.
RECHECK_IMAGE_SEC = 24 * 3600.0
BUILD_TIMEOUT_SEC = 900.0
APP_DIR = "/opt/domovoi-isolated"
_HERE = Path(__file__).resolve().parent
RUNNER_NAME = "domovoi_isolated_runner.py"
RUNNER_SOURCE = _HERE / "isolated_runner.py"
# The core's image helper (decode_pixels) and the core's own image_safety,
# which it uses: both the core's code, never the plugin's.
HELPER_NAME = "domovoi_isolated.py"
HELPER_SOURCE = _HERE / "isolated_helper.py"
SAFETY_NAME = "domovoi_image_safety.py"
SAFETY_SOURCE = _HERE.parent / "image_safety.py"
# Fixed and unprivileged (nobody): owns nothing in the image, and the
# container mounts nothing.
CONTAINER_USER = "65534:65534"

# ── per-friend containers (D2) ──
MAX_LIVE_PER_PLUGIN = DECODER_SLOTS    # the container guard expects these slot names
MAX_LIVE_TOTAL = 8
IDLE_SEC = 120.0
REAP_EVERY_SEC = 15.0
# Each container's caps; MAX_LIVE_TOTAL of them is 1 GB and 2 CPUs at most.
RUN_MEMORY = "128m"
RUN_PIDS = "16"
RUN_CPUS = "0.25"

# ── the IPC (the runner keeps the same numbers) ──
PAYLOAD_MAX = 256 * 1024
META_MAX = 1024
REPLY_MAX = 64 * 1024
REASON_MAX = 120
DECODE_TIMEOUT_SEC = 5.0
IMAGE_TIMEOUT_SEC = 10.0
KIND_DECODE, KIND_IMAGE = 0, 1
STATUS_OK, STATUS_REFUSED, STATUS_PIXELS = 0, 1, 2
# Keys a request's meta may not carry: the friend never enters the container.
META_RESERVED = frozenset({"house_id", "house", "friend", "friend_id", "house_key"})

# ── the reply checks (D9) ──
DEPTH_MAX = 16
NODES_MAX = 4096
KEYS_MAX = 256
KEY_MAX = 128
# Strings stay short: canonical values carry moves and names, not files
# (no bytes or base64 beyond this pinned size, D1).
STRING_MAX = 2048
INT_MAX = 2**53 - 1          # exact in every browser's JavaScript
_BAD_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff]")

# ── images (D1, D10; the helper keeps MAX_SIDE equal) ──
IMAGE_MAX_SIDE = 768
# The core's PNG of the largest image fits under this (768 x 768 RGBA is
# 2.25 MiB raw); anything over it is refused.
IMAGE_OUT_MAX = 2560 * 1024
REPLY_FRAME_MAX = 5 + REPLY_MAX
PIXELS_FRAME_MAX = 5 + 5 + IMAGE_MAX_SIDE * IMAGE_MAX_SIDE * 4

# ── faults (D3) ──
BACKOFF_FIRST_SEC = 1.0
BACKOFF_MAX_SEC = 60.0
ITEM_FAULTS_MAX = 2
BREAKER_FAULTS = 3
BREAKER_WINDOW_SEC = 3600.0
BREAKER_PAUSE_SEC = 3600.0
BREAKER_PAUSE_MAX_SEC = 24 * 3600.0
BUILD_RETRY_SEC = 300.0
GATE_RETRY_SEC = 30.0

DOCKERFILE_TEMPLATE = """\
# A plugin's decode-only container, written by the Domovoi core
# (domovoi/plugins_runtime/isolated.py), never by the plugin. Python and the
# packages below come from Debian; the plugin adds only its decoder
# package's .py files under {app}/, which are copied and byte-compiled,
# never run, here.
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


def container_name(slug: str, slot: int) -> str:
    """A decoder container's name: one slot per live friend container."""
    return f"{DECODER_CONTAINER_PREFIX}{slug}-{slot}"


def container_names(slug: str) -> list[str]:
    return [container_name(slug, i) for i in range(MAX_LIVE_PER_PLUGIN)]


def image_tag(slug: str) -> str:
    return f"{DECODER_CONTAINER_PREFIX}{slug}:latest"


async def _docker(*args: str, timeout: float = 60.0, stdin: bytes | None = None,
                  env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """The docker CLI (a seam the tests replace). ``env`` replaces the
    environment of the CLI process (the build sets DOCKER_BUILDKIT=0)."""
    if env is None:
        return await tor_runtime._docker(*args, timeout=timeout, stdin=stdin)
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":  # pragma: no cover
        import subprocess

        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = await asyncio.create_subprocess_exec(
        "docker", *args, env=env,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise DockerFailed(f"docker {args[0]} took longer than {int(timeout)} s") from None
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, out.decode(errors="replace"), err.decode(errors="replace")


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
    """The build context: the core's Dockerfile, runner, image helper and
    ``image_safety``, ``decoder.json`` and the decoder package's ``.py``
    files. The same files give the same bytes (fixed times, owners and
    order)."""
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
        add(f"app/{HELPER_NAME}", HELPER_SOURCE.read_bytes())
        add(f"app/{SAFETY_NAME}", SAFETY_SOURCE.read_bytes())
        add("app/decoder.json", spec)
        for rel, path in files:
            add(f"app/{rel}", path.read_bytes())
    return buf.getvalue()


def build_sha(context: bytes) -> str:
    return hashlib.sha256(context).hexdigest()


# The build's caps. Docker 29 (what the installers pin) runs ``docker
# build`` through BuildKit (buildx), which ACCEPTS ``--memory`` and the CPU
# flags for compatibility and IGNORES them: the build would run uncapped.
# The classic builder (``DOCKER_BUILDKIT=0``, deprecated but still shipped)
# runs each RUN step in a container with exactly these limits, so the
# decoder image is built with it; :func:`builder_caps_honoured` checks
# first that this Docker still has it, and the build is refused (never run
# uncapped) when it doesn't. The Dockerfile uses nothing BuildKit-only.
BUILD_MEMORY = "1g"
BUILD_CAPS = (
    "--memory", BUILD_MEMORY, "--memory-swap", BUILD_MEMORY,
    # Low priority: a quarter of the default CPU weight, and at most one CPU.
    "--cpu-shares", "256", "--cpu-period", "100000", "--cpu-quota", "100000",
)
BUILD_ENV_EXTRA = {"DOCKER_BUILDKIT": "0"}
UNCAPPED_BUILD = ("this Docker can't cap the memory of an image build (its classic builder is "
                  "gone), so the decoder image isn't built")
_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}\Z")


def build_args(slug: str, sha: str, version: str) -> list[str]:
    """``docker build`` with the context piped in, for the classic builder
    (:data:`BUILD_ENV_EXTRA`) with :data:`BUILD_CAPS`. ``--pull --no-cache``:
    a rebuild runs apt again, so Debian's fixes get in. The manifest's
    strings go in only as validated label values (D11)."""
    if not _SLUG_RE.match(slug) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise IsolatedBuildError("a bad slug or build hash")
    if not SEMVER_RE.match(version):
        version = "unknown"
    return ["build", "--pull", "--no-cache", *BUILD_CAPS, "-t", image_tag(slug),
            "--label", f"{BUILD_LABEL}={sha}",
            "--label", f"org.domovoi.plugin={slug}",
            "--label", f"org.domovoi.plugin-version={version}", "-"]


def build_env() -> dict[str, str]:
    return {**docker_env(), **BUILD_ENV_EXTRA}


async def builder_caps_honoured() -> bool:
    """Whether ``docker build`` (classic builder) still takes ``--memory``.
    BuildKit hides the flag from its help because it ignores it, so its
    presence there means the classic builder answered."""
    rc, out, err = await _docker("build", "--help", timeout=30.0, env=build_env())
    text = out + err
    return rc == 0 and "--memory-swap" in text and "docker buildx build" not in text


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


async def _recorded_image(slug: str) -> str | None:
    """The image ID the container guard has on record for ``slug``."""
    try:
        from domovoi.container_guard.engine import decoder_image_record

        return await decoder_image_record(slug)
    except Exception as e:  # noqa: BLE001 — no record means a rebuild, never a run
        log.warning("plugin %s: the container guard's record of its decoder image can't be read: %s", slug, e)
        return None


async def ensure_image(slug: str, install_dir: Path, manifest: PluginManifest) -> str:
    """The image ID to run, built or rebuilt when needed, and recorded with
    the container guard before anything runs it (D11). An image the guard
    has no record of is rebuilt, not trusted. The image a rebuild replaces
    is removed (the core built it; nothing else uses it)."""
    context = context_tar(install_dir, manifest)
    want = build_sha(context)
    info = await image_info(slug)
    reason = "it isn't built yet" if info is None else info.stale(want)
    if reason is None:
        assert info is not None
        if await _recorded_image(slug) == info.image_id:
            return info.image_id
        reason = "the container guard has no record of it"
    if not await builder_caps_honoured():
        raise DockerFailed(UNCAPPED_BUILD)
    log.info("plugin %s: building its decoder image: %s", slug, reason)
    rc, _, err = await _docker(*build_args(slug, want, manifest.version),
                               timeout=BUILD_TIMEOUT_SEC, stdin=context, env=build_env())
    if rc != 0:
        last = err.strip().splitlines()[-1:] if err.strip() else []
        raise DockerFailed(f"building the {slug} decoder failed" + (": " + last[0][:200] if last else ""))
    built = await image_info(slug)
    if built is None:
        raise DockerFailed(f"the {slug} decoder was built but can't be found")
    try:
        from domovoi.container_guard.engine import note_decoder_image

        await note_decoder_image(slug, built.image_id)
    except Exception as e:  # noqa: BLE001 — unrecorded: not run
        raise DockerFailed(f"the {slug} decoder image couldn't be recorded with the container "
                           f"guard, so it isn't run ({type(e).__name__})") from None
    if info is not None and info.image_id != built.image_id:
        await _docker("image", "rm", info.image_id, timeout=60.0)
    return built.image_id


def run_args(slug: str, slot: int, image_id: str) -> list[str]:
    """The ``docker run`` arguments for one friend's container (a pure
    function so tests pin each one). Nothing names the friend."""
    return [
        "run", "-i", "--rm", "--name", container_name(slug, slot),
        "--network", "none",
        "--user", CONTAINER_USER,
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--memory", RUN_MEMORY, "--memory-swap", RUN_MEMORY,
        "--pids-limit", RUN_PIDS, "--cpus", RUN_CPUS,
        "--ulimit", "nofile=64:64", "--ulimit", "core=0",
        "--ipc", "none",
        "--log-driver", "none",
        "--label", "org.domovoi.owner=core", "--label", f"org.domovoi.role={ROLE}",
        "--label", f"org.domovoi.plugin={slug}",
        image_id,
    ]


def docker_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _DOCKER_ENV if k in os.environ}
    env.setdefault("LANG", "C.UTF-8")
    return env


async def remove_container(name: str) -> None:
    rc, _, err = await _docker("rm", "-f", name, timeout=40.0)
    if rc != 0 and "No such container" not in err:
        raise DockerFailed(f"removing the decoder container {name} failed: " + err.strip()[:200])


async def remove_image(slug: str) -> None:
    rc, _, err = await _docker("image", "rm", image_tag(slug), timeout=60.0)
    if rc != 0 and "No such image" not in err:
        log.warning("plugin %s: its decoder image wasn't removed: %s", slug, err.strip()[:200])


# ─── when the image may be built (D11) ────────────────────────────────────

#: Set by the Friend Houses plugin-mail service: ``await check(slug)`` is
#: True when at least one friend has "Apps with this friend" on. Until it is
#: set, no ``friend_houses`` plugin's decoder is built.
FRIEND_APPS_CHECK: Callable[[str], Awaitable[bool]] | None = None

Gate = Callable[[str, PluginManifest], Awaitable[str | None]]


async def default_gate(slug: str, manifest: PluginManifest) -> str | None:
    """Why the image may not be built now, or None. Only loaded plugins
    have a pool, so the admin has confirmed the install."""
    from domovoi import egress

    if egress.policy() == "never":
        return 'the internet answer is "never" (the build downloads Debian packages)'
    if manifest.permissions.get("friend_houses"):
        from domovoi.config import settings

        if sys.platform == "win32":
            return "Friend Houses runs on the server's Linux side"
        if not getattr(settings, "friend_houses_enabled", False):
            return "Friend Houses is off"
        check = FRIEND_APPS_CHECK
        if check is None or not await check(slug):
            return 'no friend has "Apps with this friend" on'
    return None


# ─── the reply checks (D9) ────────────────────────────────────────────────

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
    if len(text) > 17:
        raise ValueError("an integer out of range")
    value = int(text)
    if abs(value) > INT_MAX:
        raise ValueError("an integer out of range")
    return value


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len(pairs) > KEYS_MAX:
        raise ValueError(f"an object with more than {KEYS_MAX} keys")
    out: dict[str, Any] = {}
    for k, v in pairs:
        if len(k) > KEY_MAX:
            raise ValueError(f"a key longer than {KEY_MAX}")
        if k in out:
            raise ValueError("a duplicate key")
        out[k] = v
    return out


def _walk(value: Any) -> None:
    """Total nodes, string lengths and characters, without recursion."""
    stack, nodes = [value], 0
    while stack:
        v = stack.pop()
        nodes += 1
        if nodes > NODES_MAX:
            raise ValueError(f"more than {NODES_MAX} values")
        if isinstance(v, str):
            if len(v) > STRING_MAX:
                raise ValueError(f"a string longer than {STRING_MAX}")
            if _BAD_CHARS.search(v):
                raise ValueError("a NUL, control character or lone surrogate")
        elif isinstance(v, dict):
            for k, item in v.items():
                if _BAD_CHARS.search(k):
                    raise ValueError("a NUL, control character or lone surrogate in a key")
                stack.append(item)
        elif isinstance(v, list):
            stack.extend(v)


def canonical(data: bytes) -> tuple[Any, bytes]:
    """``(value, canonical bytes)`` from a decoder's JSON reply, or
    ValueError. The value is parsed from the core's own re-serialization,
    so nothing of the decoder's bytes or objects survives."""
    if len(data) > REPLY_MAX:
        raise ValueError("the reply is too large")
    text = data.decode("utf-8")  # strict: UnicodeDecodeError is a ValueError
    if not _depth_ok(text):
        raise ValueError(f"the reply is nested deeper than {DEPTH_MAX}")
    try:
        value = json.loads(text, parse_constant=_no_constant, parse_float=_finite,
                           parse_int=_bounded_int, object_pairs_hook=_object)
        _walk(value)
        out = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    except (RecursionError, MemoryError):
        raise ValueError("the reply can't be read safely") from None
    except UnicodeEncodeError:
        raise ValueError("a lone surrogate") from None
    if len(out) > REPLY_MAX:
        raise ValueError("the reply is too large")
    return json.loads(out), out


# ─── the plugin's schema, checked in process (D9) ─────────────────────────
#
# A small, strict subset of JSON Schema: every keyword is enforced, and a
# keyword outside it is an error at registration (never silently ignored).

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
_SCHEMA_KEYS = frozenset({"type", "properties", "required", "additionalProperties", "items",
                          "minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum",
                          "enum", "anyOf", "description"})


class SchemaError(ValueError):
    """A schema the core can't enforce."""


def compile_schema(schema: Any, _depth: int = 0) -> dict[str, Any]:
    """``schema`` checked: known keywords only, sane values. Returns it."""
    if _depth > DEPTH_MAX or not isinstance(schema, dict):
        raise SchemaError("a schema is an object, at most 16 deep")
    unknown = set(schema) - _SCHEMA_KEYS
    if unknown:
        raise SchemaError(f"unsupported schema keywords {sorted(unknown)}")
    t = schema.get("type")
    if t is not None and t not in (*_TYPES, "integer", "number"):
        raise SchemaError(f"unknown type {t!r}")
    if "anyOf" not in schema and "enum" not in schema and t is None:
        raise SchemaError("every schema names a type, an enum or anyOf")
    for key in ("minItems", "maxItems", "minLength", "maxLength"):
        if key in schema and not (isinstance(schema[key], int) and schema[key] >= 0):
            raise SchemaError(f"{key} must be a whole number")
    for key in ("minimum", "maximum"):
        if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], (int, float))):
            raise SchemaError(f"{key} must be a number")
    if "enum" in schema and not (isinstance(schema["enum"], list) and schema["enum"]):
        raise SchemaError("enum must be a non-empty list")
    if "properties" in schema:
        if not isinstance(schema["properties"], dict):
            raise SchemaError("properties must be an object")
        for sub in schema["properties"].values():
            compile_schema(sub, _depth + 1)
    if "required" in schema and not (isinstance(schema["required"], list)
                                     and all(isinstance(k, str) for k in schema["required"])):
        raise SchemaError("required must be a list of names")
    extra = schema.get("additionalProperties", False)
    if not isinstance(extra, bool):
        compile_schema(extra, _depth + 1)
    if "items" in schema:
        compile_schema(schema["items"], _depth + 1)
    if "anyOf" in schema:
        if not (isinstance(schema["anyOf"], list) and schema["anyOf"]):
            raise SchemaError("anyOf must be a non-empty list")
        for sub in schema["anyOf"]:
            compile_schema(sub, _depth + 1)
    return schema


def schema_problem(value: Any, schema: dict[str, Any], where: str = "$") -> str | None:
    """Why ``value`` doesn't match ``schema``, or None. Objects allow no
    property the schema doesn't name unless ``additionalProperties`` says
    so."""
    if "anyOf" in schema:
        if all(schema_problem(value, s, where) for s in schema["anyOf"]):
            return f"{where} matches none of anyOf"
    if "enum" in schema and not any(value == e and type(value) is type(e) for e in schema["enum"]):
        return f"{where} is not one of the allowed values"
    t = schema.get("type")
    if t == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{where} is not an integer"
    elif t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{where} is not a number"
    elif t is not None and not isinstance(value, _TYPES[t]):
        return f"{where} is not {t}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return f"{where} is below its minimum"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{where} is above its maximum"
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", STRING_MAX):
            return f"{where} has the wrong length"
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", NODES_MAX):
            return f"{where} has the wrong number of items"
        if "items" in schema:
            for i, item in enumerate(value):
                p = schema_problem(item, schema["items"], f"{where}[{i}]")
                if p:
                    return p
    if isinstance(value, dict) and t == "object":
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                return f"{where} lacks {name!r}"
        extra = schema.get("additionalProperties", False)
        for k, item in value.items():
            sub = props.get(k)
            if sub is None:
                if extra is False:
                    return f"{where} has a property the schema doesn't name"
                if extra is True:
                    continue
                sub = extra
            p = schema_problem(item, sub, f"{where}.{k}"[:200])
            if p:
                return p
    return None


# ─── images: pixels in, the core's PNG out (D1, D10) ─────────────────────

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
#: Every image the core serves: a fixed type, no sniffing, nothing active.
IMAGE_HEADERS = {
    "Content-Type": "image/png",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Content-Disposition": "inline",
    "Cache-Control": "private, no-store",
}


def check_pixels(data: bytes) -> tuple[int, int, int, bytes]:
    """``(width, height, channels, pixels)`` from an image reply, or
    ValueError: 1..:data:`IMAGE_MAX_SIDE` a side, RGB or RGBA, and exactly
    ``width * height * channels`` bytes."""
    if len(data) < 5:
        raise ValueError("a short image header")
    width, height, channels = struct.unpack(">HHB", data[:5])
    if not (1 <= width <= IMAGE_MAX_SIDE and 1 <= height <= IMAGE_MAX_SIDE):
        raise ValueError(f"{width}x{height} is outside 1..{IMAGE_MAX_SIDE} a side")
    if channels not in (3, 4):
        raise ValueError("not RGB or RGBA")
    pixels = data[5:]
    if len(pixels) != width * height * channels:
        raise ValueError("the pixel count doesn't match the size")
    return width, height, channels, bytes(pixels)


def _chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)


def encode_png(width: int, height: int, channels: int, pixels: bytes) -> bytes:
    """A plain PNG of the pixels, written by the core: a header, the pixels,
    the end. No other chunk exists to carry anything."""
    stride = width * channels
    if len(pixels) != stride * height or channels not in (3, 4):
        raise ValueError("the pixels don't match the size")
    view = memoryview(pixels)
    raw = bytearray()
    for y in range(height):
        raw += b"\x00"
        raw += view[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6 if channels == 4 else 2, 0, 0, 0)
    out = PNG_MAGIC + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _chunk(b"IEND", b"")
    if len(out) > IMAGE_OUT_MAX:
        raise ValueError("the image is too large")
    return out


def image_response(png: bytes) -> Any:
    """A response for a PNG the core encoded (:data:`IMAGE_HEADERS`)."""
    from starlette.responses import Response

    if not png.startswith(PNG_MAGIC):
        raise ValueError("not one of the core's PNGs")
    return Response(content=png, headers=dict(IMAGE_HEADERS))


class ImageChunks:
    """An image sent in pieces, put back together by the core as opaque
    bytes (D10): the plugin never holds them. :meth:`add` returns the whole
    image once its last piece is in; the core then makes one image request
    (:meth:`DecoderPool.decode_image`). Bounded: pieces per image, bytes per
    image, images in progress per friend and in all, and an expiry."""

    def __init__(self, *, max_bytes: int = PAYLOAD_MAX, max_parts: int = 32, per_friend: int = 4,
                 total: int = 32, ttl_sec: float = 600.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_bytes, self.max_parts, self.per_friend, self.total = max_bytes, max_parts, per_friend, total
        self.ttl_sec, self._clock = ttl_sec, clock
        self._sets: dict[tuple[str, str], tuple[float, int, dict[int, bytes]]] = {}

    def _expire(self) -> None:
        now = self._clock()
        for k in [k for k, (born, _, _) in self._sets.items() if now - born > self.ttl_sec]:
            del self._sets[k]

    def discard(self, friend: str, key: str) -> None:
        self._sets.pop((friend, key), None)

    def add(self, friend: str, key: str, index: int, count: int, data: bytes) -> bytes | None:
        self._expire()
        k = (friend, str(key)[:64])
        if not (1 <= count <= self.max_parts and 0 <= index < count) or not isinstance(data, bytes):
            self.discard(*k)
            raise ValueError("a bad piece")
        entry = self._sets.get(k)
        if entry is None:
            if sum(1 for f, _ in self._sets if f == friend) >= self.per_friend or len(self._sets) >= self.total:
                raise ValueError("too many images in progress")
            entry = (self._clock(), count, {})
            self._sets[k] = entry
        _, want, parts = entry
        if want != count or (index in parts and parts[index] != data):
            self.discard(*k)
            raise ValueError("pieces that disagree")
        parts[index] = data
        if sum(len(p) for p in parts.values()) > self.max_bytes:
            self.discard(*k)
            raise ValueError("the image is too large")
        if len(parts) < count:
            return None
        del self._sets[k]
        return b"".join(parts[i] for i in range(count))


# ─── one friend's container ───────────────────────────────────────────────

@dataclass(frozen=True)
class DecodeResult:
    """One item's outcome, in words safe to log (never the decoder's bytes).

    ``outcome``: ``ok``; ``refused`` (the decoder or a check said no:
    final); ``fault`` (the container hung, crashed or broke the stream on
    this item: counted against it and its friend); ``dropped`` (the item
    faulted :data:`ITEM_FAULTS_MAX` times: final); ``unavailable`` (not
    tried: building, restarting, busy or the friend is paused)."""

    ok: bool
    value: Any = None
    canonical: bytes = b""
    reason: str = ""
    outcome: str = "ok"
    # An image request's answer: the core's own PNG.
    image: bytes | None = None
    media_type: str = ""

    @property
    def retry(self) -> bool:
        return self.outcome in ("fault", "unavailable")


def _unavailable(why: str) -> DecodeResult:
    return DecodeResult(False, reason=why, outcome="unavailable")


def _refused(why: str) -> DecodeResult:
    return DecodeResult(False, reason=why[:REASON_MAX * 2], outcome="refused")


_SAFE_REASON = re.compile(r"[^A-Za-z0-9 .,:;_()'/-]")


class _Broken(Exception):
    """The stream can't be trusted any more: kill the container."""


Spawn = Callable[[list[str]], Awaitable[Any]]


async def _spawn_docker(argv: list[str]) -> Any:
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":  # pragma: no cover
        import subprocess

        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return await asyncio.create_subprocess_exec(
        "docker", *argv, env=docker_env(),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        # The decoder's stderr goes nowhere (D9).
        stderr=asyncio.subprocess.DEVNULL, **kwargs,
    )


class _Instance:
    """One friend's container: one request at a time; a byte it writes
    unasked kills it."""

    def __init__(self, pool: "DecoderPool", friend: str, slot: int) -> None:
        self.pool, self.friend, self.slot = pool, friend, slot
        self.name = container_name(pool.slug, slot)
        self.proc: Any = None
        self.lock = asyncio.Lock()
        self.last_used = pool._clock()
        self.dead: str | None = None
        self.gone = False            # evicted or reaped: its items aren't its fault
        self._reader: asyncio.Task | None = None
        self._waiter: asyncio.Future | None = None
        self._frame_max = REPLY_FRAME_MAX
        self._rid = 0

    async def start(self, image_id: str) -> None:
        try:
            await remove_container(self.name)
        except DockerFailed as e:
            raise OSError(str(e)) from None
        self.proc = await self.pool._spawn(run_args(self.pool.slug, self.slot, image_id))
        self._reader = asyncio.ensure_future(self._read())

    def _die(self, why: str) -> None:
        if self.dead is None:
            self.dead = why
            asyncio.ensure_future(self.pool._unasked(self))

    async def _read(self) -> None:
        out = self.proc.stdout
        try:
            while True:
                first = await out.readexactly(1)
                w = self._waiter
                if w is None or w.done():
                    self._die("the decoder wrote without being asked")
                    return
                (size,) = struct.unpack(">I", first + await out.readexactly(3))
                if size < 5 or size > self._frame_max:
                    w.set_exception(_Broken("the decoder's reply has a bad size"))
                    return
                body = await out.readexactly(size)
                if not w.done():
                    w.set_result(body)
        except (asyncio.IncompleteReadError, ConnectionError, ValueError, OSError):
            w = self._waiter
            if w is not None and not w.done():
                w.set_exception(_Broken("the decoder stopped"))
            elif self.dead is None:
                self._die("the decoder stopped")

    async def _exchange(self, frame: bytes, waiter: asyncio.Future) -> bytes:
        self.proc.stdin.write(frame)
        await self.proc.stdin.drain()
        return await waiter

    async def request(self, kind: int, meta: bytes, payload: bytes, timeout: float) -> tuple[int, bytes]:
        """``(status, data)``; TimeoutError or :class:`_Broken` otherwise."""
        if self.dead is not None:
            raise _Broken(self.dead)
        self._rid = (self._rid + 1) & 0xFFFFFFFF
        rid = self._rid
        self._frame_max = PIXELS_FRAME_MAX if kind == KIND_IMAGE else REPLY_FRAME_MAX
        body = struct.pack(">IBH", rid, kind, len(meta)) + meta + payload
        waiter = asyncio.get_running_loop().create_future()
        self._waiter = waiter
        try:
            reply = await asyncio.wait_for(self._exchange(struct.pack(">I", len(body)) + body, waiter), timeout)
        finally:
            self._waiter = None
        got, status = struct.unpack(">IB", reply[:5])
        if got != rid:
            raise _Broken("the decoder answered another request")
        allowed = (STATUS_REFUSED, STATUS_PIXELS) if kind == KIND_IMAGE else (STATUS_OK, STATUS_REFUSED)
        if status not in allowed:
            raise _Broken("the decoder's reply has a bad status")
        return status, reply[5:]

    async def kill(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        proc, self.proc = self.proc, None
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
            await remove_container(self.name)
        except (DockerFailed, OSError) as e:
            log.warning("plugin %s: %s", self.pool.slug, e)


@dataclass
class _Friend:
    """What the pool remembers about one friend (D3)."""

    backoff: float = BACKOFF_FIRST_SEC
    retry_at: float = 0.0
    faults: collections.deque = field(default_factory=collections.deque)
    paused_until: float = 0.0
    pause: float = BREAKER_PAUSE_SEC


#: Told when a friend is paused for a plugin: ``listener(slug, friend, why)``.
#: The Friend Houses plugin-mail service adds the admin's notice here.
PAUSE_LISTENERS: list[Callable[[str, str, str], Any]] = []

_LIVE: list[_Instance] = []


class DecoderPool:
    """One plugin's decoder: its image, and one container per friend."""

    def __init__(
        self,
        slug: str,
        *,
        install_dir: Path,
        manifest: PluginManifest | None,
        spawn: Spawn | None = None,
        prepare: Callable[[], Awaitable[str]] | None = None,
        gate: Gate | None = None,
        timeout: float = DECODE_TIMEOUT_SEC,
        image_timeout: float = IMAGE_TIMEOUT_SEC,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.slug = slug
        self.install_dir = install_dir
        self.manifest = manifest
        self._spawn = spawn or _spawn_docker
        self._prepare = prepare or self._prepare_image
        self._gate = gate or default_gate
        self.timeout = timeout
        self.image_timeout = image_timeout
        self._clock = clock
        self.image_id: str | None = None
        self.image_state = "none"        # none | waiting | building | ready | failed
        self.problem: str | None = None
        self._prepared_at = 0.0
        self._build_retry_at = 0.0
        self._build_task: asyncio.Task | None = None
        self._reaper: asyncio.Task | None = None
        self.instances: dict[str, _Instance] = {}
        self.friends: dict[str, _Friend] = {}
        self.item_faults: collections.OrderedDict[str, int] = collections.OrderedDict()
        self.schema: dict[str, Any] | None = None
        self.restarts = 0
        self._lock = asyncio.Lock()
        self._stopped = False

    # ── the image ──
    async def _prepare_image(self) -> str:
        assert self.manifest is not None
        return await ensure_image(self.slug, self.install_dir, self.manifest)

    async def prepare(self) -> None:
        """Build (when the gate allows and it's needed) and note the image."""
        self._build_retry_at = self._clock() + BUILD_RETRY_SEC
        # A recheck of a running image (its age) that can't rebuild keeps
        # the image it has: the problem is shown, items still decode.
        running = self.image_id is not None
        try:
            why = await self._gate(self.slug, self.manifest) if self.manifest is not None else None
            if why:
                self.image_state = "ready" if running else "waiting"
                self.problem = why
                self._build_retry_at = self._clock() + GATE_RETRY_SEC
                return
            if not running:
                self.image_state = "building"
            image_id = await self._prepare()
        except (DockerFailed, IsolatedBuildError, OSError) as e:
            self.image_state = "ready" if running else "failed"
            self.problem = str(e)[:300]
            log.warning("plugin %s: its decoder couldn't be set up: %s", self.slug, e)
            return
        changed = running and image_id != self.image_id
        self.image_id = image_id
        self.image_state, self.problem = "ready", None
        self._prepared_at = self._clock()
        if changed:
            # Idle containers start again on the new image when next needed.
            for inst in [i for i in self.instances.values() if not i.lock.locked()]:
                inst.gone = True
                await self._drop(inst)

    def _prepare_soon(self) -> None:
        if self._stopped or (self._build_task is not None and not self._build_task.done()):
            return
        if self.image_state != "none" and self._clock() < self._build_retry_at:
            return
        if self.image_id is None:
            self.image_state = "building"
        self._build_task = asyncio.ensure_future(self.prepare())

    def set_schema(self, schema: Any) -> None:
        self.schema = compile_schema(schema)

    # ── friends and faults (D3) ──
    def _friend(self, friend: str) -> _Friend:
        st = self.friends.get(friend)
        if st is None:
            st = self.friends[friend] = _Friend()
        return st

    def paused(self, friend: str) -> bool:
        st = self.friends.get(friend)
        return st is not None and self._clock() < st.paused_until

    def resume(self, friend: str) -> None:
        """An admin let the friend's items through again."""
        st = self.friends.get(friend)
        if st is not None:
            st.paused_until, st.retry_at, st.backoff = 0.0, 0.0, BACKOFF_FIRST_SEC
            st.faults.clear()

    async def _fault(self, inst: _Instance, item_key: str | None, why: str) -> DecodeResult:
        await self._drop(inst)
        now = self._clock()
        st = self._friend(inst.friend)
        st.retry_at = now + st.backoff
        st.backoff = min(st.backoff * 2, BACKOFF_MAX_SEC)
        self.restarts += 1
        st.faults.append(now)
        while st.faults and now - st.faults[0] > BREAKER_WINDOW_SEC:
            st.faults.popleft()
        if len(st.faults) >= BREAKER_FAULTS:
            note = (f"{len(st.faults)} decoder faults within an hour; this friend's items "
                    f"are paused for {int(st.pause / 60)} min")
            st.paused_until = now + st.pause
            st.pause = min(st.pause * 2, BREAKER_PAUSE_MAX_SEC)
            st.faults.clear()
            log.warning("plugin %s: a friend's items are paused: %s", self.slug, note)
            for listener in list(PAUSE_LISTENERS):
                try:
                    listener(self.slug, inst.friend, note)
                except Exception as e:  # noqa: BLE001 — a listener never stops the pool
                    log.warning("plugin %s: a pause listener failed: %s", self.slug, e)
        if item_key is not None:
            self.item_faults[item_key] = self.item_faults.get(item_key, 0) + 1
            self.item_faults.move_to_end(item_key)
            while len(self.item_faults) > 4096:
                self.item_faults.popitem(last=False)
        return DecodeResult(False, reason=why, outcome="fault")

    # ── containers (D2) ──
    async def _drop(self, inst: _Instance) -> None:
        if self.instances.get(inst.friend) is inst:
            del self.instances[inst.friend]
        if inst in _LIVE:
            _LIVE.remove(inst)
        await inst.kill()

    async def _unasked(self, inst: _Instance) -> None:
        """The container wrote between requests: a fault of that friend's."""
        async with inst.lock:
            if not inst.gone and self.instances.get(inst.friend) is inst:
                await self._fault(inst, None, inst.dead or "the decoder broke its stream")

    async def _make_room(self) -> int | None:
        """A free slot, evicting the least recently used idle container
        (this plugin's for a slot, anyone's for the total), or None."""
        used = {i.slot for i in self.instances.values()}
        if len(used) >= MAX_LIVE_PER_PLUGIN:
            idle = [i for i in self.instances.values() if not i.lock.locked()]
            if not idle:
                return None
            victim = min(idle, key=lambda i: i.last_used)
            victim.gone = True
            await self._drop(victim)
            used.discard(victim.slot)
        while len(_LIVE) >= MAX_LIVE_TOTAL:
            idle = [i for i in _LIVE if not i.lock.locked()]
            if not idle:
                return None
            victim = min(idle, key=lambda i: i.last_used)
            victim.gone = True
            await victim.pool._drop(victim)
            if victim.pool is self:
                used.discard(victim.slot)
        return next(s for s in range(MAX_LIVE_PER_PLUGIN) if s not in used)

    async def _instance_for(self, friend: str) -> _Instance | DecodeResult:
        async with self._lock:
            if self._stopped:
                return _unavailable("the plugin's decoder is stopped")
            inst = self.instances.get(friend)
            if inst is not None and inst.dead is None:
                return inst
            if inst is not None:
                await self._drop(inst)
            st = self._friend(friend)
            if self._clock() < st.retry_at:
                return _unavailable(f"the decoder restarts in {st.retry_at - self._clock():.0f} s")
            if self.image_state == "ready" and self._clock() - self._prepared_at >= RECHECK_IMAGE_SEC:
                self._prepare_soon()   # the running image stays in use meanwhile
            if self.image_id is None:
                self._prepare_soon()
                return _unavailable(f"the decoder isn't ready ({self.image_state}"
                                    + (f": {self.problem}" if self.problem else "") + ")")
            slot = await self._make_room()
            if slot is None:
                return _unavailable("every decoder container is busy")
            inst = _Instance(self, friend, slot)
            try:
                await inst.start(self.image_id)
            except OSError as e:
                st.retry_at = self._clock() + st.backoff
                st.backoff = min(st.backoff * 2, BACKOFF_MAX_SEC)
                await inst.kill()
                return _unavailable(f"the decoder couldn't start: {e}"[:REASON_MAX * 2])
            self.instances[friend] = inst
            _LIVE.append(inst)
            if self._reaper is None or self._reaper.done():
                self._reaper = asyncio.ensure_future(self._reap_loop())
            return inst

    async def reap_idle(self) -> int:
        """Stop the containers idle for :data:`IDLE_SEC`; how many."""
        now, n = self._clock(), 0
        for inst in list(self.instances.values()):
            if not inst.lock.locked() and now - inst.last_used >= IDLE_SEC:
                inst.gone = True
                await self._drop(inst)
                n += 1
        return n

    async def _reap_loop(self) -> None:
        while self.instances and not self._stopped:
            await asyncio.sleep(REAP_EVERY_SEC)
            try:
                await self.reap_idle()
            except Exception as e:  # noqa: BLE001 — keep reaping
                log.warning("plugin %s: reaping its decoders failed: %s", self.slug, e)

    # ── requests ──
    async def _run(self, friend: str, kind: int, meta: bytes, payload: bytes,
                   item_key: str | None) -> DecodeResult:
        if not isinstance(friend, str) or not 1 <= len(friend) <= 128:
            return _refused("bad friend")
        if self.paused(friend):
            return _unavailable("this friend's items are paused after repeated decoder faults")
        if item_key is not None and self.item_faults.get(item_key, 0) >= ITEM_FAULTS_MAX:
            return DecodeResult(False, reason="the item faulted the decoder too often", outcome="dropped")
        got = await self._instance_for(friend)
        if isinstance(got, DecodeResult):
            return got
        inst = got
        async with inst.lock:
            if inst.gone or inst.proc is None:
                return _unavailable("the decoder was stopped")
            try:
                status, data = await inst.request(
                    kind, meta, payload, self.image_timeout if kind == KIND_IMAGE else self.timeout)
            except asyncio.TimeoutError:
                return await self._fault(inst, item_key, "timed out")
            except _Broken as e:
                return await self._fault(inst, item_key, str(e))
            except (ConnectionError, OSError, asyncio.IncompleteReadError):
                return await self._fault(inst, item_key, "the decoder stopped")
            finally:
                inst.last_used = self._clock()
        st = self._friend(friend)
        st.backoff = BACKOFF_FIRST_SEC
        if status == STATUS_REFUSED:
            text = data[:REASON_MAX].decode("ascii", "replace")
            return _refused("refused by the decoder: " + _SAFE_REASON.sub("?", text))
        if kind == KIND_IMAGE:
            try:
                width, height, channels, pixels = check_pixels(data)
                png = await asyncio.to_thread(encode_png, width, height, channels, pixels)
            except ValueError as e:
                return _refused(f"bad image: {e}")
            info = {"format": "png", "width": width, "height": height, "bytes": len(png)}
            return DecodeResult(True, value={"image": info}, image=png, media_type="image/png")
        try:
            value, out = canonical(data)
        except ValueError as e:
            return _refused(f"bad output: {e}")
        if self.schema is None:
            return _refused("bad output: the plugin registered no schema for its decoder")
        problem = schema_problem(value, self.schema)
        if problem:
            return _refused(f"bad output: {problem}")
        return DecodeResult(True, value=value, canonical=out)

    async def decode(self, friend: str, payload: bytes, meta: dict[str, Any] | None = None,
                     *, item_key: str | None = None) -> DecodeResult:
        """Decode one item from ``friend`` (the core's own name for it,
        never sent to the container). Never raises."""
        if not isinstance(payload, (bytes, bytearray)) or len(payload) > PAYLOAD_MAX:
            return _refused("the item is too large")
        if meta and set(meta) & META_RESERVED:
            return _refused("meta may not name the friend")
        try:
            meta_bytes = json.dumps(meta or {}, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()
        except (TypeError, ValueError):
            return _refused("bad meta")
        if len(meta_bytes) > META_MAX:
            return _refused("the meta is too large")
        return await self._run(friend, KIND_DECODE, meta_bytes, bytes(payload), item_key)

    async def decode_image(self, friend: str, data: bytes, *, item_key: str | None = None) -> DecodeResult:
        """Pixels from ``friend``'s image bytes, as the core's own PNG."""
        if not isinstance(data, (bytes, bytearray)) or not 0 < len(data) <= PAYLOAD_MAX:
            return _refused("the image is too large")
        return await self._run(friend, KIND_IMAGE, b"", bytes(data), item_key)

    async def stop(self) -> None:
        self._stopped = True
        for task in (self._build_task, self._reaper):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        async with self._lock:
            for inst in list(self.instances.values()):
                inst.gone = True
                await self._drop(inst)

    def status(self) -> dict[str, Any]:
        return {"slug": self.slug, "image_state": self.image_state, "problem": self.problem,
                "image_id": self.image_id, "live": len(self.instances), "restarts": self.restarts,
                "paused_friends": sum(1 for f in self.friends if self.paused(f))}


# ─── the plugins' decoders ────────────────────────────────────────────────

POOLS: dict[str, DecoderPool] = {}


def start_for(slug: str, install_dir: Path, manifest: PluginManifest) -> DecoderPool | None:
    """The plugin loaded: its pool, empty (the image is built and the
    containers started on first use, through the gate). None for a plugin
    without ``[isolated]``. Never raises."""
    if manifest.isolated is None:
        return None
    if slug in POOLS:
        log.warning("plugin %s: its decoder was still registered; replacing it", slug)
    pool = DecoderPool(slug, install_dir=install_dir, manifest=manifest)
    POOLS[slug] = pool
    return pool


async def stop_for(slug: str) -> None:
    """The plugin unloaded: stop and remove its containers. Never raises."""
    pool = POOLS.pop(slug, None)
    if pool is None:
        return
    try:
        await pool.stop()
    except Exception as e:  # noqa: BLE001 — teardown isolation
        log.warning("plugin %s: stopping its decoder failed: %s", slug, e)


async def forget(slug: str) -> None:
    """The plugin was uninstalled: its containers and image go."""
    await stop_for(slug)
    for name in container_names(slug):
        try:
            await remove_container(name)
        except (DockerFailed, OSError) as e:
            log.warning("plugin %s: %s", slug, e)
    try:
        await remove_image(slug)
    except (DockerFailed, OSError) as e:
        log.warning("plugin %s: its decoder image wasn't removed: %s", slug, e)


def set_schema(slug: str, schema: Any) -> None:
    """The plugin's schema for its decoder's answers (SchemaError, or
    KeyError when it has no decoder)."""
    POOLS[slug].set_schema(schema)


async def decode(slug: str, friend: str, payload: bytes, meta: dict[str, Any] | None = None,
                 *, item_key: str | None = None) -> DecodeResult:
    pool = POOLS.get(slug)
    if pool is None:
        return _unavailable("the plugin has no decoder")
    return await pool.decode(friend, payload, meta, item_key=item_key)


async def decode_image(slug: str, friend: str, data: bytes, *, item_key: str | None = None) -> DecodeResult:
    pool = POOLS.get(slug)
    if pool is None:
        return _unavailable("the plugin has no decoder")
    return await pool.decode_image(friend, data, item_key=item_key)
