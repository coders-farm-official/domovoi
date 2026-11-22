"""Docker images for the kit: ``docker save`` of each pinned image.

Compose pins every image by digest (``postgres:16.15-trixie@sha256:…``),
and an offline install must find the image by that digest after
``docker load``, or compose would try to pull it. What survives a save and
a load was measured on the pinned Docker Engine (29.8.1, 2026-11-22;
design risk 2, docs/OFFLINE_KIT.md "Images"):

* With the **containerd image store** (Docker 29's default on a new
  install, which provision.sh makes), an image saved **under a name**
  (``postgres:16.15-trixie``) loads back with its index digest: compose's
  ``tag@digest`` reference finds it and ``--pull=never`` runs. The archive
  is the registry's own index and the platform's blobs (OCI layout).
* Saved by its digest alone (``postgres@sha256:…``) it loads as a nameless
  image whose ID is the digest, and the digest reference does **not**
  find it.
* ``docker save --platform linux/amd64`` narrows the archive to that
  platform's manifest, so its digest is the platform manifest's, not the
  index digest compose pins: also not found. The builder saves without
  ``--platform``: the archive keeps the registry's index with the blobs
  of every platform this server has pulled (amd64; arm64 too when the kit
  is for a Mac, pulled first with ``--platform linux/arm64``).
* With the **legacy store** (overlay2 without the containerd snapshotter:
  an install upgraded from an older Docker) the digest is lost either way:
  the archive is Docker's own format with no index, and even a containerd
  store that loads it gets a new ID.

So the builder saves each image under its compose tag, from a server
whose Docker uses the containerd store, and checks the archive's
``index.json`` names the pinned digest before keeping it. A server on the
legacy store is refused with that reason (:func:`store_problem`).

Archives: ``images/<repo with / as _>_<tag>.tar``, one per image, holding
amd64 (and arm64 when the kit is for a Mac too). The MPD
image is this server's own build of ``domovoi/Dockerfile.mpd`` (pinned
base, packages from Debian at build time): saved under
``MPD_IMAGE_TAG``, not pinned, and marked so in kit.json.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Callable

from . import host
from .pins import ImagePin

Run = Callable[..., subprocess.CompletedProcess]
TIMEOUT_SEC = 1800


class ImageError(RuntimeError):
    pass


def _nice() -> list[str]:
    return ["nice", "-n", "19"] if shutil.which("nice") else []


def docker_info(run: Run = subprocess.run) -> dict | None:
    try:
        r = run(["docker", "info", "--format", "{{json .}}"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def store_problem(run: Run = subprocess.run) -> str | None:
    """Why this server's Docker can't save images for a kit, or None."""
    info = docker_info(run)
    if info is None:
        return "Docker isn't answering on this server, and the kit's database images come from it."
    status = json.dumps(info.get("DriverStatus") or [])
    if "io.containerd.snapshotter" not in status:
        return ("This server's Docker uses its older image store, which loses the images' pins "
                "when it saves them, so a kit made here couldn't install offline. Docker's "
                "containerd image store fixes it (docs/OFFLINE_KIT.md, \"Images\").")
    return None


def archive_name(repo: str, tag: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", f"{repo.replace('/', '_')}_{tag}") + ".tar"


def _arch_key(arches: tuple[str, ...]) -> str:
    return "-".join(sorted(set(arches)))


def archive_path(arches: tuple[str, ...], repo: str, tag: str) -> Path:
    return host.cache_dir("images", _arch_key(arches)) / archive_name(repo, tag)


def kit_path(repo: str, tag: str) -> str:
    return f"images/{archive_name(repo, tag)}"


def _digest_ref(pin: ImagePin) -> str:
    return f"{pin.repo}@sha256:{pin.digest}"


def have(pin: ImagePin, run: Run = subprocess.run) -> bool:
    r = run(["docker", "image", "inspect", _digest_ref(pin)], capture_output=True, text=True, timeout=60)
    return r.returncode == 0


def archive_digest(path: Path) -> tuple[str | None, str | None]:
    """(digest, name) the archive's index.json gives its image."""
    try:
        with tarfile.open(path) as tf:
            member = tf.extractfile("index.json")
            doc = json.loads(member.read(1 << 20)) if member else {}
    except (OSError, tarfile.TarError, ValueError, KeyError):
        return None, None
    try:
        m = doc["manifests"][0]
        name = (m.get("annotations") or {}).get("io.containerd.image.name")
        return str(m["digest"]).removeprefix("sha256:"), name
    except (KeyError, IndexError, TypeError):
        return None, None


def archive_ok(path: Path, pin: ImagePin) -> bool:
    digest, name = archive_digest(path)
    return digest == pin.digest and bool(name) and name.endswith(f"/{pin.repo}:{pin.tag}".replace("//", "/"))


def save(pin: ImagePin, arches: tuple[str, ...] = ("amd64",), run: Run = subprocess.run) -> Path:
    """The image's archive in the kit cache, holding ``arches``: pulled by
    digest when this server lacks it (through the outbound gate), tagged
    with its compose tag, saved under that name, checked."""
    from domovoi import egress

    out = archive_path(arches, pin.repo, pin.tag)
    if out.is_file() and archive_ok(out, pin):
        return out
    for arch in sorted(set(arches)):
        if arch == "amd64" and have(pin, run):
            continue
        egress.require_internet(f"the {pin.repo} image for the offline kit")
        r = run([*_nice(), "docker", "pull", "--platform", f"linux/{arch}", pin.ref],
                capture_output=True, text=True, timeout=TIMEOUT_SEC)
        if r.returncode != 0:
            raise ImageError(f"docker pull {pin.repo} failed: {(r.stderr or '')[-300:].strip()}")
    r = run(["docker", "tag", _digest_ref(pin), f"{pin.repo}:{pin.tag}"], capture_output=True, text=True,
            timeout=60)
    if r.returncode != 0:
        raise ImageError(f"docker tag {pin.repo} failed: {(r.stderr or '')[-300:].strip()}")
    part = out.with_name(f".{out.name}.part")
    part.unlink(missing_ok=True)
    try:
        r = run([*_nice(), "docker", "save", "-o", str(part), f"{pin.repo}:{pin.tag}"],
                capture_output=True, text=True, timeout=TIMEOUT_SEC)
        if r.returncode != 0:
            raise ImageError(f"docker save {pin.repo} failed: {(r.stderr or '')[-300:].strip()}")
        if not archive_ok(part, pin):
            raise ImageError(f"the saved {pin.repo} image doesn't carry its pinned digest, so an "
                             "offline install couldn't find it")
        part.replace(out)
    finally:
        part.unlink(missing_ok=True)
    return out


def save_local(tag: str, run: Run = subprocess.run) -> Path:
    """This server's own image (the MPD image, amd64) saved under its name."""
    repo, _, t = tag.partition(":")
    out = archive_path(("amd64",), repo, t or "latest")
    part = out.with_name(f".{out.name}.part")
    part.unlink(missing_ok=True)
    try:
        r = run([*_nice(), "docker", "save", "-o", str(part), tag],
                capture_output=True, text=True, timeout=TIMEOUT_SEC)
        if r.returncode != 0:
            raise ImageError(f"docker save {tag} failed: {(r.stderr or '')[-300:].strip()}")
        part.replace(out)
    finally:
        part.unlink(missing_ok=True)
    return out


def local_image_id(tag: str, run: Run = subprocess.run) -> str | None:
    r = run(["docker", "image", "inspect", "--format", "{{.Id}}", tag], capture_output=True, text=True,
            timeout=60)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


#: Sizes of the pinned images' amd64 archives from the containerd store,
#: for the card before the first save (measured 2026-11-22; the build uses
#: the real archive).
ESTIMATES = {"postgres": 166_104_064, "flyway/flyway": 282_803_200, "letta/letta": 377_332_224,
             "mpd": 60_000_000}


def estimate(repo: str) -> int:
    return ESTIMATES.get(repo, ESTIMATES["mpd"] if "mpd" in repo else 300_000_000)
