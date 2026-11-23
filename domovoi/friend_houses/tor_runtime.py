"""Build the Tor image and run its container, through the docker CLI.

Friend Houses ("serverless messaging and friend system between households")
runs C tor in a container the core builds and starts itself, the way it
builds the music player image (domovoi/mpd_provisioner.py). Nothing is
pulled from a third-party tor image: the image is ``domovoi/Dockerfile.tor``
on a digest-pinned Debian base, and the container guard trusts exactly the
image ID the core built (:func:`domovoi.container_guard.engine.note_tor_image`).

The container:

* is named ``domovoi-tor`` (reserved: no plugin may declare it);
* runs as the core's own user, so both sides can use the unix sockets and
  key files in the folder they share (``<config dir>/friend-houses/tor``);
* has a read-only root, no capabilities, no new privileges, and caps on
  memory, processes and CPU;
* sits on its own Docker network, ``domovoi-fh``, with a fixed subnet. The
  installers add firewall rules that stop that subnet reaching the host and
  the home network (spike S1 found a container on a plain Docker network
  could open connections to the house's own ports: threat-model finding 22);
* restarts itself only after a crash (``on-failure``), never after a reboot:
  the core starts it at boot when it's wanted, so "never" can't be undone
  by Docker bringing it back (finding 26).

The image is rebuilt when ``Dockerfile.tor`` changes and when it is older
than :data:`MAX_AGE_DAYS`, so Debian's security fixes for tor get in
(finding 24).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

CONTAINER = "domovoi-tor"
IMAGE_TAG = "domovoi-tor:latest"
NETWORK = "domovoi-fh"
# A fixed, unusual subnet so the installers' firewall rules can name it.
NETWORK_SUBNET = "172.31.254.0/24"
# ...and a fixed bridge name, so they can also stop it reaching the host
# itself (that traffic goes through INPUT, not Docker's DOCKER-USER chain).
NETWORK_BRIDGE = "domovoi-fh0"
DOCKERFILE = Path(__file__).resolve().parent.parent / "Dockerfile.tor"
DOCKERFILE_LABEL = "org.domovoi.dockerfile-sha256"
MAX_AGE_DAYS = 30
BUILD_TIMEOUT_SEC = 900.0


class DockerFailed(RuntimeError):
    """A docker command failed; the message is safe to show an admin."""


async def _docker(*args: str, timeout: float = 60.0, stdin: bytes | None = None) -> tuple[int, str, str]:
    kwargs: dict = {}
    if sys.platform == "win32":  # pragma: no cover
        import subprocess

        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = await asyncio.create_subprocess_exec(
        "docker", *args,
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


def dockerfile_sha() -> str:
    return hashlib.sha256(DOCKERFILE.read_bytes()).hexdigest()


@dataclass(frozen=True)
class ImageInfo:
    image_id: str
    dockerfile_sha: str | None
    created: datetime | None

    def stale(self, *, now: datetime | None = None) -> str | None:
        """Why the image should be rebuilt, or None."""
        if self.dockerfile_sha != dockerfile_sha():
            return "Dockerfile.tor changed"
        if self.created is None:
            return "its age is unknown"
        age = (now or datetime.now(timezone.utc)) - self.created
        if age.days >= MAX_AGE_DAYS:
            return f"it is {age.days} days old"
        return None


_GO_TIME = re.compile(r"^(\S+) (\S+) ([+-]\d{2})(\d{2})(?: \S+)?$")
_FRACTION = re.compile(r"^(\d+)(.*)$")


def _parse_created(text: str) -> datetime | None:
    """An image's ``{{.Created}}``, always **timezone-aware**, or None.

    Docker prints RFC 3339 with nanoseconds, in UTC (``…45.123456789Z``)
    on most hosts, but some print local time with an offset
    (``…45.123456789-05:00``), and Go's own form
    (``2026-11-17 14:25:30.123456789 -0500 EST``) turns up too. Only the
    fraction's own digits are trimmed to microseconds: this used to count
    the offset's digits into the fraction too, which turned an offset time
    into a naive one, and :meth:`ImageInfo.stale` then failed every
    reconcile with "can't subtract offset-naive and offset-aware
    datetimes" (seen on a house, 2026-11-22). A time that still names no
    zone is taken as UTC, so the age check can never raise."""
    text = text.strip()
    if not text:
        return None
    go = _GO_TIME.match(text)
    if go:
        text = f"{go.group(1)}T{go.group(2)}{go.group(3)}:{go.group(4)}"
    if "." in text:
        head, tail = text.split(".", 1)
        m = _FRACTION.match(tail)
        if m:
            text = f"{head}.{m.group(1)[:6]}{m.group(2)}"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


async def image_info() -> ImageInfo | None:
    rc, out, _ = await _docker(
        "image", "inspect", "-f",
        "{{.Id}}|{{index .Config.Labels \"" + DOCKERFILE_LABEL + "\"}}|{{.Created}}",
        IMAGE_TAG, timeout=15.0,
    )
    if rc != 0 or "|" not in out:
        return None
    image_id, sha, created = (out.strip().split("|") + ["", ""])[:3]
    return ImageInfo(image_id, sha or None, _parse_created(created))


async def build_image() -> str:
    """Build the image from Dockerfile.tor (no build context: the file is
    piped in). Returns the new image ID. Needs the internet (apt)."""
    sha = dockerfile_sha()
    rc, _, err = await _docker(
        # --no-cache: the monthly rebuild must run apt again, or tor stays
        # at its first build's version for ever (audit R6 FH-13).
        "build", "--pull", "--no-cache", "-t", IMAGE_TAG, "--label", f"{DOCKERFILE_LABEL}={sha}", "-",
        timeout=BUILD_TIMEOUT_SEC, stdin=DOCKERFILE.read_bytes(),
    )
    if rc != 0:
        raise DockerFailed("building the Tor image failed: " + err.strip().splitlines()[-1:][0]
                           if err.strip() else "building the Tor image failed")
    info = await image_info()
    if info is None:
        raise DockerFailed("the Tor image was built but can't be found")
    log.info("built the Friend Houses Tor image %s", info.image_id[:19])
    return info.image_id


async def ensure_image() -> str:
    """The image ID to run, building or rebuilding it when needed."""
    info = await image_info()
    reason = "it isn't built yet" if info is None else info.stale()
    if reason is None:
        assert info is not None
        return info.image_id
    log.info("building the Friend Houses Tor image: %s", reason)
    image_id = await build_image()
    try:
        from domovoi.container_guard.engine import note_tor_image

        await note_tor_image(image_id)
    except Exception as e:  # noqa: BLE001 — the guard is told best-effort
        log.warning("the Tor image wasn't recorded for the container guard: %s", e)
    return image_id


async def ensure_network() -> None:
    rc, _, _ = await _docker("network", "inspect", NETWORK, timeout=15.0)
    if rc == 0:
        return
    rc, _, err = await _docker(
        "network", "create", "--driver", "bridge", "--subnet", NETWORK_SUBNET,
        "-o", f"com.docker.network.bridge.name={NETWORK_BRIDGE}",
        "--label", "org.domovoi.owner=core", NETWORK, timeout=30.0,
    )
    if rc != 0:
        raise DockerFailed("creating the Friend Houses network failed: " + err.strip()[:200])


def run_args(*, image_id: str, data: Path, run: Path, uid: int, gid: int) -> list[str]:
    """The ``docker run`` arguments. A pure function so tests pin them."""
    return [
        "run", "-d", "--name", CONTAINER,
        "--network", NETWORK,
        "--user", f"{uid}:{gid}",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--memory", "256m", "--pids-limit", "128", "--cpus", "0.5",
        "--restart", "on-failure:5",
        "--label", "org.domovoi.owner=core", "--label", "org.domovoi.role=friend-houses-tor",
        "-v", f"{data}:/var/lib/fh", "-v", f"{run}:/run/fh",
        image_id,
    ]


async def state() -> str:
    """``running``, ``stopped`` (exists, not running) or ``absent``."""
    rc, out, _ = await _docker("inspect", "-f", "{{.State.Running}}", CONTAINER, timeout=15.0)
    if rc != 0:
        return "absent"
    return "running" if out.strip() == "true" else "stopped"


async def start(*, data: Path, run: Path) -> None:
    """Start the daemon on the current image, recreating the container when
    it was made from another one."""
    image_id = await ensure_image()
    await ensure_network()
    rc, out, _ = await _docker("inspect", "-f", "{{.Image}}", CONTAINER, timeout=15.0)
    if rc == 0 and out.strip() != image_id:
        await remove()
        rc = 1
    if rc == 0:
        rc, _, err = await _docker("start", CONTAINER, timeout=60.0)
    else:
        uid, gid = (os.getuid(), os.getgid()) if hasattr(os, "getuid") else (10001, 10001)
        rc, _, err = await _docker(*run_args(image_id=image_id, data=data, run=run, uid=uid, gid=gid),
                                   timeout=60.0)
    if rc != 0:
        raise DockerFailed("starting the Friend Houses connection failed: " + err.strip()[:200])


async def stop() -> None:
    rc, _, err = await _docker("stop", "-t", "10", CONTAINER, timeout=40.0)
    if rc != 0 and "No such container" not in err:
        raise DockerFailed("stopping the Friend Houses connection failed: " + err.strip()[:200])


async def restart() -> None:
    rc, _, err = await _docker("restart", "-t", "10", CONTAINER, timeout=60.0)
    if rc != 0:
        raise DockerFailed("restarting the Friend Houses connection failed: " + err.strip()[:200])


async def reload() -> None:
    """SIGHUP: tor re-reads its config and key files (no control port)."""
    rc, _, err = await _docker("kill", "-s", "HUP", CONTAINER, timeout=15.0)
    if rc != 0:
        raise DockerFailed("reloading the Friend Houses connection failed: " + err.strip()[:200])


async def remove() -> None:
    await _docker("rm", "-f", CONTAINER, timeout=40.0)
