"""Run the Friend Houses listener in a container of its own.

The listener (:mod:`domovoi.friend_houses.listener`) reads whatever other
houses send through Tor. Until audit R6 FH-12 it ran as a plain process
under the core's own user: a defect in it could have read anything that
user can, and reached the network. Now the core runs it in a container
built for that one job:

* the image is ``domovoi/Dockerfile.fh-listener``, built by the core from a
  context it makes itself (:func:`context_tar`): that Dockerfile and a copy
  of only the modules the listener runs, on the same digest-pinned Debian
  base as the Tor image, with Python and cryptography from Debian. The
  container guard trusts exactly the image ID the core built
  (:func:`domovoi.container_guard.engine.note_listener_image`);
* the container has **no network** (``--network none``), a read-only root,
  no capabilities and no new privileges, caps on memory, processes and
  CPU, and no logs kept by Docker (an event line can hold a message);
* it sees three things from the host: the house keys and the listener's
  files (friend list, hello, open friend files), both read-only, and the
  folder its sockets live in, which the Tor container also mounts
  (``<tor run folder>/listen``, not the folder with Tor's SOCKS socket).
  With away chat (1.5.0) on, a fourth: the house's away Noise key,
  read-only; the listener's files then hold ``phones.json`` too. While
  Friend Houses is off and away chat on, the house keys aren't mounted and
  only ``away.sock`` is served;
* it runs as the core's own user so Tor (the same user) can use its
  sockets, but that user owns nothing inside the container;
* it is started with ``docker run -i --rm`` by the core, which talks to it
  over stdin and stdout exactly as it did to the process, and it ends when
  the core does: the listener stops when its stdin closes, and the core
  removes any container left behind before starting a new one.

The image is rebuilt when the Dockerfile or any of those modules change
(an update of Domovoi) and when it is older than :data:`MAX_AGE_DAYS`.
"""

from __future__ import annotations

import hashlib
import io
import logging
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from domovoi.friend_houses.tor_runtime import DockerFailed, _docker, _parse_created

log = logging.getLogger(__name__)

CONTAINER = "domovoi-fh-listener"
IMAGE_TAG = "domovoi-fh-listener:latest"
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # .../domovoi
DOCKERFILE = _PACKAGE_ROOT / "Dockerfile.fh-listener"
BUILD_LABEL = "org.domovoi.build-sha256"
MAX_AGE_DAYS = 30
BUILD_TIMEOUT_SEC = 900.0

# Everything the listener imports, and nothing else of Domovoi. A test
# (test_friend_houses_listener_container.py) imports the listener with only
# these on the path, so a new import can't be forgotten here.
SOURCES = (
    "__init__.py",
    "_ed25519.py",
    "away/__init__.py",
    "away/listener.py",
    "away/wire.py",
    "crypto_backend.py",
    "friend_houses/__init__.py",
    "friend_houses/budget.py",
    "friend_houses/envelope.py",
    "friend_houses/friend_file.py",
    "friend_houses/identity.py",
    "friend_houses/listener.py",
    "friend_houses/noise.py",
    "friend_houses/pairing.py",
    "friend_houses/plugin_wire.py",
    "friend_houses/session.py",
)

# Paths inside the container.
KEYS_MOUNT = "/etc/fh/house-keys.json"
AWAY_KEY_MOUNT = "/etc/fh/away-noise.key"
FILES_MOUNT = "/var/lib/fh-listener"
SOCKETS_MOUNT = "/run/fh-listen"


def context_tar() -> bytes:
    """The build context: the Dockerfile and ``app/domovoi/<SOURCES>``.
    Byte-for-byte the same for the same files (fixed times, owners and
    order), so its hash says whether the image is current."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        def add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))

        add("Dockerfile", DOCKERFILE.read_bytes())
        for rel in SOURCES:
            add(f"app/domovoi/{rel}", (_PACKAGE_ROOT / rel).read_bytes())
    return buf.getvalue()


def build_sha(context: bytes | None = None) -> str:
    return hashlib.sha256(context_tar() if context is None else context).hexdigest()


@dataclass(frozen=True)
class ImageInfo:
    image_id: str
    build_sha: str | None
    created: datetime | None

    def stale(self, *, now: datetime | None = None, want: str | None = None) -> str | None:
        """Why the image should be rebuilt, or None."""
        if self.build_sha != (want or build_sha()):
            return "the listener's code changed"
        if self.created is None:
            return "its age is unknown"
        age = (now or datetime.now(timezone.utc)) - self.created
        if age.days >= MAX_AGE_DAYS:
            return f"it is {age.days} days old"
        return None


async def image_info() -> ImageInfo | None:
    rc, out, _ = await _docker(
        "image", "inspect", "-f",
        "{{.Id}}|{{index .Config.Labels \"" + BUILD_LABEL + "\"}}|{{.Created}}",
        IMAGE_TAG, timeout=15.0,
    )
    if rc != 0 or "|" not in out:
        return None
    image_id, sha, created = (out.strip().split("|") + ["", ""])[:3]
    return ImageInfo(image_id, sha or None, _parse_created(created))


def build_args(sha: str) -> list[str]:
    """The ``docker build`` arguments (the context is piped in as a tar).
    ``--no-cache``: a rebuild must run apt again (as the Tor image's)."""
    return ["build", "--pull", "--no-cache", "-t", IMAGE_TAG, "--label", f"{BUILD_LABEL}={sha}", "-"]


async def build_image() -> str:
    context = context_tar()
    rc, _, err = await _docker(*build_args(build_sha(context)), timeout=BUILD_TIMEOUT_SEC, stdin=context)
    if rc != 0:
        last = err.strip().splitlines()[-1:] if err.strip() else []
        raise DockerFailed("building the Friend Houses listener failed"
                           + (": " + last[0][:200] if last else ""))
    info = await image_info()
    if info is None:
        raise DockerFailed("the Friend Houses listener was built but can't be found")
    log.info("built the Friend Houses listener image %s", info.image_id[:19])
    return info.image_id


async def ensure_image() -> str:
    """The image ID to run, building or rebuilding it when needed."""
    info = await image_info()
    reason = "it isn't built yet" if info is None else info.stale()
    if reason is None:
        assert info is not None
        return info.image_id
    log.info("building the Friend Houses listener image: %s", reason)
    image_id = await build_image()
    try:
        from domovoi.container_guard.engine import note_listener_image

        await note_listener_image(image_id)
    except Exception as e:  # noqa: BLE001 — the guard is told best-effort
        log.warning("the listener image wasn't recorded for the container guard: %s", e)
    return image_id


def run_args(*, image_id: str, keys: Path | None, files: Path, sockets: Path, uid: int, gid: int,
             away_key: Path | None = None) -> list[str]:
    """The ``docker run`` arguments, listener arguments included. A pure
    function so tests pin every one of them. ``keys`` None: Friend Houses
    is off, so only away chat's socket is served (``away_key`` given)."""
    if keys is None and away_key is None:
        raise ValueError("the listener needs the house keys, the away key, or both")
    mounts: list[str] = []
    listener: list[str] = ["--folder", FILES_MOUNT]
    if keys is not None:
        mounts += ["-v", f"{keys}:{KEYS_MOUNT}:ro"]
        listener += ["--keys", KEYS_MOUNT,
                     "--socket", f"{SOCKETS_MOUNT}/main.sock",
                     "--pairing-socket", f"{SOCKETS_MOUNT}/pairing.sock"]
    if away_key is not None:
        mounts += ["-v", f"{away_key}:{AWAY_KEY_MOUNT}:ro"]
        listener += ["--away-socket", f"{SOCKETS_MOUNT}/away.sock", "--away-key", AWAY_KEY_MOUNT]
    return [
        "run", "-i", "--rm", "--name", CONTAINER,
        "--network", "none",
        "--user", f"{uid}:{gid}",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--memory", "128m", "--pids-limit", "32", "--cpus", "0.5",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=1m",
        "--log-driver", "none",
        "--label", "org.domovoi.owner=core", "--label", "org.domovoi.role=friend-houses-listener",
        *mounts,
        "-v", f"{files}:{FILES_MOUNT}:ro",
        "-v", f"{sockets}:{SOCKETS_MOUNT}",
        image_id,
        *listener,
    ]


async def remove() -> None:
    """Remove the container, if one was left behind (the core killed
    hard, say): there is only ever one listener."""
    rc, _, err = await _docker("rm", "-f", CONTAINER, timeout=40.0)
    if rc != 0 and "No such container" not in err:
        raise DockerFailed("removing the old Friend Houses listener failed: " + err.strip()[:200])


async def prepare() -> str:
    """Before each start: the current image (built if needed), and no
    container left over. Returns the image ID to run."""
    image_id = await ensure_image()
    await remove()
    return image_id
